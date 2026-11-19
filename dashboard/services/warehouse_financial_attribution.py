"""Servicio transaccional de atribución financiera de ventas por bodega."""

from collections import defaultdict
from decimal import Decimal
from hashlib import sha256
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from dashboard.models import WarehouseFinancialFact
from dashboard.services.warehouse_financial_math import (
    allocate_largest_remainder,
    derive_warehouse_row,
    money_to_cents,
    quality_for_value,
)
from sales.models import Order
from warehouse.models import OrderItemAllocation


UNASSIGNED = "unassigned"
NON_REVENUE_STATUSES = {"draft", "cancelled"}


def _scope_key(warehouse_id):
    return f"warehouse:{warehouse_id}" if warehouse_id else UNASSIGNED


def _idempotency_key(*parts):
    raw = "|".join(str(part) for part in parts)
    return f"af5:{sha256(raw.encode('utf-8')).hexdigest()}"


def allocation_signature(order):
    """Firma estable del reparto activo; no incluye fulfillments cancelados."""
    rows = list(
        OrderItemAllocation.objects.filter(
            company_id=order.company_id,
            fulfillment__order_id=order.id,
        )
        .exclude(fulfillment__status="cancelled")
        .order_by("order_item_id", "fulfillment__warehouse_id", "id")
        .values_list(
            "order_item_id",
            "fulfillment__warehouse_id",
            "commercial_quantity",
            "internal_quantity",
        )
    )
    raw = "|".join(":".join(str(value) for value in row) for row in rows)
    return sha256(f"{order.company_id}|{order.id}|{raw}".encode("utf-8")).hexdigest()


def _actor_values(actor):
    if actor is None or not getattr(actor, "is_authenticated", False):
        return {
            "actor": None,
            "actor_type": WarehouseFinancialFact.ACTOR_SYSTEM,
            "actor_snapshot": {},
        }
    return {
        "actor": actor,
        "actor_type": WarehouseFinancialFact.ACTOR_USER,
        "actor_snapshot": {
            "id": actor.pk,
            "username": getattr(actor, "username", ""),
            "email": getattr(actor, "email", ""),
        },
    }


def _effective_values(company, instant):
    if instant is None or not timezone.is_aware(instant):
        raise ValidationError("La fecha efectiva debe incluir zona horaria.")
    zone_name = getattr(company, "timezone", "America/Mexico_City")
    try:
        zone = ZoneInfo(zone_name)
    except Exception as exc:
        raise ValidationError("La empresa no tiene una zona horaria IANA válida.") from exc
    return {
        "effective_at_utc": instant,
        "effective_local_date": instant.astimezone(zone).date(),
        "timezone_snapshot": zone_name,
    }


def _load_scope_components(order):
    items = list(order.items.select_related("variant").order_by("id"))
    if not items:
        raise ValidationError("El pedido no tiene líneas para atribuir.")

    allocations = list(
        OrderItemAllocation.objects.select_for_update()
        .select_related("fulfillment", "fulfillment__warehouse", "order_item")
        .filter(company_id=order.company_id, fulfillment__order_id=order.id)
        .exclude(fulfillment__status="cancelled")
        .order_by("order_item_id", "fulfillment__warehouse_id", "id")
    )
    by_item = defaultdict(list)
    for allocation in allocations:
        if allocation.order_item.order_id != order.id:
            raise ValidationError("Una asignación apunta a otro pedido.")
        if allocation.fulfillment.warehouse.company_id != order.company_id:
            raise ValidationError("Una asignación apunta a una bodega de otra empresa.")
        by_item[allocation.order_item_id].append(allocation)

    subtotals = defaultdict(int)
    costs = defaultdict(int)
    scope_warehouses = {}

    for item in items:
        quantity = Decimal(str(item.quantity))
        if quantity <= 0:
            raise ValidationError(f"La línea #{item.id} tiene cantidad inválida.")
        weights = []
        allocated = Decimal("0")
        for allocation in by_item.get(item.id, []):
            amount = Decimal(str(allocation.commercial_quantity))
            if amount <= 0:
                raise ValidationError(f"La asignación #{allocation.id} es inválida.")
            allocated += amount
            key = _scope_key(allocation.fulfillment.warehouse_id)
            weights.append((key, amount))
            scope_warehouses[key] = allocation.fulfillment.warehouse
        if allocated > quantity:
            raise ValidationError(f"La línea #{item.id} está sobreasignada.")
        remainder = quantity - allocated
        if remainder:
            weights.append((UNASSIGNED, remainder))
            scope_warehouses[UNASSIGNED] = None

        line_subtotal = money_to_cents(Decimal(str(item.total)))
        line_cost = money_to_cents(Decimal(str(item.total_cost)))
        for key, cents in allocate_largest_remainder(
            total_cents=line_subtotal, weights=weights
        ).items():
            subtotals[key] += cents
        for key, cents in allocate_largest_remainder(
            total_cents=line_cost, weights=weights
        ).items():
            costs[key] += cents

    return subtotals, costs, scope_warehouses


def _build_rows(order):
    subtotals, costs, scope_warehouses = _load_scope_components(order)
    keys = sorted(subtotals)
    weights = [(key, Decimal(max(subtotals[key], 0))) for key in keys]

    discount_total = money_to_cents(Decimal(str(order.discount)))
    tax_total = money_to_cents(Decimal(str(order.tax)))
    shipping_total = money_to_cents(Decimal(str(order.shipping_cost)))
    if sum(weight for _, weight in weights) == 0 and any(
        (discount_total, tax_total, shipping_total)
    ):
        raise ValidationError("No se pueden prorratear cargos sin subtotal positivo.")

    discounts = allocate_largest_remainder(total_cents=discount_total, weights=weights)
    taxes = allocate_largest_remainder(total_cents=tax_total, weights=weights)
    shipping = allocate_largest_remainder(total_cents=shipping_total, weights=weights)

    derived_order_total = (
        sum(subtotals.values()) - discount_total + tax_total + shipping_total
    )
    legacy_total = money_to_cents(Decimal(str(order.total))) - derived_order_total
    legacy = allocate_largest_remainder(total_cents=legacy_total, weights=weights)

    rows = []
    for key in keys:
        row = derive_warehouse_row(
            subtotal_cents=subtotals[key],
            discount_cents=discounts[key],
            tax_cents=taxes[key],
            shipping_cents=shipping[key],
            cost_cents=costs[key],
            legacy_difference_cents=legacy[key],
        )
        row.update(
            {
                "scope_key": key,
                "warehouse": scope_warehouses[key],
                "allocation_method": (
                    WarehouseFinancialFact.ALLOCATION_UNASSIGNED
                    if key == UNASSIGNED
                    else WarehouseFinancialFact.ALLOCATION_EXACT_LINE
                ),
            }
        )
        rows.append(row)
    return rows


def _active_sales(order):
    return list(
        WarehouseFinancialFact.objects.select_for_update(of=("self",))
        .filter(
            company_id=order.company_id,
            order_id=order.id,
            fact_type=WarehouseFinancialFact.FACT_SALE,
            compensating_facts__isnull=True,
        )
        .select_related("warehouse")
        .order_by("id")
    )


def _reverse(active, *, order, source_event_type, source_event_id, occurred_at, actor):
    actor_values = _actor_values(actor)
    effective = _effective_values(order.company, occurred_at)
    created = []
    for original in active:
        values = {
            "company": order.company,
            "warehouse": original.warehouse,
            "order": order,
            "reversal_of": original,
            "fact_type": WarehouseFinancialFact.FACT_REVERSAL,
            "currency": original.currency,
            "source_version": original.source_version,
            "idempotency_key": _idempotency_key(
                order.company_id, order.id, "reversal", original.id,
                source_event_type, source_event_id,
            ),
            "subtotal_cents": -original.subtotal_cents,
            "discount_cents": -original.discount_cents,
            "tax_cents": -original.tax_cents,
            "shipping_cents": -original.shipping_cents,
            "cost_cents": -original.cost_cents,
            "total_cents": -original.total_cents,
            "gross_profit_cents": -original.gross_profit_cents,
            "collected_cents": 0,
            "rounding_residual_cents": -original.rounding_residual_cents,
            "legacy_difference_cents": -original.legacy_difference_cents,
            "has_legacy_inconsistency": original.has_legacy_inconsistency,
            "legacy_inconsistency_reason": original.legacy_inconsistency_reason,
            "date_basis": WarehouseFinancialFact.DATE_REVERSAL_EFFECTIVE,
            "occurred_at": occurred_at,
            "quality_label": original.quality_label,
            "is_estimated": original.is_estimated,
            "allocation_method": original.allocation_method,
            "source_event_type": source_event_type,
            "source_event_id": source_event_id,
            "metadata": {"reverses_fact_id": original.id},
            **effective,
            **actor_values,
        }
        created.append(WarehouseFinancialFact.objects.create(**values))
    return created


@transaction.atomic
def attribute_order_sale(
    *, company_id, order_id, source_event_type, source_event_id,
    actor=None, occurred_at=None,
):
    """Atribuye o recalcula una venta con lock de pedido e idempotencia."""
    source_event_type = str(source_event_type).strip()
    source_event_id = str(source_event_id).strip()
    if not source_event_type or not source_event_id:
        raise ValidationError("El evento fuente es obligatorio.")
    occurred_at = occurred_at or timezone.now()
    if not timezone.is_aware(occurred_at):
        raise ValidationError("occurred_at debe incluir zona horaria.")

    order = (
        Order.objects.select_for_update()
        .select_related("company")
        .filter(company_id=company_id, pk=order_id)
        .first()
    )
    if order is None:
        raise ValidationError("El pedido no existe en la empresa indicada.")

    repeated = list(
        WarehouseFinancialFact.objects.filter(
            company_id=company_id,
            order_id=order_id,
            source_event_type=source_event_type,
            source_event_id=source_event_id,
        ).order_by("id")
    )
    if repeated:
        return {"order": order, "created": [], "existing": repeated, "reversed": []}

    active = _active_sales(order)
    reversed_facts = _reverse(
        active, order=order, source_event_type=source_event_type,
        source_event_id=source_event_id, occurred_at=occurred_at, actor=actor,
    )
    if order.status in NON_REVENUE_STATUSES:
        return {
            "order": order, "created": [], "existing": [],
            "reversed": reversed_facts,
        }
    if order.confirmed_at is None:
        raise ValidationError("El pedido confirmado no tiene confirmed_at.")

    max_version = (
        WarehouseFinancialFact.objects.filter(
            company_id=company_id, order_id=order_id,
            fact_type=WarehouseFinancialFact.FACT_SALE,
        ).aggregate(value=Max("source_version"))["value"] or 0
    )
    version = max_version + 1
    replacement = bool(active)
    effective_at = occurred_at if replacement else order.confirmed_at
    date_basis = (
        WarehouseFinancialFact.DATE_REVERSAL_EFFECTIVE
        if replacement else WarehouseFinancialFact.DATE_ORDER_CONFIRMED
    )
    effective = _effective_values(order.company, effective_at)
    quality = quality_for_value(
        amount_quality=WarehouseFinancialFact.QUALITY_ATTRIBUTED,
        confirmation_date_is_inferred=order.confirmation_date_is_inferred,
    )
    actor_values = _actor_values(actor)

    created = []
    for row in _build_rows(order):
        legacy_reason = (
            "El total histórico del pedido no concilia con sus componentes."
            if row["has_legacy_inconsistency"] else ""
        )
        fact = WarehouseFinancialFact.objects.create(
            company=order.company,
            warehouse=row["warehouse"],
            order=order,
            fact_type=WarehouseFinancialFact.FACT_SALE,
            currency="MXN",
            source_version=version,
            idempotency_key=_idempotency_key(
                company_id, order_id, "sale", version, row["scope_key"],
                source_event_type, source_event_id,
            ),
            subtotal_cents=row["subtotal_cents"],
            discount_cents=row["discount_cents"],
            tax_cents=row["tax_cents"],
            shipping_cents=row["shipping_cents"],
            cost_cents=row["cost_cents"],
            total_cents=row["total_cents"],
            gross_profit_cents=row["gross_profit_cents"],
            collected_cents=0,
            rounding_residual_cents=row["rounding_residual_cents"],
            legacy_difference_cents=row["legacy_difference_cents"],
            has_legacy_inconsistency=row["has_legacy_inconsistency"],
            legacy_inconsistency_reason=legacy_reason,
            date_basis=date_basis,
            occurred_at=occurred_at,
            quality_label=quality["quality_label"],
            is_estimated=quality["is_estimated"],
            allocation_method=row["allocation_method"],
            source_event_type=source_event_type,
            source_event_id=source_event_id,
            metadata={
                "estimate_reasons": quality["estimate_reasons"],
                "replacement_version": replacement,
                "scope": row["scope_key"],
            },
            **effective,
            **actor_values,
        )
        created.append(fact)
    return {"order": order, "created": created, "existing": [], "reversed": reversed_facts}
