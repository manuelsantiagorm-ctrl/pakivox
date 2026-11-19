from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from inventory.models import (
    Stock,
    ProductVariant,
    ProductLot,
    LotBalance,
)
from warehouse.models import (
    OrderFulfillment,
    OrderItemAllocation,
    PickingItem,
    PickingOrder,
    VariantLocation,
    WarehouseLocation,
)
from warehouse.services.stock_reservation import (
    convert_order_item_quantity_to_internal,
)

MAX_PLANNING_NODES = 50000


def _sync_sale_financial_attribution(order, user=None):
    """Sincroniza el libro dentro de la transacción del plan persistido."""
    from dashboard.services.warehouse_financial_attribution import (
        allocation_signature,
        attribute_order_sale,
    )

    signature = allocation_signature(order)
    return attribute_order_sale(
        company_id=order.company_id,
        order_id=order.id,
        source_event_type="fulfillment_plan",
        source_event_id=f"order:{order.id}:allocation:{signature}",
        actor=user,
        occurred_at=timezone.now(),
    )


def _authenticated_user(user):
    if not user:
        return None

    if not getattr(user, "is_authenticated", True):
        return None

    return user


def _decimal_value(value):
    try:
        return Decimal(str(value or 0))
    except Exception as exc:
        raise ValidationError(f"Valor numérico inválido: {value}.") from exc


def _internal_integer(value, reference=""):
    value_decimal = _decimal_value(value)

    if value_decimal < 0:
        return 0

    if value_decimal != value_decimal.to_integral_value():
        raise ValidationError(f"La cantidad interna debe ser entera{reference}.")

    return int(value_decimal)


def _order_has_field(order, field_name):
    return any(field.name == field_name for field in order._meta.fields)


def _get_order_quantity(order_item):
    if hasattr(order_item, "quantity"):
        return order_item.quantity

    if hasattr(order_item, "cantidad"):
        return order_item.cantidad

    raise ValidationError(
        f"No se encontró la cantidad de la línea " f"#{order_item.id}."
    )


def _validate_order(order):
    if not order:
        raise ValidationError("Debes proporcionar un pedido.")

    if not order.company_id:
        raise ValidationError("El pedido no tiene empresa asignada.")

    if getattr(order, "status", "") == "cancelled":
        raise ValidationError("No se puede planificar un pedido cancelado.")

    return order


def _load_requirements(order):
    """
    Convierte las líneas comerciales del pedido a cantidades internas.

    La primera versión no divide una misma línea entre varias bodegas.
    """

    _validate_order(order)

    order_items = list(
        order.items.select_related(
            "order",
            "variant",
            "variant__company",
            "variant__product",
        ).order_by("id")
    )

    if not order_items:
        raise ValidationError("El pedido no tiene productos para planificar.")

    requirements = []

    for order_item in order_items:
        variant = order_item.variant

        if not variant:
            raise ValidationError(f"La línea #{order_item.id} no tiene variante.")

        if order_item.order_id != order.id:
            raise ValidationError(
                f"La línea #{order_item.id} pertenece " "a otro pedido."
            )

        if order_item.order.company_id != order.company_id:
            raise ValidationError(
                f"La línea #{order_item.id} pertenece " "a otra empresa."
            )

        if variant.company_id != order.company_id:
            raise ValidationError(f"El SKU {variant.sku} pertenece " "a otra empresa.")

        if getattr(variant, "is_bundle", False):
            raise ValidationError(
                f"El SKU {variant.sku} es un paquete virtual. "
                "La planificación multibodega de bundles se "
                "habilitará cuando conectemos sus componentes."
            )

        commercial_quantity = _decimal_value(_get_order_quantity(order_item))

        if commercial_quantity <= 0:
            raise ValidationError(
                f"La línea #{order_item.id} tiene " "cantidad comercial inválida."
            )

        internal_quantity = convert_order_item_quantity_to_internal(
            variant=variant,
            quantity=commercial_quantity,
        )

        internal_quantity = _internal_integer(
            internal_quantity,
            reference=f" en línea #{order_item.id}",
        )

        if internal_quantity <= 0:
            raise ValidationError(
                f"La línea #{order_item.id} genera " "una cantidad interna inválida."
            )

        requirements.append(
            {
                "order_item": order_item,
                "order_item_id": order_item.id,
                "variant": variant,
                "variant_id": variant.id,
                "commercial_quantity": commercial_quantity,
                "internal_quantity": internal_quantity,
            }
        )

    return requirements



def _is_lot_controlled_variant(variant):
    return (
        getattr(
            variant,
            "inventory_control",
            ProductVariant.INVENTORY_CONTROL_NORMAL,
        )
        != ProductVariant.INVENTORY_CONTROL_NORMAL
    )


def _load_controlled_lot_availability(
    *,
    company_id,
    warehouse_ids,
    variants,
    lock=False,
):
    """
    Disponibilidad vendible para SKU LOT / LOT_EXPIRY.

    No usa el total de VariantLocation como fuente final: el límite es la
    suma de LotBalance AVAILABLE cuyo lote esté activo y no caducado en una
    ubicación pickeable.
    """
    controlled = {
        variant.id: variant
        for variant in variants
        if _is_lot_controlled_variant(variant)
    }
    if not controlled or not warehouse_ids:
        return {}

    lot_query = ProductLot.objects.filter(
        company_id=company_id,
        variant_id__in=sorted(controlled),
        status=ProductLot.STATUS_ACTIVE,
    ).order_by("variant_id", "id")
    if lock:
        lot_query = lot_query.select_for_update()

    lots = list(lot_query)
    lot_ids = [lot.id for lot in lots if lot.is_allocatable]
    allocatable_lot_ids = set(lot_ids)

    if not allocatable_lot_ids:
        return {}

    location_query = WarehouseLocation.objects.filter(
        company_id=company_id,
        warehouse_id__in=sorted(warehouse_ids),
        is_active=True,
        is_virtual=False,
        status=WarehouseLocation.STATUS_ACTIVE,
    ).order_by("warehouse_id", "id")
    if lock:
        location_query = location_query.select_for_update()
    location_ids = list(
        location_query.values_list("id", flat=True)
    )
    if not location_ids:
        return {}

    balance_query = LotBalance.objects.filter(
        company_id=company_id,
        lot_id__in=sorted(allocatable_lot_ids),
        location_id__in=location_ids,
        status=LotBalance.STATUS_AVAILABLE,
    ).select_related(
        "lot",
        "location",
    ).order_by("lot_id", "location_id", "id")

    if lock:
        # select_related sólo usa relaciones no-null; aun así bloqueamos
        # explícitamente filas base por IDs para mantener el lock acotado.
        balance_ids = list(
            LotBalance.objects.select_for_update()
            .filter(
                company_id=company_id,
                lot_id__in=sorted(allocatable_lot_ids),
                location_id__in=location_ids,
                status=LotBalance.STATUS_AVAILABLE,
            )
            .order_by("lot_id", "location_id", "id")
            .values_list("id", flat=True)
        )
        balance_query = balance_query.filter(id__in=balance_ids)

    availability = defaultdict(int)

    for balance in balance_query:
        variant = controlled.get(balance.lot.variant_id)
        if variant is None:
            continue
        if not balance.lot.is_allocatable:
            continue
        if not balance.location.is_pickable:
            continue

        available = max(int(balance.available_quantity), 0)
        if available <= 0:
            continue

        availability[
            (balance.location.warehouse_id, balance.lot.variant_id)
        ] += available

    return dict(availability)

def _load_stock_state(
    order,
    requirements,
    *,
    lock=False,
):
    """
    Carga exclusivamente stock de la empresa del pedido.
    """

    variant_ids = sorted({requirement["variant_id"] for requirement in requirements})

    stocks_query = (
        Stock.objects.select_related(
            "company",
            "warehouse",
            "variant",
        )
        .filter(
            company_id=order.company_id,
            warehouse__company_id=order.company_id,
            variant_id__in=variant_ids,
        )
        .order_by(
            "warehouse_id",
            "variant_id",
            "id",
        )
    )

    if lock:
        stocks_query = stocks_query.select_for_update()

    warehouses = {}
    stock_availability = {}
    seen_keys = set()

    for stock in stocks_query:
        warehouse = stock.warehouse

        if warehouse.company_id != order.company_id:
            raise ValidationError(
                f"La bodega #{warehouse.id} pertenece " "a otra empresa."
            )

        if hasattr(warehouse, "is_active"):
            if not warehouse.is_active:
                continue

        key = (
            warehouse.id,
            stock.variant_id,
        )

        if key in seen_keys:
            raise ValidationError(
                "Existen registros de stock duplicados para "
                f"bodega #{warehouse.id} y variante "
                f"#{stock.variant_id}."
            )

        seen_keys.add(key)

        available_stock = _internal_integer(
            stock.available_stock,
            reference=(f" en stock #{stock.id}"),
        )

        warehouses[warehouse.id] = warehouse
        stock_availability[key] = max(
            available_stock,
            0,
        )

    if not warehouses:
        raise ValidationError(
            "La empresa no tiene bodegas con stock " "disponible para este pedido."
        )

    # La promesa operativa se limita a unidades realmente ubicadas. Las filas
    # de ubicación se bloquean en el mismo orden en todas las transacciones,
    # evitando que dos pedidos reserven simultáneamente el mismo anaquel.
    locations_query = (
        VariantLocation.objects.filter(
            company_id=order.company_id,
            variant_id__in=variant_ids,
            location__company_id=order.company_id,
            location__warehouse_id__in=warehouses.keys(),
            location__is_active=True,
            location__is_virtual=False,
            location__status=WarehouseLocation.STATUS_ACTIVE,
            quantity__gt=0,
        )
        .select_related("location")
        .order_by("location__warehouse_id", "variant_id", "id")
    )
    if lock:
        locations_query = locations_query.select_for_update()
    locations = list(locations_query)

    location_ids = [row.location_id for row in locations]
    reserved_rows = (
        PickingItem.objects.filter(
            picking_order__company_id=order.company_id,
            picking_order__status__in=(
                PickingOrder.STATUS_PENDING,
                PickingOrder.STATUS_IN_PROGRESS,
                PickingOrder.STATUS_COMPLETED,
            ),
            picking_order__order__status__in=("paid", "picking", "packed"),
            variant_id__in=variant_ids,
            location_id__in=location_ids,
        )
        .values("location_id", "variant_id")
        .annotate(total=Sum("required_quantity"))
    )
    reserved_by_location = {
        (row["location_id"], row["variant_id"]): int(row["total"] or 0)
        for row in reserved_rows
    }

    located_availability = defaultdict(int)
    for row in locations:
        available_here = max(
            int(row.quantity or 0)
            - reserved_by_location.get((row.location_id, row.variant_id), 0),
            0,
        )
        located_availability[(row.location.warehouse_id, row.variant_id)] += (
            available_here
        )

    variants_by_id = {
        requirement["variant_id"]: requirement["variant"]
        for requirement in requirements
    }
    controlled_variants = [
        variant
        for variant in variants_by_id.values()
        if _is_lot_controlled_variant(variant)
    ]

    lot_availability = _load_controlled_lot_availability(
        company_id=order.company_id,
        warehouse_ids=set(warehouses),
        variants=controlled_variants,
        lock=lock,
    )

    availability = {}
    for key, quantity in stock_availability.items():
        warehouse_id, variant_id = key
        variant = variants_by_id.get(variant_id)

        if variant is not None and _is_lot_controlled_variant(variant):
            detailed = lot_availability.get(key, 0)
        else:
            detailed = located_availability.get(key, 0)

        availability[key] = min(quantity, detailed)

    return warehouses, availability


def get_pickable_stock_for_variant(
    *,
    company,
    variant,
    warehouse_ids=None,
):
    """
    Existencia vendible informativa.

    NORMAL: mantiene cálculo histórico por ubicaciones/pickings.
    LOT/LOT_EXPIRY: el límite real es LotBalance elegible y no caducado.

    ``warehouse_ids`` limita sólo consultas de lectura. Omitirlo conserva el
    comportamiento company-wide de planeación y reserva.
    """
    if company is None or variant is None:
        return 0
    if variant.company_id != company.id:
        raise ValidationError("El SKU solicitado pertenece a otra empresa.")

    stock_query = Stock.objects.filter(
        company_id=company.id,
        warehouse__company_id=company.id,
        variant_id=variant.id,
    )

    if warehouse_ids is not None:
        try:
            warehouse_ids = tuple(
                sorted({int(value) for value in warehouse_ids})
            )
        except (TypeError, ValueError) as exc:
            raise ValidationError(
                "El alcance de bodegas para consultar stock no es válido."
            ) from exc

        if not warehouse_ids:
            return 0

        stock_query = stock_query.filter(
            warehouse_id__in=warehouse_ids,
        )

    stocks = list(
        stock_query
        .select_related("warehouse")
        .order_by("warehouse_id", "variant_id", "id")
    )

    stock_by_warehouse = {}
    warehouse_ids = set()

    for stock in stocks:
        if hasattr(stock.warehouse, "is_active") and not stock.warehouse.is_active:
            continue
        if stock.warehouse_id in stock_by_warehouse:
            raise ValidationError(
                "Existen registros de stock duplicados para "
                f"la bodega #{stock.warehouse_id} y el SKU {variant.sku}."
            )
        stock_by_warehouse[stock.warehouse_id] = max(
            _internal_integer(
                stock.available_stock,
                reference=f" en stock #{stock.id}",
            ),
            0,
        )
        warehouse_ids.add(stock.warehouse_id)

    if not stock_by_warehouse:
        return 0

    if _is_lot_controlled_variant(variant):
        lot_availability = _load_controlled_lot_availability(
            company_id=company.id,
            warehouse_ids=warehouse_ids,
            variants=[variant],
            lock=False,
        )
        return sum(
            min(
                stock_quantity,
                lot_availability.get((warehouse_id, variant.id), 0),
            )
            for warehouse_id, stock_quantity in stock_by_warehouse.items()
        )

    locations = list(
        VariantLocation.objects.filter(
            company_id=company.id,
            variant_id=variant.id,
            location__company_id=company.id,
            location__warehouse_id__in=stock_by_warehouse,
            location__is_active=True,
            location__is_virtual=False,
            location__status=WarehouseLocation.STATUS_ACTIVE,
            quantity__gt=0,
        )
        .select_related("location")
        .order_by("location__warehouse_id", "id")
    )
    location_ids = [row.location_id for row in locations]

    reserved_rows = (
        PickingItem.objects.filter(
            picking_order__company_id=company.id,
            picking_order__status__in=(
                PickingOrder.STATUS_PENDING,
                PickingOrder.STATUS_IN_PROGRESS,
                PickingOrder.STATUS_COMPLETED,
            ),
            picking_order__order__status__in=("paid", "picking", "packed"),
            variant_id=variant.id,
            location_id__in=location_ids,
        )
        .values("location_id")
        .annotate(total=Sum("required_quantity"))
    )
    reserved_by_location = {
        row["location_id"]: int(row["total"] or 0)
        for row in reserved_rows
    }

    located_by_warehouse = defaultdict(int)
    for row in locations:
        located_by_warehouse[row.location.warehouse_id] += max(
            int(row.quantity or 0)
            - reserved_by_location.get(row.location_id, 0),
            0,
        )

    return sum(
        min(
            stock_quantity,
            located_by_warehouse.get(warehouse_id, 0),
        )
        for warehouse_id, stock_quantity in stock_by_warehouse.items()
    )



def _split_requirements_multibodega(requirements, warehouses, availability):
    """Distribuye una línea entre bodegas sin recorrer catálogos completos."""
    remaining = dict(availability)
    groups_by_warehouse = defaultdict(list)
    used_warehouses = set()

    for requirement in requirements:
        pending = requirement["internal_quantity"]
        commercial_pending = requirement["commercial_quantity"]
        candidates = [
            warehouse_id
            for warehouse_id in warehouses
            if remaining.get((warehouse_id, requirement["variant_id"]), 0) > 0
        ]
        candidates.sort(
            key=lambda warehouse_id: (
                0 if warehouse_id in used_warehouses else 1,
                -remaining.get((warehouse_id, requirement["variant_id"]), 0),
                warehouse_id,
            )
        )

        for warehouse_id in candidates:
            if pending <= 0:
                break
            key = (warehouse_id, requirement["variant_id"])
            assigned = min(pending, remaining[key])
            if assigned <= 0:
                continue
            segment = dict(requirement)
            segment["internal_quantity"] = assigned
            if assigned == pending:
                segment["commercial_quantity"] = commercial_pending
            else:
                segment["commercial_quantity"] = (
                    requirement["commercial_quantity"]
                    * Decimal(assigned)
                    / Decimal(requirement["internal_quantity"])
                ).quantize(Decimal("0.001"))
                commercial_pending -= segment["commercial_quantity"]
            groups_by_warehouse[warehouse_id].append(segment)
            remaining[key] -= assigned
            pending -= assigned
            used_warehouses.add(warehouse_id)

        if pending:
            available = requirement["internal_quantity"] - pending
            raise ValidationError(
                f"Stock ubicado insuficiente para SKU "
                f"{requirement['variant'].sku}. Requerido: "
                f"{requirement['internal_quantity']}; pickeable: {available}; "
                f"faltante: {pending}."
            )

    groups = []
    for warehouse_id, lines in groups_by_warehouse.items():
        groups.append(
            {
                "warehouse": warehouses[warehouse_id],
                "warehouse_id": warehouse_id,
                "lines": sorted(lines, key=lambda line: line["order_item_id"]),
                "first_order_item_id": min(
                    line["order_item_id"] for line in lines
                ),
            }
        )
    groups.sort(key=lambda group: (group["first_order_item_id"], group["warehouse_id"]))
    return groups


def _required_by_variant(requirements):
    totals = defaultdict(int)

    for requirement in requirements:
        totals[requirement["variant_id"]] += requirement["internal_quantity"]

    return dict(totals)


def _find_single_warehouse(
    requirements,
    warehouses,
    availability,
):
    """
    Prioriza una sola bodega cuando puede cubrir todo el pedido.
    """

    totals = _required_by_variant(requirements)

    candidates = []

    for warehouse_id, warehouse in warehouses.items():
        can_cover = True
        total_available = 0

        for variant_id, required_quantity in totals.items():
            available_quantity = availability.get(
                (
                    warehouse_id,
                    variant_id,
                ),
                0,
            )

            if available_quantity < required_quantity:
                can_cover = False
                break

            total_available += available_quantity

        if can_cover:
            candidates.append(
                (
                    -total_available,
                    warehouse_id,
                    warehouse,
                )
            )

    if not candidates:
        return None

    candidates.sort()

    return candidates[0][2]


def _candidate_warehouse_ids(
    requirement,
    warehouses,
    remaining,
):
    variant_id = requirement["variant_id"]
    quantity = requirement["internal_quantity"]

    return [
        warehouse_id
        for warehouse_id in warehouses
        if remaining.get(
            (
                warehouse_id,
                variant_id,
            ),
            0,
        )
        >= quantity
    ]


def _assign_requirements_multibodega(
    requirements,
    warehouses,
    availability,
):
    """
    Busca una asignación completa sin dividir líneas.

    Utiliza backtracking limitado y prioriza reutilizar bodegas
    ya seleccionadas para reducir el número de cumplimientos.
    """

    requirements_with_candidates = []

    for requirement in requirements:
        candidates = _candidate_warehouse_ids(
            requirement=requirement,
            warehouses=warehouses,
            remaining=availability,
        )

        if not candidates:
            variant = requirement["variant"]

            raise ValidationError(
                f"Ninguna bodega tiene stock suficiente "
                f"para cubrir completa la línea del SKU "
                f"{variant.sku}. Requerido: "
                f"{requirement['internal_quantity']}."
            )

        requirements_with_candidates.append(
            (
                requirement,
                candidates,
            )
        )

    requirements_with_candidates.sort(
        key=lambda item: (
            len(item[1]),
            -item[0]["internal_quantity"],
            item[0]["order_item_id"],
        )
    )

    remaining = dict(availability)
    current_assignment = {}
    used_warehouses = set()

    best_assignment = None
    best_warehouse_count = None

    visited_nodes = 0
    planning_limit_reached = False

    def search(index):
        nonlocal best_assignment
        nonlocal best_warehouse_count
        nonlocal visited_nodes
        nonlocal planning_limit_reached

        visited_nodes += 1

        if visited_nodes > MAX_PLANNING_NODES:
            planning_limit_reached = True
            return

        if (
            best_warehouse_count is not None
            and len(used_warehouses) >= best_warehouse_count
        ):
            return

        if index >= len(requirements_with_candidates):
            best_assignment = dict(current_assignment)

            best_warehouse_count = len(used_warehouses)

            return

        requirement, original_candidates = requirements_with_candidates[index]

        variant_id = requirement["variant_id"]
        quantity = requirement["internal_quantity"]

        candidates = [
            warehouse_id
            for warehouse_id in original_candidates
            if remaining.get(
                (
                    warehouse_id,
                    variant_id,
                ),
                0,
            )
            >= quantity
        ]

        candidates.sort(
            key=lambda warehouse_id: (
                0 if warehouse_id in used_warehouses else 1,
                -remaining.get(
                    (
                        warehouse_id,
                        variant_id,
                    ),
                    0,
                ),
                warehouse_id,
            )
        )

        for warehouse_id in candidates:
            key = (
                warehouse_id,
                variant_id,
            )

            was_used = warehouse_id in used_warehouses

            remaining[key] -= quantity

            current_assignment[requirement["order_item_id"]] = warehouse_id

            used_warehouses.add(warehouse_id)

            search(index + 1)

            remaining[key] += quantity

            current_assignment.pop(
                requirement["order_item_id"],
                None,
            )

            if not was_used:
                if warehouse_id not in (current_assignment.values()):
                    used_warehouses.discard(warehouse_id)

            # Ya se comprobó que ninguna bodega única cubre todo.
            # Una solución de dos bodegas es óptima.
            if best_warehouse_count == 2:
                return

            if planning_limit_reached:
                return

    search(0)

    if best_assignment is None:
        if planning_limit_reached:
            raise ValidationError(
                "La combinación de líneas y bodegas es demasiado "
                "compleja para el planificador automático. "
                "Se requiere una asignación manual."
            )

        raise ValidationError(
            "Existe stock parcial, pero no fue posible asignar "
            "cada línea completa a una sola bodega."
        )

    return best_assignment


def _build_groups(
    requirements,
    warehouses,
    assignment,
):
    groups_by_warehouse = defaultdict(list)

    for requirement in requirements:
        warehouse_id = assignment[requirement["order_item_id"]]

        groups_by_warehouse[warehouse_id].append(requirement)

    groups = []

    for warehouse_id, lines in groups_by_warehouse.items():
        lines.sort(key=lambda line: (line["order_item_id"]))

        groups.append(
            {
                "warehouse": warehouses[warehouse_id],
                "warehouse_id": warehouse_id,
                "lines": lines,
                "first_order_item_id": (lines[0]["order_item_id"]),
            }
        )

    groups.sort(
        key=lambda group: (
            group["first_order_item_id"],
            group["warehouse_id"],
        )
    )

    return groups


def _build_new_plan(
    order,
    requirements,
    *,
    lock_stock=False,
):
    warehouses, availability = _load_stock_state(
        order=order,
        requirements=requirements,
        lock=lock_stock,
    )

    single_warehouse = _find_single_warehouse(
        requirements=requirements,
        warehouses=warehouses,
        availability=availability,
    )

    if single_warehouse:
        assignment = {
            requirement["order_item_id"]: (single_warehouse.id)
            for requirement in requirements
        }
        groups = _build_groups(
            requirements=requirements,
            warehouses=warehouses,
            assignment=assignment,
        )
    else:
        groups = _split_requirements_multibodega(
            requirements=requirements,
            warehouses=warehouses,
            availability=availability,
        )

    mode = "single_warehouse" if len(groups) == 1 else "multi_warehouse"

    return {
        "order": order,
        "mode": mode,
        "groups": groups,
        "existing": False,
        "created": False,
    }


def _existing_plan(
    order,
    requirements,
    fulfillments,
):
    requirement_map = {
        requirement["order_item_id"]: requirement for requirement in requirements
    }

    allocations_by_order_item = defaultdict(list)

    groups = []

    cancelled_status = getattr(
        OrderFulfillment,
        "STATUS_CANCELLED",
        "cancelled",
    )

    pending_status = getattr(
        OrderFulfillment,
        "STATUS_PENDING",
        "pending",
    )

    for fulfillment in fulfillments:
        if fulfillment.company_id != order.company_id:
            raise ValidationError(
                f"El cumplimiento #{fulfillment.id} " "pertenece a otra empresa."
            )

        if fulfillment.order_id != order.id:
            raise ValidationError(
                f"El cumplimiento #{fulfillment.id} " "pertenece a otro pedido."
            )

        if fulfillment.warehouse.company_id != order.company_id:
            raise ValidationError(
                f"La bodega del cumplimiento "
                f"#{fulfillment.id} pertenece "
                "a otra empresa."
            )

        allocations = list(
            fulfillment.allocations.select_related(
                "order_item",
                "order_item__order",
                "variant",
            ).order_by(
                "order_item_id",
                "id",
            )
        )

        group_lines = []

        for allocation in allocations:
            allocations_by_order_item[allocation.order_item_id].append(allocation)

            requirement = requirement_map.get(allocation.order_item_id)

            if not requirement:
                raise ValidationError(
                    f"La asignación #{allocation.id} "
                    "apunta a una línea que ya no "
                    "pertenece al pedido."
                )

            if allocation.company_id != order.company_id:
                raise ValidationError(
                    f"La asignación #{allocation.id} " "pertenece a otra empresa."
                )

            if allocation.order_item.order_id != order.id:
                raise ValidationError(
                    f"La asignación #{allocation.id} " "apunta a otro pedido."
                )

            if allocation.variant_id != requirement["variant_id"]:
                raise ValidationError(
                    f"La asignación #{allocation.id} "
                    "apunta a una variante diferente."
                )

            if allocation.variant.company_id != order.company_id:
                raise ValidationError(
                    f"La variante de la asignación "
                    f"#{allocation.id} pertenece "
                    "a otra empresa."
                )

            allocation_commercial = _decimal_value(
                allocation.commercial_quantity
            )
            allocation_internal = _internal_integer(
                allocation.internal_quantity
            )
            if allocation_commercial <= 0:
                raise ValidationError(
                    f"La cantidad comercial de la "
                    f"asignación #{allocation.id} "
                    "no es válida."
                )
            if allocation_internal <= 0:
                raise ValidationError(
                    f"La cantidad interna de la "
                    f"asignación #{allocation.id} "
                    "no es válida."
                )
            segment = dict(requirement)
            segment["commercial_quantity"] = allocation_commercial
            segment["internal_quantity"] = allocation_internal
            group_lines.append(segment)

        groups.append(
            {
                "warehouse": fulfillment.warehouse,
                "warehouse_id": fulfillment.warehouse_id,
                "fulfillment": fulfillment,
                "lines": group_lines,
                "first_order_item_id": (
                    group_lines[0]["order_item_id"] if group_lines else 0
                ),
            }
        )

    for requirement in requirements:
        allocations = allocations_by_order_item.get(
            requirement["order_item_id"],
            [],
        )

        if len(allocations) == 0:
            raise ValidationError(
                f"La línea #{requirement['order_item_id']} "
                "no tiene cumplimiento asignado."
            )

        total_internal = sum(
            _internal_integer(allocation.internal_quantity)
            for allocation in allocations
        )
        total_commercial = sum(
            (_decimal_value(allocation.commercial_quantity) for allocation in allocations),
            Decimal("0"),
        )
        if total_internal != requirement["internal_quantity"]:
            raise ValidationError(
                f"La línea #{requirement['order_item_id']} "
                "no coincide con la cantidad interna del pedido."
            )
        if total_commercial != requirement["commercial_quantity"]:
            raise ValidationError(
                f"La línea #{requirement['order_item_id']} "
                "no coincide con la cantidad comercial del pedido."
            )

    # La validación terminó correctamente.
    # Ahora sí se pueden reactivar los cumplimientos cancelados
    # para regenerar sus pickings sin crear asignaciones duplicadas.
    for group in groups:
        fulfillment = group["fulfillment"]

        if fulfillment.status == cancelled_status:
            fulfillment.status = pending_status

            fulfillment.save(
                update_fields=[
                    "status",
                    "updated_at",
                ]
            )

    groups.sort(
        key=lambda group: (
            group["first_order_item_id"],
            group["warehouse_id"],
        )
    )

    mode = "single_warehouse" if len(groups) == 1 else "multi_warehouse"

    return {
        "order": order,
        "mode": mode,
        "groups": groups,
        "existing": True,
        "created": False,
    }


def preview_fulfillment_plan(order):
    """
    Calcula el plan sin crear cumplimientos ni reservar stock.
    """

    _validate_order(order)

    requirements = _load_requirements(order)

    fulfillments = list(
        OrderFulfillment.objects.select_related(
            "company",
            "order",
            "warehouse",
        )
        .filter(
            company_id=order.company_id,
            order_id=order.id,
        )
        .order_by(
            "sequence",
            "id",
        )
    )

    if fulfillments:
        return _existing_plan(
            order=order,
            requirements=requirements,
            fulfillments=fulfillments,
        )

    return _build_new_plan(
        order=order,
        requirements=requirements,
        lock_stock=False,
    )


@transaction.atomic
def plan_order_fulfillments(
    order,
    user=None,
    sync_financial=True,
):
    """
    Crea los cumplimientos y asignaciones.

    No reserva stock.
    No crea picking.
    No modifica cantidades de inventario.
    """

    order_model = order.__class__

    order = (
        order_model.objects.select_for_update()
        .select_related(
            "company",
        )
        .get(
            pk=order.pk,
        )
    )

    _validate_order(order)

    requirements = _load_requirements(order)

    fulfillments = list(
        OrderFulfillment.objects.select_for_update()
        .select_related(
            "company",
            "order",
            "warehouse",
        )
        .filter(
            company_id=order.company_id,
            order_id=order.id,
        )
        .order_by(
            "sequence",
            "id",
        )
    )

    if fulfillments:
        result = _existing_plan(
            order=order,
            requirements=requirements,
            fulfillments=fulfillments,
        )

        mode = result["mode"]

        if _order_has_field(
            order,
            "fulfillment_mode",
        ):
            if order.fulfillment_mode != mode:
                order.fulfillment_mode = mode
                order.save(
                    update_fields=[
                        "fulfillment_mode",
                        "updated_at",
                    ]
                )

        if sync_financial:
            _sync_sale_financial_attribution(order, user=user)

        return result

    plan = _build_new_plan(
        order=order,
        requirements=requirements,
        lock_stock=True,
    )

    created_by = _authenticated_user(user)

    created_fulfillments = []

    for sequence, group in enumerate(
        plan["groups"],
        start=1,
    ):
        fulfillment = OrderFulfillment(
            company=order.company,
            order=order,
            warehouse=group["warehouse"],
            sequence=sequence,
            status=getattr(
                OrderFulfillment,
                "STATUS_PENDING",
                "pending",
            ),
            created_by=created_by,
            notes=(
                "Plan automático multibodega. "
                "Cada línea comercial se asignó completa "
                "a una sola bodega."
            ),
        )

        fulfillment.full_clean()
        fulfillment.save()

        for line in group["lines"]:
            allocation = OrderItemAllocation(
                company=order.company,
                fulfillment=fulfillment,
                order_item=line["order_item"],
                variant=line["variant"],
                commercial_quantity=(line["commercial_quantity"]),
                internal_quantity=(line["internal_quantity"]),
            )

            allocation.full_clean()
            allocation.save()

        group["fulfillment"] = fulfillment

        created_fulfillments.append(fulfillment)

    mode = plan["mode"]

    if _order_has_field(
        order,
        "fulfillment_mode",
    ):
        order.fulfillment_mode = mode
        order.save(
            update_fields=[
                "fulfillment_mode",
                "updated_at",
            ]
        )

    plan["created"] = True
    plan["existing"] = False
    plan["fulfillments"] = created_fulfillments

    if sync_financial:
        _sync_sale_financial_attribution(order, user=user)

    return plan


def plan_to_text(plan):
    """
    Convierte el resultado en texto para pruebas de consola.
    """

    order = plan["order"]

    source = (
        "EXISTENTE"
        if plan.get("existing")
        else ("CREADO" if plan.get("created") else "SIMULACIÓN")
    )

    lines = [
        "",
        "PLAN DE CUMPLIMIENTO",
        f"Pedido: {order.folio}",
        f"Empresa: {order.company_id}",
        f"Modo: {plan['mode']}",
        f"Origen: {source}",
        f"Bodegas: {len(plan['groups'])}",
    ]

    for index, group in enumerate(
        plan["groups"],
        start=1,
    ):
        warehouse = group["warehouse"]
        fulfillment = group.get("fulfillment")

        fulfillment_code = (
            fulfillment.code if fulfillment else f"{order.folio}-{index:02d}"
        )

        lines.append("")
        lines.append(f"{fulfillment_code} | " f"{warehouse.name}")

        for line in group["lines"]:
            lines.append(
                f"  - {line['variant'].sku} | "
                f"comercial: "
                f"{line['commercial_quantity']} | "
                f"interna: "
                f"{line['internal_quantity']}"
            )

    return "\n".join(lines)
