"""
Borradores de WhatsApp por momento del pedido.

El sistema sugiere el mensaje que corresponde al estado actual y deja los
demás a la mano. El vendedor puede editar el texto antes de abrirlo en
WhatsApp; PAKIVOX nunca manda nada por su cuenta en este canal.
"""

from datetime import timedelta
from decimal import Decimal
from urllib.parse import quote

from django.utils import timezone

DRAFT_CONFIRMATION = "confirmacion"
DRAFT_PAYMENT_REMINDER = "recordatorio_pago"
DRAFT_PAYMENT_RECEIVED = "pago_recibido"
DRAFT_SHIPPED = "en_camino"
DRAFT_DELIVERED = "entregado"
DRAFT_TRACKING = "seguimiento"

DRAFT_LABELS = {
    DRAFT_CONFIRMATION: "Pedido registrado",
    DRAFT_PAYMENT_REMINDER: "Recordatorio de pago",
    DRAFT_PAYMENT_RECEIVED: "Pago recibido",
    DRAFT_SHIPPED: "Va en camino",
    DRAFT_DELIVERED: "Entregado",
    DRAFT_TRACKING: "Link de seguimiento",
}

DRAFT_KEYS = tuple(DRAFT_LABELS)

# wa.me acepta textos largos, pero los navegadores móviles truncan URLs
# enormes. 1,500 caracteres de mensaje quedan muy por debajo del límite.
MAX_DRAFT_LENGTH = 1500


def money(value):
    try:
        amount = Decimal(value or 0).quantize(Decimal("0.01"))
    except Exception:
        amount = Decimal("0.00")
    return f"${amount:,.2f}"


def _first_name(order):
    customer = getattr(order, "customer", None)
    name = ""
    if customer is not None:
        name = (
            getattr(customer, "contact_name", "")
            or getattr(customer, "full_name", "")
            or getattr(customer, "business_name", "")
            or ""
        )
    name = str(name).strip()
    return name.split()[0].title() if name else ""


def _greeting(order):
    first = _first_name(order)
    return f"Hola {first}" if first else "Hola"


def _signature(order):
    company = getattr(order, "company", None)
    return f"— {getattr(company, 'name', '') or 'Tu proveedor'}"


def _shipment_lines(shipments):
    lines = []
    for shipment in shipments:
        line = f"• {shipment['carrier']}"
        if shipment.get("tracking_number"):
            line += f" · guía {shipment['tracking_number']}"
        lines.append(line)
        if shipment.get("tracking_url"):
            lines.append(f"  Rastreo: {shipment['tracking_url']}")
    return lines


def build_drafts(order, *, tracking_url, shipments=None, items_count=None):
    """
    Devuelve (drafts, suggested_key).

    drafts: lista de {"key", "label", "body", "suggested"} en el orden en
    que se muestran. Solo aparecen los que tienen sentido para el pedido.
    """
    shipments = shipments or []
    status = order.status
    folio = order.folio
    total = Decimal(order.total or 0)
    paid = Decimal(order.amount_paid or 0)
    balance = Decimal(order.balance_due or 0)
    hello = _greeting(order)
    signature = _signature(order)
    link_line = f"Sigue tu pedido aquí: {tracking_url}"

    drafts = {}

    if status != "cancelled":
        summary = f"Total: {money(total)}"
        if items_count:
            summary = f"{items_count} producto{'s' if items_count != 1 else ''} · {summary}"
        drafts[DRAFT_CONFIRMATION] = "\n".join(
            [
                f"{hello}, ya registramos tu pedido {folio}. ✅",
                "",
                summary,
                "",
                link_line,
                "",
                "Si quieres cambiar algo, respóndeme por aquí.",
                signature,
            ]
        )

    if status not in {"cancelled", "draft"} and balance > 0:
        lines = [f"{hello}, te recuerdo el pago de tu pedido {folio}.", ""]
        if paid > 0:
            lines.append(f"Recibimos {money(paid)}. Queda pendiente {money(balance)}.")
        else:
            lines.append(f"Total a pagar: {money(balance)}.")
        lines += [
            "",
            "En cuanto lo confirmemos, tu pedido pasa a preparación.",
            link_line,
            signature,
        ]
        drafts[DRAFT_PAYMENT_REMINDER] = "\n".join(lines)

    if status not in {"cancelled", "draft"} and paid > 0:
        if balance > 0:
            middle = f"Recibimos tu pago de {money(paid)}. Queda pendiente {money(balance)}."
        else:
            middle = "Tu pago quedó confirmado y tu pedido ya está en preparación. 📦"
        drafts[DRAFT_PAYMENT_RECEIVED] = "\n".join(
            [
                f"{hello}, gracias por tu pago del pedido {folio}.",
                "",
                middle,
                "",
                link_line,
                signature,
            ]
        )

    if status in {"shipped", "delivered"} or shipments:
        lines = [f"{hello}, tu pedido {folio} ya va en camino. 🚚", ""]
        shipment_lines = _shipment_lines(shipments)
        if shipment_lines:
            lines += shipment_lines + [""]
        lines += [link_line, signature]
        drafts[DRAFT_SHIPPED] = "\n".join(lines)

    if status == "delivered":
        drafts[DRAFT_DELIVERED] = "\n".join(
            [
                f"{hello}, tu pedido {folio} aparece como entregado. 🙌",
                "",
                "¿Llegó todo bien? Si necesitas algo más o quieres repetir tu pedido, aquí estoy.",
                signature,
            ]
        )

    drafts[DRAFT_TRACKING] = "\n".join(
        [
            f"{hello}, este es el link para ver tu pedido {folio} en cualquier momento:",
            tracking_url,
            signature,
        ]
    )

    suggested = suggest_draft_key(order, available=drafts.keys())

    ordered = []
    for key in DRAFT_KEYS:
        if key in drafts:
            ordered.append(
                {
                    "key": key,
                    "label": DRAFT_LABELS[key],
                    "body": drafts[key][:MAX_DRAFT_LENGTH],
                    "suggested": key == suggested,
                }
            )
    return ordered, suggested


def suggest_draft_key(order, available):
    available = set(available)
    status = order.status
    balance = Decimal(order.balance_due or 0)

    if status == "delivered":
        preferred = DRAFT_DELIVERED
    elif status == "shipped":
        preferred = DRAFT_SHIPPED
    elif status == "cancelled":
        preferred = DRAFT_TRACKING
    elif status == "draft":
        preferred = DRAFT_CONFIRMATION
    elif balance > 0 and _recently_confirmed(order) and not Decimal(order.amount_paid or 0):
        # Recién capturado y sin pagos: primero se confirma el pedido.
        preferred = DRAFT_CONFIRMATION
    elif balance > 0:
        preferred = DRAFT_PAYMENT_REMINDER
    elif status in {"paid", "picking", "packed"}:
        preferred = DRAFT_PAYMENT_RECEIVED
    else:
        preferred = DRAFT_CONFIRMATION

    if preferred in available:
        return preferred
    for key in DRAFT_KEYS:
        if key in available:
            return key
    return DRAFT_TRACKING


def _recently_confirmed(order, hours=24):
    confirmed_at = getattr(order, "confirmed_at", None)
    if not confirmed_at:
        return False
    return confirmed_at >= timezone.now() - timedelta(hours=hours)


def build_wa_me_url(phone, body):
    return f"https://wa.me/{phone}?text={quote(body or '', safe='')}"
