"""Consultas ejecutivas y conciliación de solo lectura del libro por bodega."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Count, Q, Sum

from companies.models import Company
from dashboard.models import WarehouseFinancialFact
from dashboard.services.warehouse_financial_math import money_to_cents
from inventory.models import Warehouse
from payments.models import Payment, PaymentRefundRequest
from sales.models import Order


SALE_TYPES = (
    WarehouseFinancialFact.FACT_SALE,
    WarehouseFinancialFact.FACT_REVERSAL,
)
CASH_TYPES = (
    WarehouseFinancialFact.FACT_COLLECTION,
    WarehouseFinancialFact.FACT_REFUND,
)


def _quality(queryset, *, amount_field, fact_types):
    relevant = queryset.filter(fact_type__in=fact_types).exclude(**{amount_field: 0})
    if relevant.filter(quality_label=WarehouseFinancialFact.QUALITY_ESTIMATED).exists():
        return WarehouseFinancialFact.QUALITY_ESTIMATED
    if relevant.filter(quality_label=WarehouseFinancialFact.QUALITY_ATTRIBUTED).exists():
        return WarehouseFinancialFact.QUALITY_ATTRIBUTED
    return WarehouseFinancialFact.QUALITY_REAL


def _scope(
    *, company_id, start_date, end_date, warehouse_id,
    warehouse_ids=None, include_unassigned=True,
):
    if start_date is None or end_date is None or start_date > end_date:
        raise ValidationError("El periodo financiero no es válido.")
    company = Company.objects.filter(pk=company_id).first()
    if company is None:
        raise ValidationError("La empresa no existe.")
    warehouse = None
    allowed_ids = None
    if warehouse_id is not None and warehouse_ids is not None:
        raise ValidationError("No se puede combinar warehouse_id con warehouse_ids.")
    if warehouse_id is not None:
        warehouse = Warehouse.objects.filter(
            pk=warehouse_id, company_id=company_id
        ).first()
        if warehouse is None:
            raise ValidationError("La bodega no pertenece a la empresa.")
    elif warehouse_ids is not None:
        allowed_ids = {int(value) for value in warehouse_ids}
        valid_ids = set(Warehouse.objects.filter(
            company_id=company_id, pk__in=allowed_ids
        ).values_list("id", flat=True))
        if valid_ids != allowed_ids:
            raise ValidationError("Una bodega no pertenece a la empresa.")
    facts = WarehouseFinancialFact.objects.filter(
        company_id=company_id,
        effective_local_date__range=(start_date, end_date),
    )
    if warehouse is not None:
        facts = facts.filter(warehouse_id=warehouse.id)
    elif allowed_ids is not None:
        scope_filter = Q(warehouse_id__in=allowed_ids)
        if include_unassigned:
            scope_filter |= Q(warehouse__isnull=True)
        facts = facts.filter(scope_filter)
    return company, warehouse, facts, allowed_ids


def get_financial_summary(
    *, company_id, start_date, end_date, warehouse_id=None,
    warehouse_ids=None, include_unassigned=True,
):
    """Devuelve centavos conciliables; la UI decide cómo formatearlos."""
    company, warehouse, facts, allowed_ids = _scope(
        company_id=company_id,
        start_date=start_date,
        end_date=end_date,
        warehouse_id=warehouse_id,
        warehouse_ids=warehouse_ids,
        include_unassigned=include_unassigned,
    )
    totals = facts.aggregate(
        sales_cents=Sum("total_cents", filter=Q(fact_type__in=SALE_TYPES)),
        cost_cents=Sum("cost_cents", filter=Q(fact_type__in=SALE_TYPES)),
        gross_profit_cents=Sum(
            "gross_profit_cents", filter=Q(fact_type__in=SALE_TYPES)
        ),
        collected_cents=Sum(
            "collected_cents", filter=Q(fact_type__in=CASH_TYPES)
        ),
        sale_fact_count=Count("id", filter=Q(fact_type__in=SALE_TYPES)),
        cash_fact_count=Count("id", filter=Q(fact_type__in=CASH_TYPES)),
        fact_count=Count("id"),
    )
    values = {
        key: int(totals.get(key) or 0)
        for key in ("sales_cents", "cost_cents", "gross_profit_cents", "collected_cents")
    }
    values["fact_count"] = int(totals.get("fact_count") or 0)
    values["sale_fact_count"] = int(totals.get("sale_fact_count") or 0)
    values["cash_fact_count"] = int(totals.get("cash_fact_count") or 0)
    sales = values["sales_cents"]
    values["gross_margin_percent"] = (
        (Decimal(values["gross_profit_cents"]) * Decimal("100") / Decimal(sales))
        if sales else Decimal("0.00")
    )
    values["sales_quality"] = _quality(
        facts, amount_field="total_cents", fact_types=SALE_TYPES
    )
    values["collected_quality"] = _quality(
        facts, amount_field="collected_cents", fact_types=CASH_TYPES
    )
    unassigned = facts.filter(warehouse__isnull=True).aggregate(
        sales=Sum("total_cents", filter=Q(fact_type__in=SALE_TYPES)),
        collected=Sum("collected_cents", filter=Q(fact_type__in=CASH_TYPES)),
    )
    values.update({
        "company_id": company.id,
        "warehouse_id": warehouse.id if warehouse else None,
        "scope_label": (
            warehouse.name if warehouse else
            "Bodegas autorizadas" if allowed_ids is not None else
            "Todas las bodegas"
        ),
        "start_date": start_date,
        "end_date": end_date,
        "unassigned_sales_cents": int(unassigned["sales"] or 0),
        "unassigned_collected_cents": int(unassigned["collected"] or 0),
        "has_unassigned": bool(unassigned["sales"] or unassigned["collected"]),
        "has_legacy_inconsistency": facts.filter(
            has_legacy_inconsistency=True
        ).exists(),
    })
    return values


def reconcile_order_financial_book(*, company_id, order_id):
    """Detecta diferencias de un pedido. Nunca crea, corrige ni elimina hechos."""
    order = Order.objects.filter(
        pk=order_id, company_id=company_id
    ).select_related("company").first()
    if order is None:
        raise ValidationError("El pedido no existe en la empresa indicada.")
    facts = WarehouseFinancialFact.objects.filter(
        company_id=company_id, order_id=order_id
    )
    issues = []
    sale_book = int(
        facts.filter(fact_type__in=SALE_TYPES).aggregate(value=Sum("total_cents"))[
            "value"
        ] or 0
    )
    expected_sale = (
        0 if order.status in {"draft", "cancelled"}
        else money_to_cents(Decimal(str(order.total)))
    )
    if sale_book != expected_sale:
        issues.append({
            "code": "sale_mismatch",
            "expected_cents": expected_sale,
            "book_cents": sale_book,
            "difference_cents": expected_sale - sale_book,
        })

    payments = list(Payment.objects.filter(company_id=company_id, order_id=order_id))
    for payment in payments:
        recorded = int(facts.filter(
            fact_type=WarehouseFinancialFact.FACT_COLLECTION,
            source_event_type="payment_approved",
            source_event_id=f"payment:{payment.id}",
        ).aggregate(value=Sum("collected_cents"))["value"] or 0)
        expected = (
            money_to_cents(Decimal(str(payment.amount)))
            if payment.status in {"approved", "refunded"} else 0
        )
        if recorded != expected:
            issues.append({
                "code": "collection_mismatch",
                "payment_id": payment.id,
                "expected_cents": expected,
                "book_cents": recorded,
                "difference_cents": expected - recorded,
            })

    refunds = PaymentRefundRequest.objects.filter(
        company_id=company_id,
        order_id=order_id,
        status=PaymentRefundRequest.STATUS_CONFIRMED,
    )
    for refund in refunds:
        recorded = int(facts.filter(
            fact_type=WarehouseFinancialFact.FACT_REFUND,
            source_event_type="refund_confirmed",
            source_event_id=f"refund:{refund.id}",
        ).aggregate(value=Sum("collected_cents"))["value"] or 0)
        expected = -money_to_cents(Decimal(str(refund.amount)))
        if recorded != expected:
            issues.append({
                "code": "refund_mismatch",
                "refund_id": refund.id,
                "expected_cents": expected,
                "book_cents": recorded,
                "difference_cents": expected - recorded,
            })

    legacy_count = facts.filter(has_legacy_inconsistency=True).count()
    if legacy_count:
        issues.append({
            "code": "legacy_inconsistency",
            "fact_count": legacy_count,
        })
    return {
        "company_id": company_id,
        "order_id": order_id,
        "is_reconciled": not issues,
        "issues": issues,
        "issue_count": len(issues),
        "checked_fact_count": facts.count(),
        "read_only": True,
    }
