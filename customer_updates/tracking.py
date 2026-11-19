"""
Link público de seguimiento: creación, URL y datos que se muestran.

Lo que el comprador ve es deliberadamente poco: estado, montos del pedido,
productos y guías. Nunca teléfonos, correos, costos, márgenes, notas
internas ni la dirección completa.
"""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from .models import OrderTrackingLink, generate_tracking_token

# Un link de un pedido ya cerrado deja de abrir pasado este plazo.
CLOSED_ORDER_LINK_DAYS = 120
MAX_PUBLIC_ITEMS = 200
MAX_TRACKING_EVENTS = 8

STATUS_PUBLIC_LABELS = {
    "draft": "En captura",
    "pending": "Pendiente de pago",
    "pending_payment": "Pendiente de pago",
    "partial_paid": "Anticipo recibido",
    "paid": "Pagado",
    "picking": "En preparación",
    "packed": "Empacado",
    "shipped": "En camino",
    "delivered": "Entregado",
    "cancelled": "Cancelado",
}

CARRIER_STATUS_LABELS = {
    "label_ready": "Guía generada",
    "printed": "Guía generada",
    "ready": "Listo para recolección",
    "shipped": "Entregado a la paquetería",
    "in_transit": "En tránsito",
    "out_for_delivery": "En ruta de entrega",
    "delivered": "Entregado",
    "exception": "Con incidencia",
}


def get_or_create_tracking_link(order):
    """Devuelve el link del pedido; lo crea la primera vez (seguro ante carreras)."""
    link = OrderTrackingLink.objects.filter(order_id=order.pk).first()
    if link is not None:
        return link

    try:
        with transaction.atomic():
            return OrderTrackingLink.objects.create(
                company_id=order.company_id,
                order_id=order.pk,
            )
    except IntegrityError:
        return OrderTrackingLink.objects.get(order_id=order.pk)


def rotate_tracking_link(order):
    """Invalida el link anterior y genera uno nuevo."""
    link = get_or_create_tracking_link(order)
    link.token = generate_tracking_token()
    link.rotated_at = timezone.now()
    link.save(update_fields=["token", "rotated_at"])
    return link


def tracking_path(link):
    return reverse("seguimiento_pedido", args=[link.token])


def tracking_url(link, request=None):
    path = tracking_path(link)
    if request is not None:
        return request.build_absolute_uri(path)
    base = str(getattr(settings, "PAKIVOX_BASE_URL", "https://www.pakivox.com")).rstrip("/")
    return f"{base}{path}"


def link_is_expired(link, order, now=None):
    if order.status not in {"delivered", "cancelled"}:
        return False
    now = now or timezone.now()
    return order.updated_at < now - timedelta(days=CLOSED_ORDER_LINK_DAYS)


def _money(value):
    try:
        return Decimal(value or 0).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")


def build_steps(order):
    status = order.status
    total = _money(order.total)
    balance = _money(order.balance_due)
    paid_states = {"paid", "picking", "packed", "shipped", "delivered"}
    is_paid = status in paid_states or (
        total > 0 and balance <= 0 and status not in {"draft", "cancelled"}
    )

    if status == "cancelled":
        current = None
    elif status == "draft":
        current = "received"
    elif not is_paid:
        current = "payment"
    elif status in {"paid", "picking"}:
        current = "preparing"
    elif status == "packed":
        current = "shipped"
    elif status == "shipped":
        current = "delivered"
    else:
        current = None

    done = {
        "received": status not in {"draft"},
        "payment": is_paid,
        "preparing": status in {"packed", "shipped", "delivered"},
        "shipped": status in {"shipped", "delivered"},
        "delivered": status == "delivered",
    }
    labels = [
        ("received", "Pedido recibido"),
        ("payment", "Pago confirmado"),
        ("preparing", "En preparación"),
        ("shipped", "En camino"),
        ("delivered", "Entregado"),
    ]
    return [
        {"key": key, "label": label, "done": done[key], "current": key == current}
        for key, label in labels
    ]


def _shipments(order):
    from warehouse.models import ShippingOrder

    shipping_orders = list(
        ShippingOrder.objects.filter(company_id=order.company_id, order_id=order.pk)
        .exclude(status=ShippingOrder.STATUS_CANCELLED)
        .only(
            "id",
            "carrier",
            "tracking_number",
            "status",
            "shipped_at",
            "delivered_at",
        )
        .order_by("id")
    )
    if not shipping_orders:
        return []

    carrier_by_shipping = {}
    events_by_carrier = {}
    try:
        from shipping_hub.models import CarrierShipment, ShipmentTrackingEvent

        carrier_rows = list(
            CarrierShipment.objects.filter(
                company_id=order.company_id,
                shipping_order_id__in=[so.pk for so in shipping_orders],
            )
            .exclude(status__in=["cancelled", "failed", "draft"])
            .only(
                "id",
                "shipping_order_id",
                "carrier_name",
                "master_tracking_number",
                "master_tracking_url",
                "status",
            )
            .order_by("shipping_order_id", "-id")
        )
        for row in carrier_rows:
            carrier_by_shipping.setdefault(row.shipping_order_id, row)

        carrier_ids = [row.pk for row in carrier_by_shipping.values()]
        if carrier_ids:
            for event in (
                ShipmentTrackingEvent.objects.filter(
                    company_id=order.company_id,
                    carrier_shipment_id__in=carrier_ids,
                )
                .only(
                    "carrier_shipment_id",
                    "event_name",
                    "description",
                    "location",
                    "occurred_at",
                )
                .order_by("-occurred_at")[: MAX_TRACKING_EVENTS * len(carrier_ids)]
            ):
                bucket = events_by_carrier.setdefault(event.carrier_shipment_id, [])
                if len(bucket) < MAX_TRACKING_EVENTS:
                    bucket.append(event)
    except Exception:  # pragma: no cover - shipping_hub siempre instalado
        carrier_by_shipping = {}

    result = []
    for index, so in enumerate(shipping_orders, start=1):
        carrier = carrier_by_shipping.get(so.pk)
        carrier_name = (getattr(carrier, "carrier_name", "") or so.carrier or "").strip()
        number = (getattr(carrier, "master_tracking_number", "") or so.tracking_number or "").strip()
        url = (getattr(carrier, "master_tracking_url", "") or "").strip()
        if url and not url.lower().startswith(("https://", "http://")):
            url = ""
        status_label = ""
        if carrier is not None:
            status_label = CARRIER_STATUS_LABELS.get(carrier.status, "")
        if not status_label:
            status_label = so.get_status_display()
        result.append(
            {
                "index": index,
                "carrier": carrier_name or "Paquetería por confirmar",
                "tracking_number": number,
                "tracking_url": url,
                "status_label": status_label,
                "shipped_at": so.shipped_at,
                "delivered_at": so.delivered_at,
                "events": events_by_carrier.get(getattr(carrier, "pk", None), []),
            }
        )
    return result


def _items(order):
    from sales.models import OrderItem

    rows = (
        OrderItem.objects.filter(order_id=order.pk)
        .select_related("variant__product")
        .only(
            "quantity",
            "unit_price",
            "total",
            "variant__color",
            "variant__size",
            "variant__product__name",
        )
        .order_by("id")[: MAX_PUBLIC_ITEMS + 1]
    )
    items = []
    for row in rows:
        variant = row.variant
        product = getattr(variant, "product", None)
        details = " · ".join(
            part for part in [getattr(variant, "color", ""), getattr(variant, "size", "")] if part
        )
        items.append(
            {
                "name": getattr(product, "name", "") or "Producto",
                "details": details,
                "quantity": row.quantity,
                "unit_price": row.unit_price,
                "total": row.total,
            }
        )
    truncated = len(items) > MAX_PUBLIC_ITEMS
    return items[:MAX_PUBLIC_ITEMS], truncated


def build_public_context(link):
    order = link.order
    company = order.company
    snapshot = order.shipping_address_snapshot or {}
    destination = ", ".join(
        str(part).strip()
        for part in [snapshot.get("city", ""), snapshot.get("state", "")]
        if part and str(part).strip()
    )
    items, items_truncated = _items(order)
    total = _money(order.total)
    paid = _money(order.amount_paid)
    balance = _money(order.balance_due)
    return {
        "company_name": company.name,
        "order": order,
        "folio": order.folio,
        "status": order.status,
        "status_label": STATUS_PUBLIC_LABELS.get(order.status, order.get_status_display()),
        "is_cancelled": order.status == "cancelled",
        "steps": build_steps(order),
        "total": total,
        "amount_paid": paid,
        "balance_due": balance if balance > 0 else Decimal("0.00"),
        "shipping_cost": _money(order.shipping_cost),
        "discount": _money(order.discount),
        "items": items,
        "items_truncated": items_truncated,
        "shipments": _shipments(order),
        "destination": destination,
        "updated_at": order.updated_at,
    }
