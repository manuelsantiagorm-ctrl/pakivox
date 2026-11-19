from dataclasses import dataclass
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.capability_access import user_has_capability
from payments.models import (
    CustomerCredit, CustomerCreditApplication, Payment, PaymentRefundRequest,
)
from sales.models import Order


@dataclass(frozen=True)
class CustomerCreditDecision:
    credit: CustomerCredit
    refund: PaymentRefundRequest
    payment: Payment
    order: Order
    created: bool
    message: str


def _money(value):
    return Decimal(value or 0).quantize(Decimal("0.01"))


def _actor_company_id(actor):
    profile = getattr(actor, "profile", None)
    if (
        profile is None
        or not getattr(profile, "company_id", None)
        or not getattr(profile, "is_active", False)
        or getattr(profile, "employment_status", None) != profile.STATUS_ACTIVE
    ):
        return None
    return profile.company_id


@transaction.atomic
def convert_refund_to_customer_credit(
    *, company_id, refund_id, actor, reason, idempotency_key=""
):
    """Convierte una devolución pendiente en saldo a favor sin inventar dinero.

    El Payment permanece APPROVED porque el efectivo sigue físicamente en la
    empresa. El pedido debe estar CANCELLED y la solicitud de reembolso debe
    seguir REQUESTED. La aplicación del saldo a una venta futura se hará en la
    fase de aplicación de créditos; CS-1C3 únicamente emite y audita el saldo.
    """
    if not company_id:
        raise ValidationError("No se detectó una empresa válida.")
    if _actor_company_id(actor) != company_id:
        raise PermissionDenied("El usuario no pertenece a la empresa del saldo a favor.")
    if not user_has_capability(actor, "payments.customer_credit.manage"):
        raise PermissionDenied("No tienes permiso para convertir dinero a saldo a favor.")

    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError("Describe el motivo del saldo a favor con al menos 5 caracteres.")

    from payments.services.financial_locks import lock_order_payment_refund

    order, payment, refund = lock_order_payment_refund(
        company_id=company_id,
        refund_id=refund_id,
    )

    if idempotency_key:
        existing = (
            CustomerCredit.objects.filter(
                company_id=company_id, idempotency_key=idempotency_key
            )
            .select_related("source_refund_request", "source_payment", "source_order")
            .first()
        )
        if existing:
            return CustomerCreditDecision(
                existing,
                existing.source_refund_request,
                existing.source_payment,
                existing.source_order,
                False,
                "El saldo a favor ya había sido emitido.",
            )

    existing = (
        CustomerCredit.objects.select_for_update()
        .filter(company_id=company_id, source_refund_request_id=refund.id)
        .first()
    )
    if existing:
        return CustomerCreditDecision(
            existing, refund, payment, order, False,
            "Esta solicitud ya fue convertida a saldo a favor.",
        )

    if refund.status != PaymentRefundRequest.STATUS_REQUESTED:
        raise ValidationError(
            "Solo una solicitud de reembolso pendiente puede convertirse a saldo a favor."
        )
    if payment.status != "approved":
        raise ValidationError(
            "El pago ya no está aprobado; no existe dinero retenido disponible para crédito."
        )
    if order.status != "cancelled":
        raise ValidationError(
            "El pedido debe estar cancelado comercialmente antes de emitir saldo a favor."
        )
    if _money(refund.amount) != _money(payment.amount):
        raise ValidationError(
            "CS-1C3 solo convierte solicitudes totales. El monto no coincide con el pago."
        )
    if refund.customer_id != order.customer_id:
        raise ValidationError("El cliente de la solicitud no coincide con el pedido.")

    amount = _money(refund.amount)
    credit = CustomerCredit.objects.create(
        company_id=company_id,
        customer_id=order.customer_id,
        source_payment=payment,
        source_order=order,
        source_refund_request=refund,
        created_by=actor if getattr(actor, "is_authenticated", False) else None,
        amount=amount,
        remaining_amount=amount,
        status=CustomerCredit.STATUS_ACTIVE,
        reason=reason,
        idempotency_key=idempotency_key or None,
        metadata={
            "payment_status_at_issue": payment.status,
            "order_status_at_issue": order.status,
            "refund_status_before_issue": refund.status,
            "cash_was_not_refunded": True,
            "application_to_future_order_enabled": False,
        },
    )

    refund.status = PaymentRefundRequest.STATUS_CONVERTED_TO_CREDIT
    refund.metadata = {
        **(refund.metadata or {}),
        "customer_credit_id": credit.id,
        "converted_to_credit_at": timezone.now().isoformat(),
        "cash_was_not_refunded": True,
    }
    refund.save(update_fields=["status", "metadata"])

    return CustomerCreditDecision(
        credit, refund, payment, order, True,
        "Saldo a favor emitido. El pago sigue APROBADO porque el dinero permanece en la empresa; no se registró un reembolso ficticio.",
    )

@dataclass(frozen=True)
class OrderCoverageDecision:
    order: Order
    approved_payment_total: Decimal
    customer_credit_total: Decimal
    covered_total: Decimal
    balance_due: Decimal


@dataclass(frozen=True)
class CustomerCreditApplicationDecision:
    application: CustomerCreditApplication
    credit: CustomerCredit
    order: Order
    created: bool
    message: str


@dataclass(frozen=True)
class CustomerCreditReversalDecision:
    order: Order
    reversed_application_ids: tuple
    restored_amount: Decimal


def active_order_credit_total(*, company_id, order_id):
    value = (
        CustomerCreditApplication.objects.filter(
            company_id=company_id,
            order_id=order_id,
            status=CustomerCreditApplication.STATUS_APPLIED,
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    return _money(value)


def customer_available_credit_total(*, company_id, customer_id):
    value = (
        CustomerCredit.objects.filter(
            company_id=company_id,
            customer_id=customer_id,
            status=CustomerCredit.STATUS_ACTIVE,
            remaining_amount__gt=0,
        ).aggregate(total=Sum("remaining_amount"))["total"]
        or Decimal("0.00")
    )
    return _money(value)


def sync_order_financial_coverage(*, order):
    """Fuente única para cobertura del pedido.

    ``Payment APPROVED`` representa efectivo/cobro real.
    ``CustomerCreditApplication APPLIED`` representa valor ya cobrado anteriormente
    y transferido como saldo a favor. Ambos cubren el pedido, pero solo Payment
    debe alimentar métricas de entrada de efectivo.
    """
    approved_total = _money(
        order.payments.filter(status="approved").aggregate(total=Sum("amount"))["total"]
        or Decimal("0.00")
    )
    credit_total = active_order_credit_total(
        company_id=order.company_id, order_id=order.id
    )
    covered_total = _money(approved_total + credit_total)
    total = _money(order.total)
    balance_due = max(total - covered_total, Decimal("0.00"))

    order.amount_paid = covered_total
    order.balance_due = balance_due

    advanced_statuses = {"picking", "packed", "shipped", "delivered", "cancelled"}
    if order.status not in advanced_statuses:
        if total > 0 and balance_due <= 0:
            order.status = "paid"
            order.balance_due = Decimal("0.00")
        else:
            order.status = "pending"

    fields = ["amount_paid", "balance_due", "status"]
    if hasattr(order, "updated_at"):
        fields.append("updated_at")
    order.save(update_fields=fields)

    return OrderCoverageDecision(
        order=order,
        approved_payment_total=approved_total,
        customer_credit_total=credit_total,
        covered_total=covered_total,
        balance_due=_money(order.balance_due),
    )


@transaction.atomic
def apply_customer_credit_to_order(
    *, company_id, credit_id, order_id, amount, actor, reason, idempotency_key
):
    if not company_id:
        raise ValidationError("No se detectó una empresa válida.")
    if _actor_company_id(actor) != company_id:
        raise PermissionDenied("El usuario no pertenece a la empresa del pedido.")
    if not user_has_capability(actor, "payments.customer_credit.apply"):
        raise PermissionDenied("No tienes permiso para aplicar saldo a favor a pedidos.")
    if not idempotency_key:
        raise ValidationError("La aplicación de saldo requiere una llave de idempotencia.")

    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError("Describe el motivo de aplicación con al menos 5 caracteres.")
    amount = _money(amount)
    if amount <= 0:
        raise ValidationError("El monto a aplicar debe ser mayor a cero.")

    # Bloquear primero el pedido: serializa aplicaciones/pagos sobre la misma venta.
    order = (
        Order.objects.select_for_update()
        .select_related("company", "customer")
        .get(pk=order_id, company_id=company_id)
    )

    existing = (
        CustomerCreditApplication.objects.filter(
            company_id=company_id, idempotency_key=idempotency_key
        )
        .select_related("credit", "order")
        .first()
    )
    if existing:
        return CustomerCreditApplicationDecision(
            existing, existing.credit, existing.order, False,
            "Esta aplicación de saldo a favor ya había sido procesada.",
        )

    if order.status not in {"draft", "pending", "pending_payment", "partial_paid", "paid"}:
        raise ValidationError(
            "El saldo a favor solo puede aplicarse antes de que el pedido avance a warehouse."
        )
    if order.total <= 0:
        raise ValidationError("El pedido debe tener un total mayor a cero antes de aplicar saldo.")

    credit = (
        CustomerCredit.objects.select_for_update()
        .select_related("customer", "source_payment", "source_order")
        .get(pk=credit_id, company_id=company_id)
    )
    if credit.customer_id != order.customer_id:
        raise ValidationError("El saldo a favor pertenece a otro cliente.")
    if credit.status != CustomerCredit.STATUS_ACTIVE or _money(credit.remaining_amount) <= 0:
        raise ValidationError("Este saldo a favor ya no está disponible.")

    coverage_before = sync_order_financial_coverage(order=order)
    due = _money(coverage_before.balance_due)
    if due <= 0:
        raise ValidationError("El pedido ya está completamente cubierto.")
    if amount > _money(credit.remaining_amount):
        raise ValidationError("El monto supera el saldo a favor disponible.")
    if amount > due:
        raise ValidationError("El monto supera el saldo pendiente del pedido.")

    application = CustomerCreditApplication.objects.create(
        company_id=company_id,
        customer_id=order.customer_id,
        credit=credit,
        order=order,
        applied_by=actor if getattr(actor, "is_authenticated", False) else None,
        amount=amount,
        status=CustomerCreditApplication.STATUS_APPLIED,
        reason=reason,
        idempotency_key=idempotency_key,
        metadata={
            "credit_remaining_before": str(_money(credit.remaining_amount)),
            "order_balance_before": str(due),
            "creates_payment": False,
        },
    )

    credit.remaining_amount = _money(credit.remaining_amount) - amount
    credit.status = (
        CustomerCredit.STATUS_EXHAUSTED
        if credit.remaining_amount <= 0
        else CustomerCredit.STATUS_ACTIVE
    )
    credit.save(update_fields=["remaining_amount", "status", "updated_at"])

    coverage_after = sync_order_financial_coverage(order=order)
    application.metadata = {
        **(application.metadata or {}),
        "credit_remaining_after": str(_money(credit.remaining_amount)),
        "order_balance_after": str(coverage_after.balance_due),
        "approved_payment_total": str(coverage_after.approved_payment_total),
        "customer_credit_total": str(coverage_after.customer_credit_total),
    }
    application.save(update_fields=["metadata"])

    # Si el crédito completa la venta, iniciar warehouse con la misma regla que
    # un pago aprobado. Si falla la reserva, toda esta transacción se revierte.
    if order.status == "paid" and order.balance_due <= 0:
        if not order.items.exists():
            raise ValidationError(
                "El pedido quedó cubierto, pero no tiene productos para iniciar warehouse."
            )
        from warehouse.services import reserve_paid_order

        reserved_pickings = reserve_paid_order(order, user=actor)
        if not reserved_pickings:
            raise ValidationError(
                "No se aplicó el saldo porque no fue posible crear la reserva operativa completa."
            )

    return CustomerCreditApplicationDecision(
        application, credit, order, True,
        f"Se aplicaron ${amount:.2f} de saldo a favor al pedido {order.folio} sin crear un nuevo cobro.",
    )


@transaction.atomic
def reverse_order_customer_credits(
    *, company_id, order_id, actor, reason
):
    """Devuelve al cliente todo crédito APPLIED de un pedido cancelado.

    Se usa por la cancelación comercial. No crea refunds porque el crédito no
    representa dinero nuevo; únicamente vuelve a estar disponible en el ledger.
    """
    if not company_id:
        raise ValidationError("No se detectó una empresa válida.")
    if _actor_company_id(actor) != company_id:
        raise PermissionDenied("El usuario no pertenece a la empresa del pedido.")

    reason = (reason or "").strip()
    if len(reason) < 5:
        raise ValidationError("Describe el motivo de reversa con al menos 5 caracteres.")

    order = Order.objects.select_for_update().get(pk=order_id, company_id=company_id)
    applications = list(
        CustomerCreditApplication.objects.select_for_update()
        .filter(
            company_id=company_id,
            order_id=order.id,
            status=CustomerCreditApplication.STATUS_APPLIED,
        )
        .order_by("credit_id", "id")
    )
    if not applications:
        return CustomerCreditReversalDecision(order, tuple(), Decimal("0.00"))

    credits = {
        credit.id: credit
        for credit in CustomerCredit.objects.select_for_update()
        .filter(
            company_id=company_id,
            id__in=sorted({row.credit_id for row in applications}),
        )
        .order_by("id")
    }

    restored = Decimal("0.00")
    reversed_ids = []
    now = timezone.now()
    for application in applications:
        credit = credits.get(application.credit_id)
        if credit is None:
            raise ValidationError("No se encontró el saldo origen de una aplicación.")
        new_remaining = _money(credit.remaining_amount) + _money(application.amount)
        if new_remaining > _money(credit.amount):
            raise ValidationError(
                "La reversa excedería el saldo originalmente emitido; operación abortada."
            )
        credit.remaining_amount = new_remaining
        credit.status = CustomerCredit.STATUS_ACTIVE
        credit.save(update_fields=["remaining_amount", "status", "updated_at"])

        application.status = CustomerCreditApplication.STATUS_REVERSED
        application.reversed_by = actor if getattr(actor, "is_authenticated", False) else None
        application.reversed_at = now
        application.reversal_reason = reason
        application.metadata = {
            **(application.metadata or {}),
            "credit_restored": str(_money(application.amount)),
            "reversed_at": now.isoformat(),
        }
        application.save(
            update_fields=[
                "status", "reversed_by", "reversed_at",
                "reversal_reason", "metadata",
            ]
        )
        restored += _money(application.amount)
        reversed_ids.append(application.id)

    return CustomerCreditReversalDecision(
        order, tuple(reversed_ids), _money(restored)
    )
