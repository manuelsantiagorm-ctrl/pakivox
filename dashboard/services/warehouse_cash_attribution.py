"""Hechos inmutables de cobro y reembolso atribuidos por bodega."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from dashboard.models import WarehouseFinancialFact
from dashboard.services.warehouse_financial_attribution import (
    UNASSIGNED,
    _actor_values,
    _effective_values,
    _idempotency_key,
    _scope_key,
)
from dashboard.services.warehouse_financial_math import (
    allocate_largest_remainder,
    money_to_cents,
)


def _validate_locked_pair(*, order, payment):
    if order.pk != payment.order_id or order.company_id != payment.company_id:
        raise ValidationError("El pago no pertenece al pedido y empresa bloqueados.")


def _sale_scopes(order):
    version = (
        WarehouseFinancialFact.objects.filter(
            company_id=order.company_id,
            order_id=order.id,
            fact_type=WarehouseFinancialFact.FACT_SALE,
        ).aggregate(value=Max("source_version"))["value"]
    )
    if not version:
        return [(UNASSIGNED, None, Decimal("1"))]
    sales = list(
        WarehouseFinancialFact.objects.filter(
            company_id=order.company_id,
            order_id=order.id,
            fact_type=WarehouseFinancialFact.FACT_SALE,
            source_version=version,
        ).select_related("warehouse").order_by("warehouse_id", "id")
    )
    positive = [fact for fact in sales if fact.total_cents > 0]
    if not positive:
        return [(UNASSIGNED, None, Decimal("1"))]
    return [
        (_scope_key(fact.warehouse_id), fact.warehouse, Decimal(fact.total_cents))
        for fact in positive
    ]


def _existing(*, company_id, event_type, event_id):
    return list(
        WarehouseFinancialFact.objects.filter(
            company_id=company_id,
            source_event_type=event_type,
            source_event_id=event_id,
        ).order_by("id")
    )


@transaction.atomic
def record_locked_payment_collection(*, order, payment, actor=None, occurred_at=None):
    """Registra el efectivo real; su reparto por bodega es una estimación."""
    _validate_locked_pair(order=order, payment=payment)
    if payment.status not in {"approved", "refunded"}:
        raise ValidationError("Solo un pago aprobado o reembolsado puede generar cobro histórico.")
    inferred_date = payment.reviewed_at is None
    occurred_at = (
        occurred_at or payment.reviewed_at or payment.paid_at
        or payment.created_at or timezone.now()
    )
    if not timezone.is_aware(occurred_at):
        raise ValidationError("La aprobación del pago debe incluir zona horaria.")
    event_type = "payment_approved"
    event_id = f"payment:{payment.id}"
    repeated = _existing(
        company_id=order.company_id, event_type=event_type, event_id=event_id
    )
    if repeated:
        return {"created": [], "existing": repeated}

    scopes = _sale_scopes(order)
    total_cents = money_to_cents(Decimal(str(payment.amount)))
    distribution = allocate_largest_remainder(
        total_cents=total_cents,
        weights=[(key, weight) for key, _, weight in scopes],
    )
    effective = _effective_values(order.company, occurred_at)
    actor_values = _actor_values(actor)
    created = []
    for key, warehouse, _ in scopes:
        amount = distribution[key]
        fact = WarehouseFinancialFact.objects.create(
            company=order.company,
            warehouse=warehouse,
            order=order,
            fact_type=WarehouseFinancialFact.FACT_COLLECTION,
            currency="MXN",
            source_version=1,
            idempotency_key=_idempotency_key(
                order.company_id, order.id, "collection", payment.id, key
            ),
            collected_cents=amount,
            date_basis=WarehouseFinancialFact.DATE_PAYMENT_APPROVED,
            occurred_at=occurred_at,
            quality_label=WarehouseFinancialFact.QUALITY_ESTIMATED,
            is_estimated=True,
            allocation_method=(
                WarehouseFinancialFact.ALLOCATION_UNASSIGNED
                if warehouse is None
                else WarehouseFinancialFact.ALLOCATION_PROPORTIONAL
            ),
            source_event_type=event_type,
            source_event_id=event_id,
            metadata={
                "payment_id": payment.id,
                "allocation_basis": "latest_sale_total_share",
                "estimate_reasons": [
                    "payment_not_allocated_to_order_lines",
                    *(["inferred_payment_approval_date"] if inferred_date else []),
                ],
                "scope": key,
            },
            **effective,
            **actor_values,
        )
        created.append(fact)
    if sum(fact.collected_cents for fact in created) != total_cents:
        raise ValidationError("El cobro atribuido no concilia con el pago.")
    return {"created": created, "existing": []}


@transaction.atomic
def record_locked_payment_refund(
    *, order, payment, refund, actor=None, occurred_at=None
):
    """Registra la salida real compensando el reparto del cobro original."""
    _validate_locked_pair(order=order, payment=payment)
    if refund.company_id != order.company_id or refund.payment_id != payment.id:
        raise ValidationError("El reembolso no pertenece al pago bloqueado.")
    if refund.status != refund.STATUS_CONFIRMED:
        raise ValidationError("Solo un reembolso confirmado genera salida de efectivo.")
    occurred_at = (
        occurred_at or refund.confirmed_at or refund.requested_at or timezone.now()
    )
    if not timezone.is_aware(occurred_at):
        raise ValidationError("La confirmación del reembolso debe incluir zona horaria.")
    event_type = "refund_confirmed"
    event_id = f"refund:{refund.id}"
    repeated = _existing(
        company_id=order.company_id, event_type=event_type, event_id=event_id
    )
    if repeated:
        return {"created": [], "existing": repeated}

    collections = list(
        WarehouseFinancialFact.objects.select_for_update(of=("self",)).filter(
            company_id=order.company_id,
            order_id=order.id,
            fact_type=WarehouseFinancialFact.FACT_COLLECTION,
            source_event_type="payment_approved",
            source_event_id=f"payment:{payment.id}",
        ).select_related("warehouse").order_by("id")
    )
    refund_cents = money_to_cents(Decimal(str(refund.amount)))
    if collections:
        collected_total = sum(fact.collected_cents for fact in collections)
        if collected_total != refund_cents:
            raise ValidationError(
                "El reembolso total no concilia con el cobro financiero original."
            )
        scopes = [
            (fact.warehouse, -fact.collected_cents, fact.allocation_method)
            for fact in collections
        ]
    else:
        scopes = [(None, -refund_cents, WarehouseFinancialFact.ALLOCATION_UNASSIGNED)]

    effective = _effective_values(order.company, occurred_at)
    actor_values = _actor_values(actor)
    created = []
    for warehouse, amount, method in scopes:
        key = _scope_key(warehouse.id if warehouse else None)
        created.append(WarehouseFinancialFact.objects.create(
            company=order.company,
            warehouse=warehouse,
            order=order,
            fact_type=WarehouseFinancialFact.FACT_REFUND,
            currency="MXN",
            source_version=1,
            idempotency_key=_idempotency_key(
                order.company_id, order.id, "refund", refund.id, key
            ),
            collected_cents=amount,
            date_basis=WarehouseFinancialFact.DATE_REFUND_CONFIRMED,
            occurred_at=occurred_at,
            quality_label=WarehouseFinancialFact.QUALITY_ESTIMATED,
            is_estimated=True,
            allocation_method=method,
            source_event_type=event_type,
            source_event_id=event_id,
            metadata={
                "payment_id": payment.id,
                "refund_id": refund.id,
                "allocation_basis": (
                    "original_collection" if collections else "legacy_unassigned"
                ),
                "estimate_reasons": ["refund_not_allocated_to_order_lines"],
                "scope": key,
            },
            **effective,
            **actor_values,
        ))
    if sum(fact.collected_cents for fact in created) != -refund_cents:
        raise ValidationError("El reembolso atribuido no concilia con la devolución.")
    return {"created": created, "existing": []}
