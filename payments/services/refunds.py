from dataclasses import dataclass
from decimal import Decimal

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from payments.models import CustomerCredit, Payment, PaymentRefundRequest
from sales.models import Order


@dataclass(frozen=True)
class RefundDecision:
    refund: PaymentRefundRequest
    payment: Payment
    order: Order
    created_or_confirmed: bool
    message: str


def _money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"))


def _active_warehouse_operation(*, company_id, order_id):
    PickingOrder = apps.get_model("warehouse", "PickingOrder")
    PackingOrder = apps.get_model("warehouse", "PackingOrder")
    ShippingOrder = apps.get_model("warehouse", "ShippingOrder")

    pickings = list(
        PickingOrder.objects.select_for_update()
        .filter(company_id=company_id, order_id=order_id)
        .order_by("id")
    )
    packings = list(
        PackingOrder.objects.select_for_update()
        .filter(company_id=company_id, order_id=order_id)
        .order_by("id")
    )
    shippings = list(
        ShippingOrder.objects.select_for_update()
        .filter(company_id=company_id, order_id=order_id)
        .order_by("id")
    )

    active_pickings = [p for p in pickings if p.status != PickingOrder.STATUS_CANCELLED]
    active_packings = [p for p in packings if p.status != PackingOrder.STATUS_CANCELLED]
    active_shippings = [s for s in shippings if s.status != ShippingOrder.STATUS_CANCELLED]
    return {
        "active": bool(active_pickings or active_packings or active_shippings),
        "picking_ids": [p.id for p in active_pickings],
        "packing_ids": [p.id for p in active_packings],
        "shipping_ids": [s.id for s in active_shippings],
    }


@transaction.atomic
def request_full_refund(*, company_id, payment_id, actor, reason, idempotency_key=""):
    if not company_id:
        raise ValidationError("No se detectó una empresa válida.")
    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError("Describe el motivo del reembolso con al menos 5 caracteres.")

    from payments.services.financial_locks import lock_order_then_payment

    order, payment = lock_order_then_payment(
        company_id=company_id,
        payment_id=payment_id,
    )

    if idempotency_key:
        existing = (
            PaymentRefundRequest.objects.filter(
                company_id=company_id, request_idempotency_key=idempotency_key
            )
            .select_related("payment", "order")
            .first()
        )
        if existing:
            return RefundDecision(
                existing, existing.payment, existing.order, False,
                "La solicitud de reembolso ya había sido registrada.",
            )

    if payment.status != "approved":
        raise ValidationError("Solo se puede solicitar reembolso de un pago aprobado.")

    if CustomerCredit.objects.filter(
        company_id=company_id,
        source_payment_id=payment.id,
        status=CustomerCredit.STATUS_ACTIVE,
    ).exists():
        raise ValidationError(
            "Este pago ya fue convertido a saldo a favor. No puede solicitarse además un reembolso."
        )

    active = (
        PaymentRefundRequest.objects.select_for_update()
        .filter(
            company_id=company_id, payment_id=payment.id,
            status=PaymentRefundRequest.STATUS_REQUESTED,
        )
        .first()
    )
    if active:
        return RefundDecision(
            active, payment, order, False,
            "Este pago ya tiene un reembolso pendiente de confirmación.",
        )

    refund = PaymentRefundRequest.objects.create(
        company_id=company_id,
        payment=payment,
        order=order,
        customer_id=order.customer_id,
        requested_by=actor if getattr(actor, "is_authenticated", False) else None,
        status=PaymentRefundRequest.STATUS_REQUESTED,
        amount=_money(payment.amount),
        reason=reason,
        request_idempotency_key=idempotency_key or None,
        metadata={
            "payment_status_at_request": payment.status,
            "order_status_at_request": order.status,
            "payment_amount": str(_money(payment.amount)),
            "full_refund_only": True,
            "provider_automatic_refund": False,
        },
    )
    return RefundDecision(
        refund, payment, order, True,
        "Solicitud registrada. El dinero aún NO está marcado como reembolsado.",
    )


@transaction.atomic
def confirm_manual_full_refund(
    *, company_id, refund_id, actor, confirmation_reference, idempotency_key=""
):
    if not company_id:
        raise ValidationError("No se detectó una empresa válida.")
    confirmation_reference = (confirmation_reference or "").strip()
    if len(confirmation_reference) < 4:
        raise ValidationError(
            "Captura una referencia o evidencia de la devolución realizada fuera de PAKIVOX."
        )

    from payments.services.financial_locks import lock_order_payment_refund

    order, payment, refund = lock_order_payment_refund(
        company_id=company_id,
        refund_id=refund_id,
    )

    if idempotency_key:
        existing = (
            PaymentRefundRequest.objects.filter(
                company_id=company_id, confirm_idempotency_key=idempotency_key
            )
            .select_related("payment", "order")
            .first()
        )
        if existing:
            return RefundDecision(
                existing, existing.payment, existing.order, False,
                "La confirmación de reembolso ya había sido procesada.",
            )

    if refund.status == PaymentRefundRequest.STATUS_CONFIRMED:
        return RefundDecision(
            refund, payment, order, False, "El reembolso ya estaba confirmado."
        )
    if refund.status != PaymentRefundRequest.STATUS_REQUESTED:
        raise ValidationError("Esta solicitud ya no está disponible para confirmación.")
    if payment.status != "approved":
        raise ValidationError(
            "El pago ya no está aprobado. No se modificó el estado financiero."
        )
    if _money(refund.amount) != _money(payment.amount):
        raise ValidationError(
            "CS-1C1 solo permite reembolso total. El monto de la solicitud no coincide con el pago."
        )
    if order.status != "cancelled":
        raise ValidationError(
            "Primero debe completarse la cancelación comercial segura del pedido. "
            "PAKIVOX no confirmará un reembolso mientras el pedido siga operativo."
        )

    warehouse_state = _active_warehouse_operation(
        company_id=company_id, order_id=order.id
    )
    if warehouse_state["active"]:
        raise ValidationError(
            "El pedido todavía tiene picking, packing o shipping activo. "
            "Resuelve primero la operación warehouse."
        )

    payment.status = "refunded"
    payment.reviewed_by = actor if getattr(actor, "is_authenticated", False) else None
    payment.reviewed_at = timezone.now()
    payment.rejection_reason = refund.reason
    payment.save(
        update_fields=[
            "status", "reviewed_by", "reviewed_at",
            "rejection_reason", "updated_at",
        ]
    )

    # Pedido cancelado: conservarlo terminal. Un pedido cancelado no debe aparecer
    # como cartera por cobrar después de devolver el dinero.
    approved_total = sum(
        (
            _money(value) for value in Payment.objects.filter(
                company_id=company_id, order_id=order.id, status="approved"
            ).values_list("amount", flat=True)
        ),
        Decimal("0.00"),
    )
    order.amount_paid = _money(approved_total)
    order.balance_due = Decimal("0.00")
    order.status = "cancelled"
    order.save(update_fields=["amount_paid", "balance_due", "status", "updated_at"])

    refund.status = PaymentRefundRequest.STATUS_CONFIRMED
    refund.confirmed_by = actor if getattr(actor, "is_authenticated", False) else None
    refund.confirmed_at = timezone.now()
    refund.confirmation_reference = confirmation_reference
    refund.confirm_idempotency_key = idempotency_key or None
    refund.metadata = {
        **(refund.metadata or {}),
        "payment_status_after_confirmation": payment.status,
        "order_status_after_confirmation": order.status,
        "approved_total_after_confirmation": str(_money(approved_total)),
        "warehouse_state_at_confirmation": warehouse_state,
    }
    refund.save(
        update_fields=[
            "status", "confirmed_by", "confirmed_at",
            "confirmation_reference", "confirm_idempotency_key", "metadata",
        ]
    )

    from dashboard.services.warehouse_cash_attribution import (
        record_locked_payment_refund,
    )

    record_locked_payment_refund(
        order=order,
        payment=payment,
        refund=refund,
        actor=actor,
        occurred_at=refund.confirmed_at,
    )

    return RefundDecision(
        refund, payment, order, True,
        "Reembolso confirmado con evidencia. El pago dejó de contar como cobro aprobado.",
    )
