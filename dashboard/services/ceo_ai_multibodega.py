import math
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from accounts.warehouse_access import (
    ACCESS_VIEW,
    ALL_WAREHOUSES_VALUE,
    get_accessible_warehouses,
)
from inventory.models import Stock
from payments.models import Payment
from purchases.models import PurchaseOrder, PurchaseOrderItem, SupplierPayment
from sales.models import Order, OrderItem
from warehouse.models import (
    OrderFulfillment,
    OrderItemAllocation,
    PackingOrder,
    PickingOrder,
    ShippingOrder,
)


MAX_AI_WAREHOUSES = 10


ALL_SCOPE_PHRASES = {
    "todas las bodegas",
    "todas mis bodegas",
    "toda la empresa",
    "empresa completa",
    "vision global",
    "visión global",
    "consolidado",
    "consolidada",
    "global",
}

COMPARISON_PHRASES = {
    "compara",
    "comparar",
    "comparacion",
    "comparación",
    "contra",
    "versus",
    "vs",
    "cual bodega",
    "cuál bodega",
    "que bodega",
    "qué bodega",
    "bodega vendio mas",
    "bodega vendió más",
    "bodegas vendieron",
    "ranking de bodegas",
    "mejor bodega",
    "peor bodega",
}

COMPANY_SCOPE_PHRASES = {
    "toda la empresa",
    "empresa completa",
    "resultado corporativo",
    "nivel empresa",
    "consolidado corporativo",
}

WAREHOUSE_TOPIC_PHRASES = {
    "bodega",
    "bodegas",
    "almacen",
    "almacén",
    "almacenes",
    "cedis",
    "sucursal",
    "sucursales",
}


def decimal_value(value, default="0"):
    if value in {None, ""}:
        return Decimal(default)

    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def number(value):
    return float(decimal_value(value))


def integer(value):
    return int(decimal_value(value).to_integral_value())


def percentage(numerator, denominator):
    numerator = decimal_value(numerator)
    denominator = decimal_value(denominator)

    if denominator <= 0:
        return 0.0

    return number(numerator / denominator * Decimal("100"))


def normalize_text(value):
    value = str(value or "").strip().lower()
    value = unicodedata.normalize("NFD", value)
    value = "".join(
        character
        for character in value
        if unicodedata.category(character) != "Mn"
    )
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def get_volume_factor(variant):
    if not variant:
        return Decimal("1")

    possible_fields = [
        "units_per_package",
        "pieces_per_package",
        "items_per_package",
        "internal_units_per_sale_unit",
        "quantity_per_package",
        "conversion_factor",
        "sales_unit_factor",
        "unit_conversion_factor",
        "package_quantity",
        "pack_quantity",
        "content_quantity",
    ]

    for field_name in possible_fields:
        if hasattr(variant, field_name):
            factor = decimal_value(
                getattr(variant, field_name),
                "1",
            )

            if factor > 0:
                return factor

    specifications = getattr(variant, "specifications", {}) or {}

    if isinstance(specifications, dict):
        for field_name in [
            *possible_fields,
            "piezas_por_paquete",
            "unidades_por_paquete",
            "contenido",
        ]:
            factor = decimal_value(
                specifications.get(field_name),
                "1",
            )

            if factor > 0:
                return factor

    sku = str(getattr(variant, "sku", "") or "")
    match = re.search(r"PACK(\d+)", sku.upper())

    if match:
        factor = decimal_value(match.group(1), "1")

        if factor > 0:
            return factor

    return Decimal("1")


def _contains_phrase(normalized_question, phrases):
    return any(
        normalize_text(phrase) in normalized_question
        for phrase in phrases
    )


def _warehouse_aliases(warehouse):
    aliases = []

    for value in [
        getattr(warehouse, "name", ""),
        getattr(warehouse, "code", ""),
    ]:
        normalized = normalize_text(value)

        if normalized and normalized not in aliases:
            aliases.append(normalized)

    return aliases


def _question_mentions_warehouse(normalized_question, warehouse):
    for alias in _warehouse_aliases(warehouse):
        if len(alias) < 2:
            continue

        pattern = rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])"

        if re.search(pattern, normalized_question):
            return True

    return False


def resolve_ai_warehouse_scope(
    *,
    user,
    question="",
    selected_warehouse_value=None,
):
    """
    Resuelve el alcance multi-bodega exclusivamente desde bodegas
    permitidas al usuario. Nunca acepta IDs de empresa desde frontend.

    Prioridad:
    1. Bodegas mencionadas expresamente.
    2. Petición explícita de comparación / toda la empresa.
    3. Selector activo del Dashboard.
    4. Todas las bodegas permitidas.
    """

    accessible_queryset = get_accessible_warehouses(
        user=user,
        required_level=ACCESS_VIEW,
    )

    accessible_total = accessible_queryset.count()
    accessible_warehouses = list(
        accessible_queryset[:MAX_AI_WAREHOUSES]
    )

    accessible_ids = [
        warehouse.id
        for warehouse in accessible_warehouses
    ]

    normalized_question = normalize_text(question)

    mentioned = [
        warehouse
        for warehouse in accessible_warehouses
        if _question_mentions_warehouse(
            normalized_question,
            warehouse,
        )
    ]

    explicit_company = _contains_phrase(
        normalized_question,
        COMPANY_SCOPE_PHRASES,
    )

    explicit_all = _contains_phrase(
        normalized_question,
        ALL_SCOPE_PHRASES,
    )

    comparison_requested = _contains_phrase(
        normalized_question,
        COMPARISON_PHRASES,
    )

    warehouse_topic = _contains_phrase(
        normalized_question,
        WAREHOUSE_TOPIC_PHRASES,
    )

    selected_warehouse = None

    if selected_warehouse_value not in {
        None,
        "",
        ALL_WAREHOUSES_VALUE,
    }:
        try:
            selected_id = int(selected_warehouse_value)
        except (TypeError, ValueError):
            selected_id = None

        if selected_id:
            selected_warehouse = next(
                (
                    warehouse
                    for warehouse in accessible_warehouses
                    if warehouse.id == selected_id
                ),
                None,
            )

    if mentioned:
        selected = mentioned
        mode = "comparison" if len(mentioned) > 1 else "named"
        source = "question"

    elif explicit_company:
        selected = accessible_warehouses
        mode = "company"
        source = "question"

    elif explicit_all or comparison_requested:
        selected = accessible_warehouses
        mode = "comparison" if comparison_requested else "all"
        source = "question"

    elif selected_warehouse:
        selected = [selected_warehouse]
        mode = "current"
        source = "selector"

    else:
        selected = accessible_warehouses
        mode = "all"
        source = "selector"

    selected_ids = [
        warehouse.id
        for warehouse in selected
    ]

    selected_names = [
        warehouse.name
        for warehouse in selected
    ]

    if not selected:
        label = "Sin bodegas disponibles"
    elif len(selected) == 1:
        label = selected[0].name
    elif mode == "company":
        label = "Toda la empresa"
    else:
        label = f"{len(selected)} bodegas comparadas"

    return {
        "mode": mode,
        "source": source,
        "label": label,
        "warehouse_ids": selected_ids,
        "warehouse_names": selected_names,
        "mentioned_warehouse_ids": [warehouse.id for warehouse in mentioned],
        "mentioned_warehouse_names": [warehouse.name for warehouse in mentioned],
        "accessible_warehouse_ids": accessible_ids,
        "accessible_warehouse_names": [
            warehouse.name
            for warehouse in accessible_warehouses
        ],
        "accessible_count": accessible_total,
        "analyzed_count": len(selected_ids),
        "max_supported": MAX_AI_WAREHOUSES,
        "comparison_requested": comparison_requested,
        "warehouse_topic_detected": warehouse_topic,
        "truncated_to_limit": accessible_total > MAX_AI_WAREHOUSES,
    }


def _period_bounds(resolved_period):
    def normalize_date(value, field_name):
        if isinstance(value, datetime):
            return value.date()

        if isinstance(value, date):
            return value

        if isinstance(value, str):
            normalized_value = value.strip()

            if not normalized_value:
                raise ValueError(f"El periodo contiene {field_name} vacío.")

            try:
                return date.fromisoformat(normalized_value)
            except ValueError as exc:
                raise ValueError(
                    f"El periodo contiene {field_name} inválido: "
                    f"{normalized_value!r}."
                ) from exc

        raise TypeError(
            f"El periodo contiene {field_name} con un tipo no permitido: "
            f"{type(value).__name__}."
        )

    if not isinstance(resolved_period, dict):
        raise TypeError("resolved_period debe ser un diccionario.")

    start_date = normalize_date(
        resolved_period.get("start_date"),
        "start_date",
    )
    end_date = normalize_date(
        resolved_period.get("end_date"),
        "end_date",
    )

    if end_date < start_date:
        raise ValueError(
            "La fecha final del periodo no puede ser anterior " "a la fecha inicial."
        )

    current_timezone = timezone.get_current_timezone()

    start_datetime = timezone.make_aware(
        datetime.combine(start_date, time.min),
        current_timezone,
    )

    end_datetime_exclusive = timezone.make_aware(
        datetime.combine(
            end_date + timedelta(days=1),
            time.min,
        ),
        current_timezone,
    )

    return (
        start_date,
        end_date,
        start_datetime,
        end_datetime_exclusive,
    )


def _safe_payment_date_field():
    field_names = {
        field.name
        for field in Payment._meta.get_fields()
    }

    for candidate in [
        "paid_at",
        "reviewed_at",
        "created_at",
    ]:
        if candidate in field_names:
            return candidate

    return "created_at"


def _build_period_filter(
    *,
    field_name,
    start_datetime,
    end_datetime_exclusive,
):
    query = Q(
        **{
            f"{field_name}__gte": start_datetime,
            f"{field_name}__lt": end_datetime_exclusive,
        }
    )

    if field_name != "created_at":
        query |= Q(
            **{
                f"{field_name}__isnull": True,
                "created_at__gte": start_datetime,
                "created_at__lt": end_datetime_exclusive,
            }
        )

    return query


def _order_relation_data(
    *,
    company,
    order_ids,
    accessible_warehouse_ids,
):
    allocation_bases = defaultdict(
        lambda: defaultdict(Decimal)
    )
    allocations_by_item = defaultdict(list)

    if not order_ids:
        return {
            "allocation_bases": allocation_bases,
            "allocations_by_item": allocations_by_item,
            "fulfillments": defaultdict(set),
            "pickings": defaultdict(set),
        }

    allocations = (
        OrderItemAllocation.objects.filter(
            company=company,
            order_item__order_id__in=order_ids,
            fulfillment__warehouse_id__in=accessible_warehouse_ids,
        )
        .select_related(
            "fulfillment",
            "fulfillment__warehouse",
            "order_item",
            "order_item__variant",
            "order_item__variant__product",
        )
    )

    for allocation in allocations:
        order_item = allocation.order_item
        order_id = order_item.order_id
        warehouse_id = allocation.fulfillment.warehouse_id
        item_quantity = decimal_value(order_item.quantity)
        allocated_commercial = decimal_value(
            allocation.commercial_quantity
        )

        if item_quantity > 0:
            ratio = allocated_commercial / item_quantity
        else:
            ratio = Decimal("0")

        ratio = max(
            Decimal("0"),
            min(ratio, Decimal("1")),
        )

        base = decimal_value(order_item.total) * ratio

        if base <= 0:
            base = decimal_value(
                allocation.internal_quantity,
                "0",
            )

        allocation_bases[order_id][warehouse_id] += base
        allocations_by_item[order_item.id].append(allocation)

    fulfillments = defaultdict(set)

    for order_id, warehouse_id in (
        OrderFulfillment.objects.filter(
            company=company,
            order_id__in=order_ids,
            warehouse_id__in=accessible_warehouse_ids,
        )
        .exclude(status=OrderFulfillment.STATUS_CANCELLED)
        .values_list("order_id", "warehouse_id")
    ):
        fulfillments[order_id].add(warehouse_id)

    pickings = defaultdict(set)

    for order_id, warehouse_id in (
        PickingOrder.objects.filter(
            company=company,
            order_id__in=order_ids,
            warehouse_id__in=accessible_warehouse_ids,
        )
        .exclude(status=PickingOrder.STATUS_CANCELLED)
        .values_list("order_id", "warehouse_id")
    ):
        if warehouse_id:
            pickings[order_id].add(warehouse_id)

    return {
        "allocation_bases": allocation_bases,
        "allocations_by_item": allocations_by_item,
        "fulfillments": fulfillments,
        "pickings": pickings,
    }


def _weights_for_order(order_id, relation_data):
    bases = relation_data["allocation_bases"].get(
        order_id,
        {},
    )

    total_base = sum(
        bases.values(),
        Decimal("0"),
    )

    if total_base > 0:
        return {
            warehouse_id: base / total_base
            for warehouse_id, base in bases.items()
            if base > 0
        }

    warehouse_ids = relation_data["fulfillments"].get(
        order_id,
        set(),
    )

    if not warehouse_ids:
        warehouse_ids = relation_data["pickings"].get(
            order_id,
            set(),
        )

    if not warehouse_ids:
        return {}

    equal_weight = Decimal("1") / Decimal(
        len(warehouse_ids)
    )

    return {
        warehouse_id: equal_weight
        for warehouse_id in warehouse_ids
    }


def _empty_warehouse_row(warehouse):
    return {
        "warehouse_id": warehouse.id,
        "warehouse_key": f"WH-{warehouse.id}",
        "warehouse_name": warehouse.name,
        "warehouse_code": getattr(warehouse, "code", "") or "",
        "registered_order_ids": set(),
        "delivered_order_ids": set(),
        "payment_ids": set(),
        "registered_sales": Decimal("0"),
        "registered_costs": Decimal("0"),
        "registered_profit": Decimal("0"),
        "accounts_receivable": Decimal("0"),
        "cash_collected": Decimal("0"),
        "recognized_revenue": Decimal("0"),
        "recognized_costs": Decimal("0"),
        "recognized_profit": Decimal("0"),
        "purchase_orders": 0,
        "purchases_total": Decimal("0"),
        "supplier_payments": Decimal("0"),
        "active_fulfillments": 0,
        "delivered_fulfillments": 0,
        "pending_pickings": 0,
        "completed_pickings": 0,
        "pending_packings": 0,
        "packed_packings": 0,
        "pending_shippings": 0,
        "shipped_shippings": 0,
        "delivered_shippings": 0,
        "physical_stock": Decimal("0"),
        "reserved_stock": Decimal("0"),
        "committed_stock": Decimal("0"),
        "available_stock": Decimal("0"),
        "incoming_stock_15d": Decimal("0"),
        "overdue_purchase_stock": Decimal("0"),
        "out_of_stock_skus": 0,
        "critical_skus": 0,
        "high_risk_skus": 0,
        "projected_stockout_15d_skus": 0,
        "products": defaultdict(
            lambda: {
                "sku": "",
                "product_name": "",
                "sales_units": Decimal("0"),
                "internal_units": Decimal("0"),
                "revenue": Decimal("0"),
                "cost": Decimal("0"),
                "profit": Decimal("0"),
            }
        ),
        "daily_registered_sales": defaultdict(Decimal),
        "daily_recognized_revenue": defaultdict(Decimal),
        "daily_collections": defaultdict(Decimal),
    }


def _apply_order_financials(
    *,
    order,
    weights,
    selected_ids,
    warehouse_rows,
    recognized=False,
    recognized_date=None,
):
    for warehouse_id, weight in weights.items():
        if warehouse_id not in selected_ids:
            continue

        row = warehouse_rows[warehouse_id]

        if recognized:
            row["delivered_order_ids"].add(order.id)
            row["recognized_revenue"] += decimal_value(order.total) * weight
            row["recognized_costs"] += decimal_value(order.total_cost) * weight
            row["recognized_profit"] += decimal_value(order.gross_profit) * weight

            if recognized_date:
                row["daily_recognized_revenue"][recognized_date] += (
                    decimal_value(order.total) * weight
                )

        else:
            row["registered_order_ids"].add(order.id)
            row["registered_sales"] += decimal_value(order.total) * weight
            row["registered_costs"] += decimal_value(order.total_cost) * weight
            row["registered_profit"] += decimal_value(order.gross_profit) * weight
            row["accounts_receivable"] += decimal_value(order.balance_due) * weight
            row["daily_registered_sales"][order.created_at.date()] += (
                decimal_value(order.total) * weight
            )


def _product_rows_for_orders(
    *,
    company,
    orders,
    relation_data,
    selected_ids,
    warehouse_rows,
):
    if not orders:
        return

    order_map = {
        order.id: order
        for order in orders
    }

    items = (
        OrderItem.objects.filter(
            order_id__in=order_map.keys(),
            order__company=company,
        )
        .select_related(
            "variant",
            "variant__product",
        )
    )

    for item in items:
        variant = item.variant

        if not variant:
            continue

        sku = variant.sku or f"SKU-{variant.id}"
        product_name = getattr(
            getattr(variant, "product", None),
            "name",
            "Sin producto",
        )
        factor = get_volume_factor(variant)
        item_quantity = decimal_value(item.quantity)
        allocations = relation_data["allocations_by_item"].get(
            item.id,
            [],
        )

        if allocations:
            for allocation in allocations:
                warehouse_id = allocation.fulfillment.warehouse_id

                if warehouse_id not in selected_ids:
                    continue

                commercial_quantity = decimal_value(
                    allocation.commercial_quantity
                )
                internal_quantity = decimal_value(
                    allocation.internal_quantity
                )

                if item_quantity > 0:
                    ratio = commercial_quantity / item_quantity
                else:
                    ratio = Decimal("0")

                ratio = max(
                    Decimal("0"),
                    min(ratio, Decimal("1")),
                )

                product = warehouse_rows[warehouse_id]["products"][sku]
                product["sku"] = sku
                product["product_name"] = product_name
                product["sales_units"] += commercial_quantity
                product["internal_units"] += internal_quantity
                product["revenue"] += decimal_value(item.total) * ratio
                product["cost"] += decimal_value(item.total_cost) * ratio
                product["profit"] += decimal_value(item.gross_profit) * ratio

            continue

        weights = _weights_for_order(
            item.order_id,
            relation_data,
        )

        for warehouse_id, weight in weights.items():
            if warehouse_id not in selected_ids:
                continue

            product = warehouse_rows[warehouse_id]["products"][sku]
            product["sku"] = sku
            product["product_name"] = product_name
            product["sales_units"] += item_quantity * weight
            product["internal_units"] += item_quantity * factor * weight
            product["revenue"] += decimal_value(item.total) * weight
            product["cost"] += decimal_value(item.total_cost) * weight
            product["profit"] += decimal_value(item.gross_profit) * weight


def _chart_from_daily(
    *,
    daily_totals,
    start_date,
    end_date,
    title,
    metric,
):
    days = (end_date - start_date).days + 1

    if days <= 12:
        labels = []
        values = []
        current = start_date

        while current <= end_date:
            labels.append(current.strftime("%d/%m"))
            values.append(number(daily_totals.get(current, 0)))
            current += timedelta(days=1)

        return {
            "grouping": "day",
            "labels": labels,
            "values": values,
            "title": title,
            "metric": metric,
        }

    buckets = []
    current = start_date

    while current <= end_date and len(buckets) < 12:
        bucket_end = min(
            current + timedelta(days=6),
            end_date,
        )
        buckets.append((current, bucket_end))
        current = bucket_end + timedelta(days=1)

    labels = []
    values = []

    for bucket_start, bucket_end in buckets:
        labels.append(
            f"{bucket_start.strftime('%d/%m')}-{bucket_end.strftime('%d/%m')}"
        )
        total = Decimal("0")
        day = bucket_start

        while day <= bucket_end:
            total += daily_totals.get(day, Decimal("0"))
            day += timedelta(days=1)

        values.append(number(total))

    return {
        "grouping": "week",
        "labels": labels,
        "values": values,
        "title": title,
        "metric": metric,
    }


def _pricing_guardrail(average_sale_price, average_unit_cost):
    sale_price = decimal_value(average_sale_price)
    unit_cost = decimal_value(average_unit_cost)
    minimum_margin_percent = decimal_value(
        getattr(settings, "CEO_AI_MIN_MARGIN_PERCENT", 25)
    )
    minimum_margin_percent = max(
        Decimal("0"),
        min(minimum_margin_percent, Decimal("95")),
    )

    if sale_price <= 0 or unit_cost < 0:
        return {
            "pricing_data_complete": False,
            "current_margin_percent": 0.0,
            "minimum_margin_percent": number(minimum_margin_percent),
            "minimum_safe_price": None,
            "max_safe_discount_percent": 0.0,
            "suggested_discount_percent": 0.0,
        }

    current_margin = (
        (sale_price - unit_cost)
        / sale_price
        * Decimal("100")
    )
    denominator = Decimal("1") - minimum_margin_percent / Decimal("100")
    minimum_safe_price = (
        unit_cost / denominator
        if denominator > 0
        else sale_price
    )
    max_discount = (
        (sale_price - minimum_safe_price)
        / sale_price
        * Decimal("100")
    )
    max_discount = max(
        Decimal("0"),
        min(max_discount, Decimal("100")),
    )
    suggested = min(max_discount, Decimal("10"))

    if suggested < Decimal("3"):
        suggested = Decimal("0")

    return {
        "pricing_data_complete": True,
        "current_margin_percent": number(current_margin),
        "minimum_margin_percent": number(minimum_margin_percent),
        "minimum_safe_price": number(minimum_safe_price),
        "max_safe_discount_percent": number(max_discount),
        "suggested_discount_percent": number(suggested),
    }


def build_warehouse_business_analysis(
    *,
    company,
    warehouse_scope,
    resolved_period,
):
    selected_ids = set(
        warehouse_scope.get("warehouse_ids", [])
    )
    accessible_ids = set(
        warehouse_scope.get("accessible_warehouse_ids", [])
    )

    if not selected_ids or not accessible_ids:
        return {
            "scope": warehouse_scope,
            "summary": {},
            "warehouses": [],
            "rankings": {},
            "top_products_selected_period": [],
            "stock_risk": [],
            "stock_risk_by_warehouse": [],
            "commercial_opportunities": [],
            "charts": {},
            "warnings": [
                "No existen bodegas permitidas para realizar el análisis."
            ],
        }

    # La lista se reconstruye por company + IDs previamente validados.
    from inventory.models import Warehouse

    warehouse_objects = list(
        Warehouse.objects.filter(
            company=company,
            id__in=selected_ids,
        ).order_by("name", "id")
    )

    warehouse_rows = {
        warehouse.id: _empty_warehouse_row(warehouse)
        for warehouse in warehouse_objects
    }

    (
        start_date,
        end_date,
        start_datetime,
        end_datetime_exclusive,
    ) = _period_bounds(resolved_period)

    selected_orders = list(
        Order.objects.filter(
            company=company,
            created_at__gte=start_datetime,
            created_at__lt=end_datetime_exclusive,
        )
        .exclude(status="cancelled")
        .select_related("customer")
    )

    relation_data = _order_relation_data(
        company=company,
        order_ids=[order.id for order in selected_orders],
        accessible_warehouse_ids=accessible_ids,
    )

    scoped_registered_order_ids = set()

    for order in selected_orders:
        weights = _weights_for_order(order.id, relation_data)

        if not any(
            warehouse_id in selected_ids
            for warehouse_id in weights
        ):
            continue

        scoped_registered_order_ids.add(order.id)

        _apply_order_financials(
            order=order,
            weights=weights,
            selected_ids=selected_ids,
            warehouse_rows=warehouse_rows,
            recognized=False,
        )

    delivered_filter = (
        Q(
            delivered_at__gte=start_datetime,
            delivered_at__lt=end_datetime_exclusive,
        )
        | Q(
            delivered_at__isnull=True,
            shipping_order__delivered_at__gte=start_datetime,
            shipping_order__delivered_at__lt=end_datetime_exclusive,
        )
        | Q(
            delivered_at__isnull=True,
            shipping_order__delivered_at__isnull=True,
            updated_at__gte=start_datetime,
            updated_at__lt=end_datetime_exclusive,
        )
    )

    delivered_fulfillments = list(
        OrderFulfillment.objects.filter(
            company=company,
            warehouse_id__in=accessible_ids,
            status=OrderFulfillment.STATUS_DELIVERED,
        )
        .filter(delivered_filter)
        .select_related(
            "order",
            "warehouse",
            "shipping_order",
        )
    )

    delivered_order_ids = {
        fulfillment.order_id
        for fulfillment in delivered_fulfillments
    }

    delivered_relation_data = _order_relation_data(
        company=company,
        order_ids=delivered_order_ids,
        accessible_warehouse_ids=accessible_ids,
    )

    delivered_orders_map = {
        order.id: order
        for order in Order.objects.filter(
            company=company,
            id__in=delivered_order_ids,
        )
    }

    applied_recognized_pairs = set()
    scoped_delivered_order_ids = set()

    for fulfillment in delivered_fulfillments:
        warehouse_id = fulfillment.warehouse_id

        if warehouse_id not in selected_ids:
            continue

        order = delivered_orders_map.get(
            fulfillment.order_id
        )

        if not order:
            continue

        pair = (order.id, warehouse_id)

        if pair in applied_recognized_pairs:
            continue

        weights = _weights_for_order(
            order.id,
            delivered_relation_data,
        )
        weight = weights.get(warehouse_id)

        if not weight:
            continue

        delivered_at = (
            fulfillment.delivered_at
            or getattr(
                getattr(fulfillment, "shipping_order", None),
                "delivered_at",
                None,
            )
            or fulfillment.updated_at
        )

        _apply_order_financials(
            order=order,
            weights={warehouse_id: weight},
            selected_ids=selected_ids,
            warehouse_rows=warehouse_rows,
            recognized=True,
            recognized_date=delivered_at.date(),
        )

        scoped_delivered_order_ids.add(order.id)
        applied_recognized_pairs.add(pair)

    legacy_delivered_shippings = list(
        ShippingOrder.objects.filter(
            company=company,
            fulfillment__isnull=True,
            status=ShippingOrder.STATUS_DELIVERED,
            delivered_at__gte=start_datetime,
            delivered_at__lt=end_datetime_exclusive,
        ).select_related(
            "order",
            "packing_order",
            "packing_order__picking_order",
            "packing_order__picking_order__warehouse",
        )
    )

    legacy_order_ids = {
        shipping.order_id
        for shipping in legacy_delivered_shippings
    }

    legacy_relation_data = _order_relation_data(
        company=company,
        order_ids=legacy_order_ids,
        accessible_warehouse_ids=accessible_ids,
    )

    for shipping in legacy_delivered_shippings:
        direct_warehouse_id = getattr(
            getattr(
                getattr(shipping, "packing_order", None),
                "picking_order",
                None,
            ),
            "warehouse_id",
            None,
        )

        weights = _weights_for_order(
            shipping.order_id,
            legacy_relation_data,
        )

        if not weights and direct_warehouse_id in accessible_ids:
            weights = {direct_warehouse_id: Decimal("1")}

        for warehouse_id, weight in weights.items():
            if warehouse_id not in selected_ids:
                continue

            pair = (shipping.order_id, warehouse_id)

            if pair in applied_recognized_pairs:
                continue

            _apply_order_financials(
                order=shipping.order,
                weights={warehouse_id: weight},
                selected_ids=selected_ids,
                warehouse_rows=warehouse_rows,
                recognized=True,
                recognized_date=shipping.delivered_at.date(),
            )

            scoped_delivered_order_ids.add(shipping.order_id)
            applied_recognized_pairs.add(pair)

    delivered_selected_orders = [
        order
        for order in selected_orders
        if order.status == "delivered"
        and order.id in scoped_registered_order_ids
    ]

    _product_rows_for_orders(
        company=company,
        orders=delivered_selected_orders,
        relation_data=relation_data,
        selected_ids=selected_ids,
        warehouse_rows=warehouse_rows,
    )

    payment_date_field = _safe_payment_date_field()
    approved_status = getattr(
        Payment,
        "STATUS_APPROVED",
        "approved",
    )

    payments = list(
        Payment.objects.filter(
            company=company,
            status=approved_status,
        )
        .filter(
            _build_period_filter(
                field_name=payment_date_field,
                start_datetime=start_datetime,
                end_datetime_exclusive=end_datetime_exclusive,
            )
        )
        .select_related("order")
    )

    payment_order_ids = {
        payment.order_id
        for payment in payments
    }

    payment_relation_data = _order_relation_data(
        company=company,
        order_ids=payment_order_ids,
        accessible_warehouse_ids=accessible_ids,
    )

    scoped_payment_ids = set()

    for payment in payments:
        weights = _weights_for_order(
            payment.order_id,
            payment_relation_data,
        )

        payment_date = (
            getattr(payment, payment_date_field, None)
            or payment.created_at
        ).date()

        for warehouse_id, weight in weights.items():
            if warehouse_id not in selected_ids:
                continue

            row = warehouse_rows[warehouse_id]
            row["payment_ids"].add(payment.id)
            row["cash_collected"] += decimal_value(payment.amount) * weight
            row["daily_collections"][payment_date] += (
                decimal_value(payment.amount) * weight
            )
            scoped_payment_ids.add(payment.id)

    purchase_orders = list(
        PurchaseOrder.objects.filter(
            company=company,
            warehouse_id__in=selected_ids,
            created_at__gte=start_datetime,
            created_at__lt=end_datetime_exclusive,
        ).exclude(status=PurchaseOrder.STATUS_CANCELLED)
    )

    for purchase_order in purchase_orders:
        row = warehouse_rows.get(
            purchase_order.warehouse_id
        )

        if not row:
            continue

        row["purchase_orders"] += 1
        row["purchases_total"] += decimal_value(
            purchase_order.total
        )

    supplier_payment_filter = Q(
        payment_date__gte=start_datetime,
        payment_date__lt=end_datetime_exclusive,
    ) | Q(
        payment_date__isnull=True,
        created_at__gte=start_datetime,
        created_at__lt=end_datetime_exclusive,
    )

    supplier_payments = SupplierPayment.objects.filter(
        company=company,
        status=SupplierPayment.STATUS_APPROVED,
        purchase_order__warehouse_id__in=selected_ids,
    ).filter(supplier_payment_filter)

    for supplier_payment in supplier_payments.select_related(
        "purchase_order"
    ):
        row = warehouse_rows.get(
            supplier_payment.purchase_order.warehouse_id
        )

        if row:
            row["supplier_payments"] += decimal_value(
                supplier_payment.amount
            )

    active_fulfillment_statuses = {
        OrderFulfillment.STATUS_PENDING,
        OrderFulfillment.STATUS_RESERVED,
        OrderFulfillment.STATUS_PICKING,
        OrderFulfillment.STATUS_PACKED,
        OrderFulfillment.STATUS_SHIPPED,
        OrderFulfillment.STATUS_INCIDENT,
    }

    fulfillments = OrderFulfillment.objects.filter(
        company=company,
        warehouse_id__in=selected_ids,
    ).values_list("warehouse_id", "status")

    for warehouse_id, status in fulfillments:
        row = warehouse_rows.get(warehouse_id)

        if not row:
            continue

        if status in active_fulfillment_statuses:
            row["active_fulfillments"] += 1

        if status == OrderFulfillment.STATUS_DELIVERED:
            row["delivered_fulfillments"] += 1

    for warehouse_id, status in PickingOrder.objects.filter(
        company=company,
        warehouse_id__in=selected_ids,
    ).values_list("warehouse_id", "status"):
        row = warehouse_rows.get(warehouse_id)

        if not row:
            continue

        if status in {
            PickingOrder.STATUS_PENDING,
            PickingOrder.STATUS_IN_PROGRESS,
        }:
            row["pending_pickings"] += 1

        if status == PickingOrder.STATUS_COMPLETED:
            row["completed_pickings"] += 1

    packings = PackingOrder.objects.filter(
        company=company,
    ).filter(
        Q(fulfillment__warehouse_id__in=selected_ids)
        | Q(
            fulfillment__isnull=True,
            picking_order__warehouse_id__in=selected_ids,
        )
    ).select_related(
        "fulfillment",
        "picking_order",
    )

    for packing in packings:
        warehouse_id = (
            packing.fulfillment.warehouse_id
            if packing.fulfillment_id
            else getattr(packing.picking_order, "warehouse_id", None)
        )
        row = warehouse_rows.get(warehouse_id)

        if not row:
            continue

        if packing.status == PackingOrder.STATUS_PENDING:
            row["pending_packings"] += 1

        if packing.status == PackingOrder.STATUS_PACKED:
            row["packed_packings"] += 1

    shippings = ShippingOrder.objects.filter(
        company=company,
    ).filter(
        Q(fulfillment__warehouse_id__in=selected_ids)
        | Q(
            fulfillment__isnull=True,
            packing_order__picking_order__warehouse_id__in=selected_ids,
        )
    ).select_related(
        "fulfillment",
        "packing_order",
        "packing_order__picking_order",
    )

    for shipping in shippings:
        warehouse_id = (
            shipping.fulfillment.warehouse_id
            if shipping.fulfillment_id
            else getattr(
                getattr(shipping.packing_order, "picking_order", None),
                "warehouse_id",
                None,
            )
        )
        row = warehouse_rows.get(warehouse_id)

        if not row:
            continue

        if shipping.status in {
            ShippingOrder.STATUS_PENDING,
            ShippingOrder.STATUS_READY,
        }:
            row["pending_shippings"] += 1

        if shipping.status == ShippingOrder.STATUS_SHIPPED:
            row["shipped_shippings"] += 1

        if shipping.status == ShippingOrder.STATUS_DELIVERED:
            row["delivered_shippings"] += 1

    sales_internal_by_warehouse_sku = defaultdict(
        lambda: defaultdict(Decimal)
    )

    for warehouse_id, row in warehouse_rows.items():
        for sku, product in row["products"].items():
            sales_internal_by_warehouse_sku[warehouse_id][sku] += decimal_value(
                product["internal_units"]
            )

    stock_risk_by_warehouse = []
    aggregate_stock_by_sku = defaultdict(
        lambda: {
            "sku": "",
            "product_name": "",
            "physical": Decimal("0"),
            "reserved": Decimal("0"),
            "committed": Decimal("0"),
            "available": Decimal("0"),
            "incoming_15d": Decimal("0"),
            "overdue_purchase": Decimal("0"),
            "sold_internal": Decimal("0"),
        }
    )

    period_days = max(
        int(resolved_period.get("days", 1) or 1),
        1,
    )

    forecast_horizon_days = 15
    forecast_start_date = timezone.localdate()
    forecast_end_date = (
        forecast_start_date
        + timedelta(days=forecast_horizon_days)
    )

    incoming_15d_by_warehouse_variant = defaultdict(
        lambda: defaultdict(Decimal)
    )
    overdue_incoming_by_warehouse_variant = defaultdict(
        lambda: defaultdict(Decimal)
    )
    undated_open_purchase_items = 0

    open_purchase_statuses = [
        PurchaseOrder.STATUS_SENT,
        PurchaseOrder.STATUS_PARTIALLY_RECEIVED,
    ]

    incoming_items = (
        PurchaseOrderItem.objects.filter(
            purchase_order__company=company,
            purchase_order__warehouse_id__in=selected_ids,
            purchase_order__status__in=open_purchase_statuses,
        )
        .select_related(
            "purchase_order",
            "purchase_order__warehouse",
            "variant",
        )
    )

    for purchase_item in incoming_items:
        purchase_order = purchase_item.purchase_order
        expected_date = purchase_order.expected_date
        pending_quantity = max(
            decimal_value(purchase_item.quantity_ordered)
            - decimal_value(purchase_item.quantity_received),
            Decimal("0"),
        )

        if pending_quantity <= 0:
            continue

        if not expected_date:
            undated_open_purchase_items += 1
            continue

        warehouse_id = purchase_order.warehouse_id
        variant_id = purchase_item.variant_id

        if expected_date < forecast_start_date:
            overdue_incoming_by_warehouse_variant[
                warehouse_id
            ][variant_id] += pending_quantity
            continue

        if expected_date <= forecast_end_date:
            incoming_15d_by_warehouse_variant[
                warehouse_id
            ][variant_id] += pending_quantity

    stocks = Stock.objects.filter(
        company=company,
        warehouse_id__in=selected_ids,
    ).select_related(
        "warehouse",
        "variant",
        "variant__product",
    )

    for stock in stocks:
        warehouse_id = stock.warehouse_id
        row = warehouse_rows[warehouse_id]
        physical = decimal_value(stock.physical_stock)
        reserved = decimal_value(stock.reserved_stock)
        committed = decimal_value(stock.committed_stock)
        available = physical - reserved - committed
        sku = stock.variant.sku or f"SKU-{stock.variant_id}"
        product_name = getattr(
            getattr(stock.variant, "product", None),
            "name",
            "Sin producto",
        )
        sold_internal = sales_internal_by_warehouse_sku[warehouse_id].get(
            sku,
            Decimal("0"),
        )
        average_daily = sold_internal / Decimal(period_days)
        incoming_15d = incoming_15d_by_warehouse_variant[
            warehouse_id
        ].get(
            stock.variant_id,
            Decimal("0"),
        )
        overdue_purchase = overdue_incoming_by_warehouse_variant[
            warehouse_id
        ].get(
            stock.variant_id,
            Decimal("0"),
        )
        expected_demand_15d = (
            average_daily
            * Decimal(forecast_horizon_days)
        )
        projected_without_incoming_15d = (
            available
            - expected_demand_15d
        )
        projected_with_incoming_15d = (
            available
            + incoming_15d
            - expected_demand_15d
        )

        if average_daily > 0:
            days_remaining = max(
                available,
                Decimal("0"),
            ) / average_daily
            suggested_reorder = max(
                math.ceil(
                    average_daily * Decimal("30")
                    - available
                    - incoming_15d
                ),
                0,
            )
        else:
            days_remaining = None
            suggested_reorder = 0

        out_of_stock = available <= 0
        projected_stockout_15d = bool(
            out_of_stock
            or (
                average_daily > 0
                and projected_with_incoming_15d < 0
            )
        )
        incoming_covers_horizon = bool(
            average_daily > 0
            and projected_without_incoming_15d < 0
            and projected_with_incoming_15d >= 0
        )

        if out_of_stock:
            risk = "critico"
        elif (
            days_remaining is not None
            and days_remaining <= 7
            and projected_stockout_15d
        ):
            risk = "critico"
        elif projected_stockout_15d:
            risk = "alto"
        elif incoming_covers_horizon:
            risk = "medio"
        elif days_remaining is not None and days_remaining <= 30:
            risk = "medio"
        elif available <= decimal_value(
            getattr(stock.variant, "min_stock", 0)
        ):
            risk = "medio"
        else:
            risk = "bajo"

        row["physical_stock"] += physical
        row["reserved_stock"] += reserved
        row["committed_stock"] += committed
        row["available_stock"] += available
        row["incoming_stock_15d"] += incoming_15d
        row["overdue_purchase_stock"] += overdue_purchase

        if out_of_stock:
            row["out_of_stock_skus"] += 1
        elif risk == "critico":
            row["critical_skus"] += 1

        if risk == "alto":
            row["high_risk_skus"] += 1

        if projected_stockout_15d:
            row["projected_stockout_15d_skus"] += 1

        if risk != "bajo" or sold_internal > 0 or incoming_15d > 0:
            stock_risk_by_warehouse.append(
                {
                    "warehouse_key": f"WH-{warehouse_id}",
                    "warehouse_id": warehouse_id,
                    "warehouse_name": stock.warehouse.name,
                    "sku": sku,
                    "product_name": product_name,
                    "physical_internal": integer(physical),
                    "reserved_internal": integer(reserved),
                    "committed_internal": integer(committed),
                    "available_internal": integer(available),
                    "incoming_internal_15d": integer(incoming_15d),
                    "overdue_purchase_internal": integer(overdue_purchase),
                    "expected_demand_internal_15d": number(
                        expected_demand_15d
                    ),
                    "projected_without_incoming_internal_15d": number(
                        projected_without_incoming_15d
                    ),
                    "projected_available_internal_15d": number(
                        projected_with_incoming_15d
                    ),
                    "projected_stockout_15d": projected_stockout_15d,
                    "incoming_covers_horizon": incoming_covers_horizon,
                    "sold_internal_selected_period": number(sold_internal),
                    "period_days": period_days,
                    "forecast_horizon_days": forecast_horizon_days,
                    "average_daily_internal": number(average_daily),
                    "days_remaining": (
                        number(days_remaining)
                        if days_remaining is not None
                        else None
                    ),
                    "suggested_reorder_internal": int(suggested_reorder),
                    "calculated_risk": risk,
                }
            )

        aggregate = aggregate_stock_by_sku[sku]
        aggregate["sku"] = sku
        aggregate["product_name"] = product_name
        aggregate["physical"] += physical
        aggregate["reserved"] += reserved
        aggregate["committed"] += committed
        aggregate["available"] += available
        aggregate["incoming_15d"] += incoming_15d
        aggregate["overdue_purchase"] += overdue_purchase
        aggregate["sold_internal"] += sold_internal

    risk_order = {
        "critico": 0,
        "alto": 1,
        "medio": 2,
        "bajo": 3,
    }

    stock_risk_by_warehouse.sort(
        key=lambda item: (
            risk_order.get(item["calculated_risk"], 99),
            item["days_remaining"]
            if item["days_remaining"] is not None
            else 999999,
            item["warehouse_name"],
            item["sku"],
        )
    )

    aggregate_stock_risk = []

    for aggregate in aggregate_stock_by_sku.values():
        average_daily = aggregate["sold_internal"] / Decimal(period_days)
        expected_demand_15d = (
            average_daily
            * Decimal(forecast_horizon_days)
        )
        projected_without_incoming_15d = (
            aggregate["available"]
            - expected_demand_15d
        )
        projected_with_incoming_15d = (
            aggregate["available"]
            + aggregate["incoming_15d"]
            - expected_demand_15d
        )

        if average_daily > 0:
            days_remaining = max(
                aggregate["available"],
                Decimal("0"),
            ) / average_daily
            suggested_reorder = max(
                math.ceil(
                    average_daily * Decimal("30")
                    - aggregate["available"]
                    - aggregate["incoming_15d"]
                ),
                0,
            )
        else:
            days_remaining = None
            suggested_reorder = 0

        out_of_stock = aggregate["available"] <= 0
        projected_stockout_15d = bool(
            out_of_stock
            or (
                average_daily > 0
                and projected_with_incoming_15d < 0
            )
        )
        incoming_covers_horizon = bool(
            average_daily > 0
            and projected_without_incoming_15d < 0
            and projected_with_incoming_15d >= 0
        )

        if out_of_stock:
            risk = "critico"
        elif (
            days_remaining is not None
            and days_remaining <= 7
            and projected_stockout_15d
        ):
            risk = "critico"
        elif projected_stockout_15d:
            risk = "alto"
        elif incoming_covers_horizon:
            risk = "medio"
        elif days_remaining is not None and days_remaining <= 30:
            risk = "medio"
        else:
            risk = "bajo"

        if (
            risk == "bajo"
            and aggregate["sold_internal"] <= 0
            and aggregate["incoming_15d"] <= 0
        ):
            continue

        aggregate_stock_risk.append(
            {
                "sku": aggregate["sku"],
                "product_name": aggregate["product_name"],
                "physical_internal": integer(aggregate["physical"]),
                "reserved_internal": integer(aggregate["reserved"]),
                "committed_internal": integer(aggregate["committed"]),
                "available_internal": integer(aggregate["available"]),
                "incoming_internal_15d": integer(
                    aggregate["incoming_15d"]
                ),
                "overdue_purchase_internal": integer(
                    aggregate["overdue_purchase"]
                ),
                "expected_demand_internal_15d": number(
                    expected_demand_15d
                ),
                "projected_without_incoming_internal_15d": number(
                    projected_without_incoming_15d
                ),
                "projected_available_internal_15d": number(
                    projected_with_incoming_15d
                ),
                "projected_stockout_15d": projected_stockout_15d,
                "incoming_covers_horizon": incoming_covers_horizon,
                "sold_internal_selected_period": number(
                    aggregate["sold_internal"]
                ),
                "sold_internal_last_30_days": number(
                    aggregate["sold_internal"]
                ),
                "period_days": period_days,
                "forecast_horizon_days": forecast_horizon_days,
                "average_daily_internal": number(average_daily),
                "days_remaining": (
                    number(days_remaining)
                    if days_remaining is not None
                    else None
                ),
                "suggested_reorder_internal": int(suggested_reorder),
                "calculated_risk": risk,
            }
        )

    aggregate_stock_risk.sort(
        key=lambda item: (
            risk_order.get(item["calculated_risk"], 99),
            item["days_remaining"]
            if item["days_remaining"] is not None
            else 999999,
        )
    )

    aggregate_products = defaultdict(
        lambda: {
            "sku": "",
            "product_name": "",
            "sales_units": Decimal("0"),
            "internal_units": Decimal("0"),
            "revenue": Decimal("0"),
            "cost": Decimal("0"),
            "profit": Decimal("0"),
        }
    )

    for row in warehouse_rows.values():
        for sku, product in row["products"].items():
            aggregate = aggregate_products[sku]
            aggregate["sku"] = sku
            aggregate["product_name"] = product["product_name"]

            for field_name in [
                "sales_units",
                "internal_units",
                "revenue",
                "cost",
                "profit",
            ]:
                aggregate[field_name] += decimal_value(
                    product[field_name]
                )

    top_products = []

    for product in aggregate_products.values():
        sales_units = product["sales_units"]
        revenue = product["revenue"]
        cost = product["cost"]
        profit = product["profit"]
        average_sale_price = (
            revenue / sales_units
            if sales_units > 0
            else Decimal("0")
        )
        average_unit_cost = (
            cost / sales_units
            if sales_units > 0
            else Decimal("0")
        )
        guardrail = _pricing_guardrail(
            average_sale_price,
            average_unit_cost,
        )

        top_products.append(
            {
                "sku": product["sku"],
                "product_name": product["product_name"],
                "sales_units": number(product["sales_units"]),
                "internal_units": number(product["internal_units"]),
                "revenue": number(revenue),
                "total_cost": number(cost),
                "profit": number(profit),
                "margin_percent": percentage(profit, revenue),
                "average_sale_price": number(average_sale_price),
                "average_unit_cost": number(average_unit_cost),
                **guardrail,
            }
        )

    top_products.sort(
        key=lambda product: (
            product["revenue"],
            product["profit"],
        ),
        reverse=True,
    )
    top_products = top_products[:10]

    stock_map = {
        item["sku"]: item
        for item in aggregate_stock_risk
    }

    commercial_opportunities = []

    for product in top_products:
        stock_info = stock_map.get(
            product["sku"],
            {},
        )
        available = integer(
            stock_info.get("available_internal", 0)
        )
        risk = stock_info.get(
            "calculated_risk",
            "bajo",
        )
        can_promote = (
            product.get("pricing_data_complete", False)
            and available > 0
            and risk not in {"critico", "alto"}
        )

        product["available_internal"] = available
        product["stock_risk_level"] = risk
        product["can_promote"] = can_promote

        if not can_promote:
            continue

        commercial_opportunities.append(
            {
                "sku": product["sku"],
                "product_name": product["product_name"],
                "sales_units_selected_period": product["sales_units"],
                "sales_units_last_30_days": product["sales_units"],
                "internal_units_selected_period": product["internal_units"],
                "internal_units_last_30_days": product["internal_units"],
                "revenue_selected_period": product["revenue"],
                "revenue_last_30_days": product["revenue"],
                "profit_selected_period": product["profit"],
                "profit_last_30_days": product["profit"],
                "average_sale_price": product["average_sale_price"],
                "average_unit_cost": product["average_unit_cost"],
                "current_margin_percent": product["current_margin_percent"],
                "minimum_margin_percent": product["minimum_margin_percent"],
                "minimum_safe_price": product["minimum_safe_price"],
                "max_safe_discount_percent": product["max_safe_discount_percent"],
                "suggested_discount_percent": product["suggested_discount_percent"],
                "available_internal": available,
                "stock_risk_level": risk,
            }
        )

    commercial_opportunities.sort(
        key=lambda opportunity: (
            opportunity["profit_selected_period"],
            opportunity["revenue_selected_period"],
        ),
        reverse=True,
    )
    commercial_opportunities = commercial_opportunities[:8]

    serialized_warehouses = []
    combined_daily_registered = defaultdict(Decimal)
    combined_daily_recognized = defaultdict(Decimal)
    combined_daily_collections = defaultdict(Decimal)
    total_registered_order_ids = set()
    total_delivered_order_ids = set()
    total_payment_ids = set()

    for warehouse in warehouse_objects:
        row = warehouse_rows[warehouse.id]

        for day, value in row["daily_registered_sales"].items():
            combined_daily_registered[day] += value

        for day, value in row["daily_recognized_revenue"].items():
            combined_daily_recognized[day] += value

        for day, value in row["daily_collections"].items():
            combined_daily_collections[day] += value

        total_registered_order_ids.update(
            row["registered_order_ids"]
        )
        total_delivered_order_ids.update(
            row["delivered_order_ids"]
        )
        total_payment_ids.update(
            row["payment_ids"]
        )

        warehouse_products = []

        for product in row["products"].values():
            revenue = product["revenue"]
            profit = product["profit"]
            warehouse_products.append(
                {
                    "sku": product["sku"],
                    "product_name": product["product_name"],
                    "sales_units": number(product["sales_units"]),
                    "internal_units": number(product["internal_units"]),
                    "revenue": number(revenue),
                    "cost": number(product["cost"]),
                    "profit": number(profit),
                    "margin_percent": percentage(profit, revenue),
                }
            )

        warehouse_products.sort(
            key=lambda product: product["revenue"],
            reverse=True,
        )

        serialized_warehouses.append(
            {
                "warehouse_id": warehouse.id,
                "warehouse_key": f"WH-{warehouse.id}",
                "warehouse_name": warehouse.name,
                "warehouse_code": getattr(warehouse, "code", "") or "",
                "registered_orders": len(row["registered_order_ids"]),
                "registered_sales": number(row["registered_sales"]),
                "accounts_receivable": number(row["accounts_receivable"]),
                "registered_costs": number(row["registered_costs"]),
                "registered_profit": number(row["registered_profit"]),
                "registered_margin_percent": percentage(
                    row["registered_profit"],
                    row["registered_sales"],
                ),
                "delivered_orders": len(row["delivered_order_ids"]),
                "recognized_revenue": number(row["recognized_revenue"]),
                "recognized_costs": number(row["recognized_costs"]),
                "recognized_profit": number(row["recognized_profit"]),
                "recognized_margin_percent": percentage(
                    row["recognized_profit"],
                    row["recognized_revenue"],
                ),
                "approved_payments": len(row["payment_ids"]),
                "cash_collected": number(row["cash_collected"]),
                "purchase_orders": row["purchase_orders"],
                "purchases_total": number(row["purchases_total"]),
                "supplier_payments": number(row["supplier_payments"]),
                "active_fulfillments": row["active_fulfillments"],
                "delivered_fulfillments": row["delivered_fulfillments"],
                "pending_pickings": row["pending_pickings"],
                "completed_pickings": row["completed_pickings"],
                "pending_packings": row["pending_packings"],
                "packed_packings": row["packed_packings"],
                "pending_shippings": row["pending_shippings"],
                "shipped_shippings": row["shipped_shippings"],
                "delivered_shippings": row["delivered_shippings"],
                "physical_stock": integer(row["physical_stock"]),
                "reserved_stock": integer(row["reserved_stock"]),
                "committed_stock": integer(row["committed_stock"]),
                "available_stock": integer(row["available_stock"]),
                "incoming_stock_15d": integer(
                    row["incoming_stock_15d"]
                ),
                "overdue_purchase_stock": integer(
                    row["overdue_purchase_stock"]
                ),
                "out_of_stock_skus": row["out_of_stock_skus"],
                "critical_skus": row["critical_skus"],
                "high_risk_skus": row["high_risk_skus"],
                "projected_stockout_15d_skus": row[
                    "projected_stockout_15d_skus"
                ],
                "top_products": warehouse_products[:5],
            }
        )

    total_registered_sales = sum(
        (
            decimal_value(row["registered_sales"])
            for row in warehouse_rows.values()
        ),
        Decimal("0"),
    )
    total_registered_costs = sum(
        (
            decimal_value(row["registered_costs"])
            for row in warehouse_rows.values()
        ),
        Decimal("0"),
    )
    total_registered_profit = sum(
        (
            decimal_value(row["registered_profit"])
            for row in warehouse_rows.values()
        ),
        Decimal("0"),
    )
    total_receivable = sum(
        (
            decimal_value(row["accounts_receivable"])
            for row in warehouse_rows.values()
        ),
        Decimal("0"),
    )
    total_recognized_revenue = sum(
        (
            decimal_value(row["recognized_revenue"])
            for row in warehouse_rows.values()
        ),
        Decimal("0"),
    )
    total_recognized_costs = sum(
        (
            decimal_value(row["recognized_costs"])
            for row in warehouse_rows.values()
        ),
        Decimal("0"),
    )
    total_recognized_profit = sum(
        (
            decimal_value(row["recognized_profit"])
            for row in warehouse_rows.values()
        ),
        Decimal("0"),
    )
    total_cash_collected = sum(
        (
            decimal_value(row["cash_collected"])
            for row in warehouse_rows.values()
        ),
        Decimal("0"),
    )

    rankings = {
        "by_registered_sales": [
            {
                "warehouse_name": row["warehouse_name"],
                "value": row["registered_sales"],
            }
            for row in sorted(
                serialized_warehouses,
                key=lambda item: item["registered_sales"],
                reverse=True,
            )
        ],
        "by_recognized_revenue": [
            {
                "warehouse_name": row["warehouse_name"],
                "value": row["recognized_revenue"],
            }
            for row in sorted(
                serialized_warehouses,
                key=lambda item: item["recognized_revenue"],
                reverse=True,
            )
        ],
        "by_profit": [
            {
                "warehouse_name": row["warehouse_name"],
                "value": row["recognized_profit"],
            }
            for row in sorted(
                serialized_warehouses,
                key=lambda item: item["recognized_profit"],
                reverse=True,
            )
        ],
        "by_available_stock": [
            {
                "warehouse_name": row["warehouse_name"],
                "value": row["available_stock"],
            }
            for row in sorted(
                serialized_warehouses,
                key=lambda item: item["available_stock"],
                reverse=True,
            )
        ],
        "by_out_of_stock": [
            {
                "warehouse_name": row["warehouse_name"],
                "value": row["out_of_stock_skus"],
            }
            for row in sorted(
                serialized_warehouses,
                key=lambda item: item["out_of_stock_skus"],
                reverse=True,
            )
        ],
        "by_critical_stock": [
            {
                "warehouse_name": row["warehouse_name"],
                "value": (
                    row["out_of_stock_skus"]
                    + row["critical_skus"]
                ),
            }
            for row in sorted(
                serialized_warehouses,
                key=lambda item: (
                    item["out_of_stock_skus"]
                    + item["critical_skus"]
                ),
                reverse=True,
            )
        ],
        "by_stockout_risk": [
            {
                "warehouse_name": row["warehouse_name"],
                "value": row["projected_stockout_15d_skus"],
            }
            for row in sorted(
                serialized_warehouses,
                key=lambda item: item["projected_stockout_15d_skus"],
                reverse=True,
            )
        ],
    }

    charts = {
        "registered_sales": _chart_from_daily(
            daily_totals=combined_daily_registered,
            start_date=start_date,
            end_date=end_date,
            title=f"Ventas registradas · {warehouse_scope['label']}",
            metric="Ventas registradas en MXN",
        ),
        "recognized_revenue": _chart_from_daily(
            daily_totals=combined_daily_recognized,
            start_date=start_date,
            end_date=end_date,
            title=f"Ingresos reconocidos · {warehouse_scope['label']}",
            metric="Ingresos reconocidos en MXN",
        ),
        "collections": _chart_from_daily(
            daily_totals=combined_daily_collections,
            start_date=start_date,
            end_date=end_date,
            title=f"Cobranza · {warehouse_scope['label']}",
            metric="Cobrado en MXN",
        ),
    }

    warnings = []

    if warehouse_scope.get("truncated_to_limit"):
        warnings.append(
            "La cuenta tiene más de 10 bodegas accesibles; "
            "el análisis se limitó a las primeras 10 permitidas."
        )

    if undated_open_purchase_items:
        warnings.append(
            f"{undated_open_purchase_items} partidas de compra abiertas "
            "no tienen fecha esperada y no se incluyeron como entradas "
            "confirmadas del pronóstico a 15 días."
        )

    return {
        "scope": warehouse_scope,
        "summary": {
            "registered_orders": len(total_registered_order_ids),
            "registered_sales": number(total_registered_sales),
            "accounts_receivable_from_registered_orders": number(total_receivable),
            "expected_costs_from_registered_orders": number(total_registered_costs),
            "expected_profit_from_registered_orders": number(total_registered_profit),
            "delivered_orders": len(total_delivered_order_ids),
            "recognized_revenue": number(total_recognized_revenue),
            "recognized_cost_of_sales": number(total_recognized_costs),
            "recognized_gross_profit": number(total_recognized_profit),
            "recognized_margin_percent": percentage(
                total_recognized_profit,
                total_recognized_revenue,
            ),
            "approved_payments": len(total_payment_ids),
            "cash_collected": number(total_cash_collected),
            "cash_collected_vs_registered_sales_percent": percentage(
                total_cash_collected,
                total_registered_sales,
            ),
            "out_of_stock_skus": sum(
                row["out_of_stock_skus"]
                for row in serialized_warehouses
            ),
            "critical_skus": sum(
                row["critical_skus"]
                for row in serialized_warehouses
            ),
            "high_risk_skus": sum(
                row["high_risk_skus"]
                for row in serialized_warehouses
            ),
            "projected_stockout_15d_skus": sum(
                row["projected_stockout_15d_skus"]
                for row in serialized_warehouses
            ),
            "incoming_stock_15d": sum(
                row["incoming_stock_15d"]
                for row in serialized_warehouses
            ),
            "forecast_horizon_days": forecast_horizon_days,
        },
        "warehouses": serialized_warehouses,
        "rankings": rankings,
        "top_products_selected_period": top_products,
        "stock_risk": aggregate_stock_risk[:12],
        "stock_risk_by_warehouse": stock_risk_by_warehouse[:30],
        "commercial_opportunities": commercial_opportunities,
        "charts": charts,
        "warnings": warnings,
        "attribution_method": {
            "sales": (
                "OrderItemAllocation por bodega; fallback a fulfillment/picking."
            ),
            "payments": (
                "Pago distribuido por la participación financiera del pedido."
            ),
            "multi_warehouse_orders": (
                "Un pedido multi-bodega se reparte proporcionalmente; no se duplica."
            ),
            "stockout_forecast": (
                "Velocidad diaria del periodo consultado, stock disponible, "
                "reservado, comprometido y compras abiertas con fecha esperada "
                "dentro de 15 días."
            ),
        },
        "company_level_only": [
            "pipeline CRM",
            "deals sin bodega asignada",
        ],
    }


def apply_warehouse_analysis_to_snapshot(
    *,
    snapshot,
    warehouse_analysis,
):
    payload = snapshot["payload"]
    scope = warehouse_analysis.get("scope", {})
    summary = warehouse_analysis.get("summary", {})

    payload["warehouse_scope"] = scope
    payload["warehouse_analysis"] = warehouse_analysis

    if not summary:
        return snapshot

    original_summary = payload.get(
        "selected_period_summary",
        {},
    )

    scoped_summary = {
        **original_summary,
        **summary,
        "scope_label": scope.get("label", ""),
        "scope_mode": scope.get("mode", "all"),
        "warehouse_count": scope.get("analyzed_count", 0),
        "orders": summary.get("registered_orders", 0),
        "sales": summary.get("registered_sales", 0),
        "collected": summary.get("cash_collected", 0),
        "accounts_receivable": summary.get(
            "accounts_receivable_from_registered_orders",
            0,
        ),
        "cost_of_sales": summary.get(
            "recognized_cost_of_sales",
            0,
        ),
        "gross_profit": summary.get(
            "recognized_gross_profit",
            0,
        ),
        "margin_percent": summary.get(
            "recognized_margin_percent",
            0,
        ),
        "collection_percent": summary.get(
            "cash_collected_vs_registered_sales_percent",
            0,
        ),
    }

    payload["selected_period_summary"] = scoped_summary

    charts = warehouse_analysis.get("charts", {})

    if charts.get("registered_sales"):
        payload["period_registered_sales_chart"] = charts[
            "registered_sales"
        ]
        payload["period_sales_chart"] = charts[
            "registered_sales"
        ]

    if charts.get("recognized_revenue"):
        payload["period_recognized_revenue_chart"] = charts[
            "recognized_revenue"
        ]

    if charts.get("collections"):
        payload["period_collections_chart"] = charts[
            "collections"
        ]

    payload["top_products_selected_period"] = warehouse_analysis.get(
        "top_products_selected_period",
        [],
    )
    payload["top_products_last_30_days"] = payload[
        "top_products_selected_period"
    ]
    scoped_stock_risk = warehouse_analysis.get(
        "stock_risk_by_warehouse",
        [],
    )

    if not scoped_stock_risk:
        scoped_stock_risk = warehouse_analysis.get(
            "stock_risk",
            [],
        )

    payload["stock_risk"] = scoped_stock_risk[:12]
    payload["stock_risk_by_warehouse"] = warehouse_analysis.get(
        "stock_risk_by_warehouse",
        [],
    )
    payload["commercial_opportunities"] = warehouse_analysis.get(
        "commercial_opportunities",
        [],
    )

    stock_local_map = {}
    stock_candidates_by_sku = defaultdict(list)

    for item in payload["stock_risk"]:
        sku = item.get("sku")

        if not sku:
            continue

        warehouse_key = item.get("warehouse_key")

        if warehouse_key:
            stock_local_map[
                f"{warehouse_key}|{sku}"
            ] = item

        stock_candidates_by_sku[sku].append(item)

    for sku, candidates in stock_candidates_by_sku.items():
        if len(candidates) == 1:
            stock_local_map[sku] = candidates[0]

    snapshot["stock_local_map"] = stock_local_map

    payload.setdefault("data_quality", {}).update(
        {
            "warehouse_scope_applied": True,
            "warehouse_scope_mode": scope.get("mode"),
            "warehouse_count_analyzed": scope.get(
                "analyzed_count",
                0,
            ),
            "warehouse_limit": MAX_AI_WAREHOUSES,
            "orders_allocated_without_duplication": True,
        }
    )

    return snapshot


def question_needs_warehouse_comparison_graph(
    *,
    question,
    warehouse_scope,
):
    normalized_question = normalize_text(question)

    stock_comparison_topic = any(
        keyword in normalized_question
        for keyword in [
            "stock critico",
            "inventario critico",
            "agotado",
            "agotados",
            "quiebre",
            "stockout",
            "se agotara",
            "se agotaran",
            "proximos 15 dias",
            "15 dias",
            "riesgo de agotamiento",
        ]
    )

    return bool(
        warehouse_scope.get("analyzed_count", 0) > 1
        and (
            warehouse_scope.get("comparison_requested")
            or warehouse_scope.get("warehouse_topic_detected")
            or stock_comparison_topic
            or _contains_phrase(
                normalized_question,
                WAREHOUSE_TOPIC_PHRASES,
            )
        )
    )


def build_warehouse_comparison_graph(
    *,
    warehouse_analysis,
    question="",
):
    warehouses = warehouse_analysis.get(
        "warehouses",
        [],
    )

    if not warehouses:
        return None

    normalized_question = normalize_text(question)
    labels = [
        row["warehouse_name"]
        for row in warehouses
    ]

    asks_out_of_stock = any(
        keyword in normalized_question
        for keyword in [
            "agotado",
            "agotados",
            "sin stock",
            "stock en cero",
            "existencia cero",
        ]
    )
    asks_critical_stock = any(
        keyword in normalized_question
        for keyword in [
            "stock critico",
            "inventario critico",
            "existencia critica",
            "productos criticos",
            "sku criticos",
        ]
    )
    asks_projected_stockout = any(
        keyword in normalized_question
        for keyword in [
            "quiebre",
            "stockout",
            "se agotara",
            "se agotaran",
            "va a agotar",
            "van a agotar",
            "proximos 15 dias",
            "15 dias",
            "riesgo de agotamiento",
            "riesgo de quiebre",
        ]
    )

    # La intención específica siempre tiene prioridad sobre la palabra
    # genérica "stock". Así una pregunta de stock crítico nunca termina
    # mostrando inventario total disponible.
    if asks_out_of_stock:
        values = [
            row.get("out_of_stock_skus", 0)
            for row in warehouses
        ]

        return {
            "tipo_grafica": "bar",
            "etiquetas": labels,
            "valores": values,
            "titulo": "Productos agotados por bodega",
            "titulo_eje": "SKUs con disponibilidad igual o menor a cero",
        }

    if asks_critical_stock:
        values = [
            row.get("out_of_stock_skus", 0)
            + row.get("critical_skus", 0)
            for row in warehouses
        ]

        return {
            "tipo_grafica": "bar",
            "etiquetas": labels,
            "valores": values,
            "titulo": "Productos agotados o críticos por bodega",
            "titulo_eje": "SKUs agotados o con cobertura crítica",
        }

    if asks_projected_stockout:
        values = [
            row.get("projected_stockout_15d_skus", 0)
            for row in warehouses
        ]

        return {
            "tipo_grafica": "bar",
            "etiquetas": labels,
            "valores": values,
            "titulo": "Quiebres actuales o proyectados a 15 días por bodega",
            "titulo_eje": "SKUs sin cobertura actual o proyectada",
        }

    if any(
        keyword in normalized_question
        for keyword in [
            "utilidad",
            "ganancia",
            "rentable",
            "rentabilidad",
            "margen",
        ]
    ):
        field = "recognized_profit"
        title = "Utilidad reconocida por bodega"
        axis = "Utilidad en MXN"

    elif any(
        keyword in normalized_question
        for keyword in [
            "ingreso",
            "ingresos",
            "entregado",
            "entregada",
        ]
    ):
        field = "recognized_revenue"
        title = "Ingresos reconocidos por bodega"
        axis = "Ingresos en MXN"

    elif any(
        keyword in normalized_question
        for keyword in [
            "cobrado",
            "cobranza",
            "pago",
            "pagos",
        ]
    ):
        field = "cash_collected"
        title = "Cobranza por bodega"
        axis = "Cobrado en MXN"

    elif any(
        keyword in normalized_question
        for keyword in [
            "stock",
            "inventario",
            "existencia",
            "disponible",
        ]
    ):
        field = "available_stock"
        title = "Stock disponible por bodega"
        axis = "Piezas internas disponibles"

    else:
        field = "registered_sales"
        title = "Ventas registradas por bodega"
        axis = "Ventas registradas en MXN"

    return {
        "tipo_grafica": "bar",
        "etiquetas": labels,
        "valores": [
            row.get(field, 0)
            for row in warehouses
        ],
        "titulo": title,
        "titulo_eje": axis,
    }
