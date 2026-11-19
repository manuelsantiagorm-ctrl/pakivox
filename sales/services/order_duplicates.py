from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.utils import timezone

from payments.models import CustomerCreditApplication, Payment
from sales.models import Order, OrderResolutionEvent


# Solo estados SIN pago confirmado disparan la alerta de posible
# duplicado. Un pedido ya pagado, en picking, empacado o enviado
# ya es una operación real en curso -- no puede ser un duplicado
# accidental, así que no debe interrumpir al vendedor.
ACTIVE_ORDER_STATUSES = (
    "draft",
    "pending_payment",
    "partial_paid",
)

# Estatus que se consideran "cerrados" para efectos del historial
# tranquilo (no disparan alerta, solo se listan como referencia).
CLOSED_ORDER_STATUSES = (
    "delivered",
)

DEFAULT_CANCELLED_WINDOW_HOURS = 72
DEFAULT_MAX_RESULTS = 8
DEFAULT_HISTORY_MAX_RESULTS = 5


def _configured_int(name, default, minimum, maximum):
    try:
        value = int(getattr(settings, name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


def duplicate_cancelled_window_hours():
    return _configured_int(
        "PAKIVOX_DUPLICATE_ORDER_CANCELLED_HOURS",
        DEFAULT_CANCELLED_WINDOW_HOURS,
        1,
        24 * 90,
    )


def duplicate_max_results():
    return _configured_int(
        "PAKIVOX_DUPLICATE_ORDER_MAX_RESULTS",
        DEFAULT_MAX_RESULTS,
        3,
        20,
    )


def order_history_max_results():
    return _configured_int(
        "PAKIVOX_ORDER_HISTORY_MAX_RESULTS",
        DEFAULT_HISTORY_MAX_RESULTS,
        3,
        20,
    )


@dataclass(frozen=True)
class DuplicateDetection:
    customer_id: int
    active_orders: tuple
    cancelled_recent_orders: tuple
    cancelled_window_hours: int
    total_candidates: int = 0

    @property
    def candidates(self):
        return self.active_orders + self.cancelled_recent_orders

    @property
    def has_candidates(self):
        return bool(self.candidates)

    @property
    def candidate_ids(self):
        return tuple(item["id"] for item in self.candidates)


def _money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"))


def detect_related_orders(*, company, customer, exclude_order_id=None):
    """Detecta posibles pedidos relacionados sin depender del nombre del cliente.

    Solo se consideran "activos" para esta alerta los pedidos SIN pago
    confirmado (draft, pending_payment, partial_paid) creados dentro de una
    ventana reciente (misma ventana configurable que los cancelados): son
    los únicos que realmente pueden ser un duplicado accidental o un pedido
    a medias que vale la pena retomar. Pedidos sin pagar más viejos que la
    ventana, o ya pagados/en proceso de surtido/envío, son ruido histórico
    o una operación real en curso, y no se incluyen aquí.
    """
    if company is None or customer is None:
        raise ValidationError(
            "Empresa y cliente son obligatorios para revisar pedidos relacionados."
        )
    if customer.company_id != company.id:
        raise ValidationError("El cliente no pertenece a la empresa actual.")

    hours = duplicate_cancelled_window_hours()
    cutoff = timezone.now() - timedelta(hours=hours)

    base_filter = Q(status__in=ACTIVE_ORDER_STATUSES, created_at__gte=cutoff) | Q(
        status="cancelled", created_at__gte=cutoff
    )

    full_queryset = Order.objects.filter(company=company, customer=customer).filter(
        base_filter
    )
    if exclude_order_id:
        full_queryset = full_queryset.exclude(pk=exclude_order_id)

    total_candidates = full_queryset.count()

    queryset = full_queryset.select_related("created_by").order_by("-created_at", "-id")

    rows = list(queryset[: duplicate_max_results()])
    order_ids = [row.id for row in rows]

    approved_totals = {
        row["order_id"]: _money(row["approved_total"])
        for row in (
            Payment.objects.filter(
                company=company,
                order_id__in=order_ids,
                status="approved",
            )
            .values("order_id")
            .annotate(approved_total=Sum("amount"))
        )
    }
    applied_credit_totals = {
        row["order_id"]: _money(row["credit_total"])
        for row in (
            CustomerCreditApplication.objects.filter(
                company=company,
                order_id__in=order_ids,
                status=CustomerCreditApplication.STATUS_APPLIED,
            )
            .values("order_id")
            .annotate(credit_total=Sum("amount"))
        )
    }

    active = []
    cancelled = []
    for order in rows:
        approved_total = approved_totals.get(order.id, Decimal("0.00"))
        customer_credit_total = applied_credit_totals.get(order.id, Decimal("0.00"))
        covered_total = _money(approved_total + customer_credit_total)
        total = _money(order.total)
        item = {
            "id": order.id,
            "folio": order.folio,
            "status": order.status,
            "status_label": order.get_status_display(),
            "total": total,
            "amount_paid": _money(order.amount_paid),
            "balance_due": _money(order.balance_due),
            "approved_payment_total": approved_total,
            "customer_credit_total": customer_credit_total,
            "covered_total": covered_total,
            "created_at": order.created_at,
            "created_by_id": order.created_by_id,
            "created_by": order.created_by,
            "recovery_candidate": (
                order.status == "cancelled"
                and total > Decimal("0.00")
                and covered_total >= total
            ),
        }
        if order.status == "cancelled":
            cancelled.append(item)
        else:
            active.append(item)

    return DuplicateDetection(
        customer_id=customer.id,
        active_orders=tuple(active),
        cancelled_recent_orders=tuple(cancelled),
        cancelled_window_hours=hours,
        total_candidates=total_candidates,
    )


def get_order_history(*, company, customer, exclude_order_id=None, limit=None):
    """Pedidos ya cerrados (entregados, o cancelados fuera de la ventana de
    detección de duplicados) para mostrarlos como historial informativo
    cuando el cliente NO tiene nada activo ni sospechoso pendiente.

    A diferencia de detect_related_orders, esto nunca bloquea la creación
    de un pedido nuevo: es solo contexto de referencia para el usuario.
    """
    if company is None or customer is None:
        raise ValidationError("Empresa y cliente son obligatorios para consultar el historial de pedidos.")
    if customer.company_id != company.id:
        raise ValidationError("El cliente no pertenece a la empresa actual.")

    hours = duplicate_cancelled_window_hours()
    cutoff = timezone.now() - timedelta(hours=hours)

    queryset = (
        Order.objects.filter(company=company, customer=customer)
        .filter(
            Q(status__in=CLOSED_ORDER_STATUSES)
            | Q(status="cancelled", created_at__lt=cutoff)
        )
        .order_by("-created_at", "-id")
    )
    if exclude_order_id:
        queryset = queryset.exclude(pk=exclude_order_id)

    rows = list(queryset[: limit or order_history_max_results()])

    return tuple(
        {
            "id": order.id,
            "folio": order.folio,
            "status": order.status,
            "status_label": order.get_status_display(),
            "total": _money(order.total),
            "created_at": order.created_at,
            "created_by_id": order.created_by_id,
            # se agrega para reutilizar el mismo serializador que
            # active_orders / cancelled_recent_orders en la vista
            "recovery_candidate": False,
        }
        for order in rows
    )


def validate_new_order_duplicate_decision(*, company, customer, override=False):
    detection = detect_related_orders(company=company, customer=customer)
    if detection.has_candidates and not override:
        raise ValidationError(
            "Este cliente ya tiene pedidos activos o cancelados recientemente. "
            "Revísalos y confirma explícitamente si esta operación es una venta nueva."
        )
    return detection


def record_duplicate_override(*, order, actor, detection):
    if not detection or not detection.has_candidates:
        return None
    profile = getattr(actor, "profile", None)
    if profile is None or profile.company_id != order.company_id:
        raise ValidationError("El usuario no pertenece a la empresa del pedido.")

    return OrderResolutionEvent.objects.create(
        company=order.company,
        order=order,
        customer=order.customer,
        actor=actor,
        event_type=OrderResolutionEvent.EVENT_DUPLICATE_WARNING_OVERRIDDEN,
        reason_code=OrderResolutionEvent.REASON_DUPLICATE,
        reason_text=(
            "El usuario confirmó que se trata de una venta nueva después de revisar "
            "los pedidos relacionados del mismo cliente."
        ),
        previous_status="",
        new_status=order.status,
        amount_paid_before=order.amount_paid,
        amount_paid_after=order.amount_paid,
        balance_due_before=order.balance_due,
        balance_due_after=order.balance_due,
        message="Advertencia de posible pedido duplicado revisada y sobreescrita.",
        metadata={
            "candidate_order_ids": list(detection.candidate_ids),
            "active_order_ids": [item["id"] for item in detection.active_orders],
            "cancelled_recent_order_ids": [
                item["id"] for item in detection.cancelled_recent_orders
            ],
            "cancelled_window_hours": detection.cancelled_window_hours,
            "source": "create_order",
        },
    )
