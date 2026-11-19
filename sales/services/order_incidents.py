from __future__ import annotations

from decimal import Decimal

from django.db.models import (
    BooleanField,
    DecimalField,
    Exists,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce

from accounts.capability_access import user_has_capability
from payments.models import CustomerCredit, Payment, PaymentRefundRequest
from sales.models import Order
from warehouse.models import (
    OrderFulfillment,
    PackingOrder,
    PickingOrder,
    ShippingOrder,
)


MONEY_FIELD = DecimalField(max_digits=12, decimal_places=2)


def _money_subquery(queryset, value_name: str):
    return Subquery(queryset.values(value_name)[:1], output_field=MONEY_FIELD)


def incident_orders_queryset(*, company, user):
    """Proyección server-side de incidencias comerciales/financieras/operativas.

    No crea un segundo estado paralelo. Una incidencia existe mientras los
    estados fuente (Order, Payment, refund, crédito o warehouse) sigan
    requiriendo atención y desaparece cuando el flujo seguro se resuelve.
    """
    if company is None:
        return Order.objects.none()

    approved_amount_qs = (
        Payment.objects.filter(
            company=company,
            order_id=OuterRef("pk"),
            status="approved",
        )
        .values("order_id")
        .annotate(total=Sum("amount"))
    )
    latest_approved_payment = (
        Payment.objects.filter(
            company=company,
            order_id=OuterRef("pk"),
            status="approved",
        )
        .order_by("-created_at", "-pk")
    )
    pending_refund = (
        PaymentRefundRequest.objects.filter(
            company=company,
            order_id=OuterRef("pk"),
            status=PaymentRefundRequest.STATUS_REQUESTED,
        )
        .order_by("-requested_at", "-pk")
    )
    active_credit = CustomerCredit.objects.filter(
        company=company,
        source_order_id=OuterRef("pk"),
        status=CustomerCredit.STATUS_ACTIVE,
        remaining_amount__gt=0,
    ).order_by("-created_at", "-pk")
    active_credit_amount_qs = (
        CustomerCredit.objects.filter(
            company=company,
            source_order_id=OuterRef("pk"),
            status=CustomerCredit.STATUS_ACTIVE,
            remaining_amount__gt=0,
        )
        .values("source_order_id")
        .annotate(total=Sum("remaining_amount"))
    )
    active_picking = PickingOrder.objects.filter(
        company=company,
        order_id=OuterRef("pk"),
    ).exclude(status=PickingOrder.STATUS_CANCELLED)
    active_packing = PackingOrder.objects.filter(
        company=company,
        order_id=OuterRef("pk"),
    ).exclude(status=PackingOrder.STATUS_CANCELLED)
    active_shipping = ShippingOrder.objects.filter(
        company=company,
        order_id=OuterRef("pk"),
    ).exclude(status=ShippingOrder.STATUS_CANCELLED)
    fulfillment_incident = OrderFulfillment.objects.filter(
        company=company,
        order_id=OuterRef("pk"),
        status=OrderFulfillment.STATUS_INCIDENT,
    )

    qs = (
        Order.objects.filter(company=company)
        .select_related("customer", "created_by")
        .annotate(
            cs1f_approved_amount=Coalesce(
                _money_subquery(approved_amount_qs, "total"),
                Value(Decimal("0.00")),
                output_field=MONEY_FIELD,
            ),
            cs1f_approved_payment_id=Subquery(
                latest_approved_payment.values("pk")[:1]
            ),
            cs1f_has_pending_refund=Exists(pending_refund),
            cs1f_pending_refund_id=Subquery(pending_refund.values("pk")[:1]),
            cs1f_pending_refund_payment_id=Subquery(
                pending_refund.values("payment_id")[:1]
            ),
            cs1f_pending_refund_amount=Coalesce(
                Subquery(
                    pending_refund.values("amount")[:1],
                    output_field=MONEY_FIELD,
                ),
                Value(Decimal("0.00")),
                output_field=MONEY_FIELD,
            ),
            cs1f_has_active_credit=Exists(active_credit),
            cs1f_active_credit_payment_id=Subquery(
                active_credit.values("source_payment_id")[:1]
            ),
            cs1f_active_credit_amount=Coalesce(
                _money_subquery(active_credit_amount_qs, "total"),
                Value(Decimal("0.00")),
                output_field=MONEY_FIELD,
            ),
            cs1f_has_active_picking=Exists(active_picking),
            cs1f_has_active_packing=Exists(active_packing),
            cs1f_has_active_shipping=Exists(active_shipping),
            cs1f_has_fulfillment_incident=Exists(fulfillment_incident),
        )
    )

    if not user_has_capability(user, "sales.supervise"):
        qs = qs.filter(created_by=user)

    financial = (
        Q(cs1f_has_pending_refund=True)
        | Q(cs1f_has_active_credit=True)
        | Q(status="cancelled", cs1f_approved_amount__gt=0)
    )
    operational = (
        Q(cs1f_has_fulfillment_incident=True)
        | Q(status="picking", cs1f_has_active_picking=False)
        | Q(status="packed", cs1f_has_active_packing=False)
        | Q(status="shipped", cs1f_has_active_shipping=False)
    )

    return qs.filter(financial | operational).order_by(
        "-updated_at", "-created_at", "-pk"
    )


def category_condition(category: str) -> Q:
    if category == "financial":
        return (
            Q(cs1f_has_pending_refund=True)
            | Q(cs1f_has_active_credit=True)
            | Q(status="cancelled", cs1f_approved_amount__gt=0)
        )
    if category == "operational":
        return (
            Q(cs1f_has_fulfillment_incident=True)
            | Q(status="picking", cs1f_has_active_picking=False)
            | Q(status="packed", cs1f_has_active_packing=False)
            | Q(status="shipped", cs1f_has_active_shipping=False)
        )
    if category == "recovery":
        return Q(
            status="cancelled",
            cs1f_approved_amount__gt=0,
            cs1f_has_pending_refund=False,
            cs1f_has_active_credit=False,
        )
    if category == "credit":
        return Q(cs1f_has_active_credit=True)
    return Q()


def classify_incident(order) -> str:
    """Devuelve una sola prioridad visual para evitar tarjetas duplicadas."""
    if order.cs1f_has_pending_refund:
        return "refund_pending"
    if order.cs1f_has_fulfillment_incident:
        return "warehouse_incident"
    if order.status == "picking" and not order.cs1f_has_active_picking:
        return "missing_picking"
    if order.status == "packed" and not order.cs1f_has_active_packing:
        return "missing_packing"
    if order.status == "shipped" and not order.cs1f_has_active_shipping:
        return "missing_shipping"
    if order.cs1f_has_active_credit:
        return "credit_available"
    if order.status == "cancelled" and order.cs1f_approved_amount > 0:
        return "cancelled_money_unresolved"
    return "review_required"
