"""Contexto financiero del Dashboard CEO alimentado solo por el libro."""

import json
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.db.models import Min, Q, Sum
from django.utils import timezone

from dashboard.models import WarehouseFinancialFact
from dashboard.services.warehouse_financial_reporting import get_financial_summary
from inventory.models import Warehouse


QUALITY_LABELS = {
    WarehouseFinancialFact.QUALITY_REAL: "Real",
    WarehouseFinancialFact.QUALITY_ATTRIBUTED: "Atribuido",
    WarehouseFinancialFact.QUALITY_ESTIMATED: "Estimado",
}


def _money(cents):
    return (Decimal(int(cents or 0)) / Decimal("100")).quantize(Decimal("0.01"))


def _scope_kwargs(*, company_id, active_warehouse, accessible_warehouse_ids):
    allowed_ids = {int(value) for value in accessible_warehouse_ids}
    company_warehouses = Warehouse.objects.filter(company_id=company_id)
    if any(field.name == "is_active" for field in Warehouse._meta.get_fields()):
        company_warehouses = company_warehouses.filter(is_active=True)
    company_ids = set(company_warehouses.values_list("id", flat=True))
    if active_warehouse is not None:
        if active_warehouse.company_id != company_id or active_warehouse.id not in allowed_ids:
            raise ValueError("La bodega activa no está autorizada para el Dashboard CEO.")
        return {"warehouse_id": active_warehouse.id}, active_warehouse.name
    include_unassigned = allowed_ids == company_ids
    label = "Todas las bodegas" if include_unassigned else "Bodegas autorizadas"
    return {
        "warehouse_ids": allowed_ids,
        "include_unassigned": include_unassigned,
    }, label


def build_ceo_financial_context(
    *, company, active_warehouse, accessible_warehouse_ids, now=None,
):
    """Entrega importes listos para UI sin consultar Order ni Payment."""
    if company is None:
        raise ValueError("El Dashboard CEO requiere una empresa activa.")
    zone = ZoneInfo(company.timezone or "America/Mexico_City")
    instant = now or timezone.now()
    local_today = instant.astimezone(zone).date()
    month_start = local_today.replace(day=1)
    rolling_start = local_today - timedelta(days=29)
    scope_kwargs, scope_label = _scope_kwargs(
        company_id=company.id,
        active_warehouse=active_warehouse,
        accessible_warehouse_ids=accessible_warehouse_ids,
    )
    scoped_facts = WarehouseFinancialFact.objects.filter(company_id=company.id)
    if "warehouse_id" in scope_kwargs:
        scoped_facts = scoped_facts.filter(warehouse_id=scope_kwargs["warehouse_id"])
    else:
        allowed_ids = scope_kwargs["warehouse_ids"]
        if scope_kwargs["include_unassigned"]:
            scoped_facts = scoped_facts.filter(
                Q(warehouse_id__in=allowed_ids) | Q(warehouse__isnull=True)
            )
        else:
            scoped_facts = scoped_facts.filter(warehouse_id__in=allowed_ids)
    first_date = scoped_facts.aggregate(value=Min("effective_local_date"))["value"]
    accumulated = get_financial_summary(
        company_id=company.id,
        start_date=first_date or local_today,
        end_date=local_today,
        **scope_kwargs,
    )
    monthly = get_financial_summary(
        company_id=company.id,
        start_date=month_start,
        end_date=local_today,
        **scope_kwargs,
    )

    # Serie diaria real del periodo visible. Se agrega en PostgreSQL/SQLite y
    # conserva exactamente el mismo alcance multiempresa/multibodega del libro.
    series_start = min(month_start, rolling_start)
    daily_rows = scoped_facts.filter(
        effective_local_date__range=(series_start, local_today)
    ).values("effective_local_date").annotate(
        sales_cents=Sum(
            "total_cents",
            filter=Q(fact_type__in=(
                WarehouseFinancialFact.FACT_SALE,
                WarehouseFinancialFact.FACT_REVERSAL,
            )),
        ),
        collected_cents=Sum(
            "collected_cents",
            filter=Q(fact_type__in=(
                WarehouseFinancialFact.FACT_COLLECTION,
                WarehouseFinancialFact.FACT_REFUND,
            )),
        ),
        profit_cents=Sum(
            "gross_profit_cents",
            filter=Q(fact_type__in=(
                WarehouseFinancialFact.FACT_SALE,
                WarehouseFinancialFact.FACT_REVERSAL,
            )),
        ),
    ).order_by("effective_local_date")
    by_day = {row["effective_local_date"]: row for row in daily_rows}
    trend_labels = []
    trend_sales = []
    trend_collected = []
    trend_profit = []
    cursor = month_start
    while cursor <= local_today:
        row = by_day.get(cursor, {})
        trend_labels.append(cursor.strftime("%d/%m"))
        trend_sales.append(float(_money(row.get("sales_cents"))))
        trend_collected.append(float(_money(row.get("collected_cents"))))
        trend_profit.append(float(_money(row.get("profit_cents"))))
        cursor += date.resolution

    executive_collection_labels = []
    executive_collection_sales = []
    executive_collection_collected = []
    cursor = rolling_start
    while cursor <= local_today:
        row = by_day.get(cursor, {})
        executive_collection_labels.append(cursor.strftime("%d/%m"))
        executive_collection_sales.append(float(_money(row.get("sales_cents"))))
        executive_collection_collected.append(
            float(_money(row.get("collected_cents")))
        )
        cursor += date.resolution

    executive_rolling_sales = sum(
        (Decimal(str(value)) for value in executive_collection_sales),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    executive_rolling_collected = sum(
        (Decimal(str(value)) for value in executive_collection_collected),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    executive_collection_rate = (
        (executive_rolling_collected / executive_rolling_sales * Decimal("100"))
        if executive_rolling_sales > 0
        else Decimal("0.00")
    ).quantize(Decimal("0.1"))

    sales = _money(accumulated["sales_cents"])
    collected = _money(accumulated["collected_cents"])
    month_sales = _money(monthly["sales_cents"])
    month_collected = _money(monthly["collected_cents"])
    return {
        "sales_total": sales,
        "active_sales_total": sales,
        "paid_total": collected,
        "balance_due_total": max(sales - collected, Decimal("0.00")),
        "sales_cost_total": _money(accumulated["cost_cents"]),
        "gross_profit_total": _money(accumulated["gross_profit_cents"]),
        "gross_margin_percent": accumulated["gross_margin_percent"],
        "month_sales_total": month_sales,
        "month_active_sales_total": month_sales,
        "month_paid_total": month_collected,
        "month_balance_due_total": max(
            month_sales - month_collected, Decimal("0.00")
        ),
        "month_sales_cost_total": _money(monthly["cost_cents"]),
        "month_gross_profit_total": _money(monthly["gross_profit_cents"]),
        "month_gross_margin_percent": monthly["gross_margin_percent"],
        "month_payments_total": month_collected,
        "profitability_counts": [
            float(sales),
            float(_money(accumulated["cost_cents"])),
            float(_money(accumulated["gross_profit_cents"])),
        ],
        "finance_trend_labels": json.dumps(trend_labels),
        "finance_trend_sales": json.dumps(trend_sales),
        "finance_trend_collected": json.dumps(trend_collected),
        "finance_trend_profit": json.dumps(trend_profit),
        "finance_trend_has_data": any(
            value != 0
            for series in (trend_sales, trend_collected, trend_profit)
            for value in series
        ),
        "executive_collection_labels": json.dumps(executive_collection_labels),
        "executive_collection_sales": json.dumps(executive_collection_sales),
        "executive_collection_collected": json.dumps(
            executive_collection_collected
        ),
        "executive_collection_labels_data": executive_collection_labels,
        "executive_collection_sales_data": executive_collection_sales,
        "executive_collection_collected_data": executive_collection_collected,
        "executive_rolling_sales": executive_rolling_sales,
        "executive_rolling_collected": executive_rolling_collected,
        "executive_collection_rate": executive_collection_rate,
        "executive_collection_has_data": any(
            value != 0
            for series in (
                executive_collection_sales,
                executive_collection_collected,
            )
            for value in series
        ),
        "finance_scope_label": scope_label,
        "finance_is_warehouse_scope": active_warehouse is not None,
        "finance_supports_sku_rankings": (
            active_warehouse is None and scope_kwargs.get("include_unassigned") is True
        ),
        "finance_period_start": first_date or local_today,
        "finance_period_end": local_today,
        "finance_sales_quality": (
            QUALITY_LABELS[accumulated["sales_quality"]]
            if accumulated["sale_fact_count"] else "Sin datos"
        ),
        "finance_collected_quality": (
            QUALITY_LABELS[accumulated["collected_quality"]]
            if accumulated["cash_fact_count"] else "Sin datos"
        ),
        "finance_has_unassigned": accumulated["has_unassigned"],
        "finance_unassigned_sales": _money(accumulated["unassigned_sales_cents"]),
        "finance_unassigned_collected": _money(
            accumulated["unassigned_collected_cents"]
        ),
        "finance_has_legacy_inconsistency": accumulated[
            "has_legacy_inconsistency"
        ],
        "finance_fact_count": accumulated["fact_count"],
        "finance_from_ledger": True,
    }
