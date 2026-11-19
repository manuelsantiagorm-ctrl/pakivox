"""Orden canónico de locks financieros: Order -> Payment -> Refund."""

from django.core.exceptions import ValidationError

from payments.models import Payment, PaymentRefundRequest
from sales.models import Order


def lock_order_then_payment(*, company_id, payment_id):
    seed = (
        Payment.objects.filter(pk=payment_id, company_id=company_id)
        .values("order_id")
        .first()
    )
    if seed is None:
        raise Payment.DoesNotExist()
    order = Order.objects.select_for_update().get(
        pk=seed["order_id"], company_id=company_id
    )
    payment = Payment.objects.select_for_update().get(
        pk=payment_id, company_id=company_id, order_id=order.id
    )
    return order, payment


def lock_order_payment_refund(*, company_id, refund_id):
    seed = (
        PaymentRefundRequest.objects.filter(pk=refund_id, company_id=company_id)
        .values("order_id", "payment_id")
        .first()
    )
    if seed is None:
        raise PaymentRefundRequest.DoesNotExist()
    order = Order.objects.select_for_update().get(
        pk=seed["order_id"], company_id=company_id
    )
    payment = Payment.objects.select_for_update().get(
        pk=seed["payment_id"], company_id=company_id, order_id=order.id
    )
    refund = PaymentRefundRequest.objects.select_for_update().get(
        pk=refund_id,
        company_id=company_id,
        order_id=order.id,
        payment_id=payment.id,
    )
    if refund.customer_id != order.customer_id:
        raise ValidationError("El cliente del reembolso no coincide con el pedido.")
    return order, payment, refund
