import sentry_sdk
import re
from datetime import date
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum, Q
from django.utils import timezone

from inventory.services import check_stock_critical
from inventory.models import (
    Stock,
    StockMovement,
    ProductVariant,
    ProductLot,
    LotBalance,
    LotMovement,
)
from sales.models import OrderItem
from warehouse.models import (
    PickingItem,
    VariantLocation,
    WarehouseAuditLog,
    WarehouseLocation,
    PickingOrder,
    OrderItemAllocation,
    PickingLotAllocation,
)


def save_existing_fields(instance, field_names):
    existing_fields = [
        field_name
        for field_name in field_names
        if model_has_field(instance.__class__, field_name)
    ]

    if existing_fields:
        instance.save(update_fields=existing_fields)
    else:
        instance.save()


def model_has_field(model_or_instance, field_name):
    model_class = (
        model_or_instance
        if isinstance(model_or_instance, type)
        else model_or_instance.__class__
    )

    try:
        model_class._meta.get_field(field_name)
        return True
    except Exception:
        return False


def _natural_key(value):
    """
    Ordena textos como A1, A2 y A10 de forma lógica.
    """
    value = str(value or "")

    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", value)
    ]


def _warehouse_location_sort_key(location):
    return (
        _natural_key(getattr(location, "aisle", "")),
        _natural_key(getattr(location, "rack", "")),
        _natural_key(getattr(location, "level", "")),
        _natural_key(getattr(location, "bin_code", "")),
    )


def _location_sort_key(variant_location):
    return _warehouse_location_sort_key(variant_location.location)


def _find_pickable_locations(company, warehouse, variant, *, lock=False):
    locations = VariantLocation.objects.select_related(
        "variant",
        "variant__product",
        "location",
        "location__warehouse",
    ).filter(
        company=company,
        variant=variant,
        location__warehouse=warehouse,
        quantity__gt=0,
        location__is_active=True,
    )

    if model_has_field(WarehouseLocation, "is_virtual"):
        locations = locations.filter(location__is_virtual=False)

    if model_has_field(WarehouseLocation, "status"):
        active_status = getattr(WarehouseLocation, "STATUS_ACTIVE", None)

        if active_status:
            locations = locations.filter(location__status=active_status)

    if lock:
        locations = locations.select_for_update()

    locations = list(locations.order_by("id"))
    locations.sort(key=_location_sort_key)

    return locations


def _active_location_reservations(company, variant, location_ids):
    if not location_ids:
        return {}
    rows = (
        PickingItem.objects.filter(
            picking_order__company=company,
            picking_order__status__in=(
                PickingOrder.STATUS_PENDING,
                PickingOrder.STATUS_IN_PROGRESS,
                PickingOrder.STATUS_COMPLETED,
            ),
            picking_order__order__status__in=("paid", "picking", "packed"),
            variant=variant,
            location_id__in=location_ids,
        )
        .values("location_id")
        .annotate(total=Sum("required_quantity"))
    )
    return {row["location_id"]: int(row["total"] or 0) for row in rows}


def _stock_available(stock):
    try:
        return stock.available_stock
    except Exception as error:
        sentry_sdk.capture_exception(error)
        physical = getattr(stock, "physical_stock", 0) or 0
        reserved = getattr(stock, "reserved_stock", 0) or 0
        committed = getattr(stock, "committed_stock", 0) or 0

    return physical - reserved - committed


def clean_quantity_label(value):
    if value is None:
        return 0

    try:
        if value == value.to_integral_value():
            return int(value)
    except Exception:
        pass

    try:
        return round(value, 3)
    except Exception:
        return value


def convert_order_item_quantity_to_internal(
    variant,
    quantity,
):
    """
    Convierte cantidad comercial del pedido a cantidad interna de stock.

    Ejemplo:
    2 paquetes x 12 piezas = 24 piezas internas.
    """

    product = getattr(
        variant,
        "product",
        None,
    )

    quantity_decimal = Decimal(str(quantity or 0))

    if quantity_decimal <= 0:
        raise ValidationError("La cantidad del pedido debe ser mayor a cero.")

    if product and hasattr(
        product,
        "to_base_quantity",
    ):
        internal_quantity = product.to_base_quantity(quantity_decimal)
    else:
        stock_mode = (
            getattr(
                product,
                "stock_mode",
                "unit",
            )
            if product
            else "unit"
        )

        conversion_factor = getattr(
            product,
            "conversion_factor",
            Decimal("1"),
        ) or Decimal("1")

        if stock_mode == "bulk":
            internal_quantity = quantity_decimal * conversion_factor
        else:
            internal_quantity = quantity_decimal

    if internal_quantity <= 0:
        raise ValidationError("La cantidad interna debe ser mayor a cero.")

    if internal_quantity != internal_quantity.to_integral_value():
        raise ValidationError(
            "La conversión del producto genera piezas internas "
            "con decimales. Por ahora el stock interno debe ser entero."
        )

    return int(internal_quantity)


def get_commercial_quantity_label(variant, internal_quantity):
    product = getattr(
        variant,
        "product",
        None,
    )

    if (
        product
        and getattr(
            product,
            "stock_mode",
            "unit",
        )
        == "bulk"
    ):
        commercial_quantity = clean_quantity_label(
            product.to_commercial_quantity(internal_quantity)
        )

        return (
            f"{commercial_quantity} "
            f"{product.get_commercial_unit_display()} "
            f"({internal_quantity} "
            f"{product.get_base_unit_display()} internas)"
        )

    return f"{internal_quantity} piezas"


def _get_order_item_quantity(order_item):
    if hasattr(
        order_item,
        "quantity",
    ):
        return order_item.quantity

    if hasattr(
        order_item,
        "cantidad",
    ):
        return order_item.cantidad

    raise ValidationError(
        "No se encontró el campo de cantidad " "en la línea del pedido."
    )


def _get_order_items(order):
    if hasattr(
        order,
        "items",
    ):
        return (
            order.items.select_related(
                "variant",
                "variant__product",
            )
            .all()
            .order_by("id")
        )

    return (
        OrderItem.objects.select_related(
            "variant",
            "variant__product",
        )
        .filter(
            order=order,
        )
        .order_by("id")
    )


def _validate_positive_internal_quantity(
    quantity,
    *,
    reference="",
):
    try:
        quantity_decimal = Decimal(str(quantity or 0))
    except Exception as exc:
        raise ValidationError(f"Cantidad interna inválida{reference}.") from exc

    if quantity_decimal <= 0:
        raise ValidationError(f"La cantidad interna debe ser mayor a cero{reference}.")

    if quantity_decimal != quantity_decimal.to_integral_value():
        raise ValidationError(f"La cantidad interna debe ser entera{reference}.")

    return int(quantity_decimal)


def _validate_picking_context(picking_order):
    """
    Valida que pedido, empresa, bodega y cumplimiento pertenezcan
    al mismo contexto multiempresa.
    """

    company = picking_order.company
    order = picking_order.order

    if order.company_id != company.id:
        raise ValidationError("El pedido del picking pertenece a otra empresa.")

    if picking_order.warehouse_id:
        if picking_order.warehouse.company_id != company.id:
            raise ValidationError("La bodega del picking pertenece a otra empresa.")

    if not picking_order.fulfillment_id:
        return None

    fulfillment = picking_order.fulfillment

    if fulfillment.company_id != company.id:
        raise ValidationError("El cumplimiento del picking pertenece a otra empresa.")

    if fulfillment.order_id != order.id:
        raise ValidationError("El cumplimiento no pertenece al pedido del picking.")

    if fulfillment.warehouse.company_id != company.id:
        raise ValidationError("La bodega del cumplimiento pertenece a otra empresa.")

    if picking_order.warehouse_id:
        if picking_order.warehouse_id != fulfillment.warehouse_id:
            raise ValidationError(
                "La bodega del picking no coincide con " "la bodega del cumplimiento."
            )
    else:
        picking_order.warehouse = fulfillment.warehouse
        picking_order.save(
            update_fields=[
                "warehouse",
                "updated_at",
            ]
        )

    return fulfillment


def _get_picking_lines(picking_order):
    """
    Devuelve únicamente las líneas que debe reservar el picking.

    Nuevo flujo:
    - Usa las asignaciones del cumplimiento.

    Flujo histórico:
    - Si no existe cumplimiento, utiliza todas las líneas del pedido.
    """

    company = picking_order.company
    order = picking_order.order
    fulfillment = _validate_picking_context(picking_order)

    lines = []

    if fulfillment:
        allocations = list(
            fulfillment.allocations.select_related(
                "order_item",
                "order_item__order",
                "order_item__variant",
                "variant",
                "variant__product",
            ).order_by(
                "order_item_id",
                "id",
            )
        )

        if not allocations:
            raise ValidationError(
                f"El cumplimiento {fulfillment.code} " "no tiene productos asignados."
            )

        for allocation in allocations:
            order_item = allocation.order_item
            variant = allocation.variant

            if allocation.company_id != company.id:
                raise ValidationError(
                    f"La asignación #{allocation.id} " "pertenece a otra empresa."
                )

            if order_item.order_id != order.id:
                raise ValidationError(
                    f"La línea #{order_item.id} no pertenece "
                    f"al pedido {order.folio}."
                )

            if order_item.order.company_id != company.id:
                raise ValidationError(
                    f"La línea #{order_item.id} pertenece " "a otra empresa."
                )

            if variant.company_id != company.id:
                raise ValidationError(
                    f"El SKU {variant.sku} pertenece " "a otra empresa."
                )

            if order_item.variant_id != variant.id:
                raise ValidationError(
                    f"La variante asignada para la línea "
                    f"#{order_item.id} no coincide con "
                    "la variante original del pedido."
                )

            commercial_quantity = Decimal(str(allocation.commercial_quantity or 0))

            if commercial_quantity <= 0:
                raise ValidationError(
                    f"La asignación #{allocation.id} "
                    "tiene cantidad comercial inválida."
                )

            internal_quantity = _validate_positive_internal_quantity(
                allocation.internal_quantity,
                reference=(f" en asignación " f"#{allocation.id}"),
            )

            lines.append(
                {
                    "allocation_id": allocation.id,
                    "order_item": order_item,
                    "variant": variant,
                    "commercial_quantity": (commercial_quantity),
                    "internal_quantity": (internal_quantity),
                }
            )

        return lines

    order_items = list(_get_order_items(order))

    if not order_items:
        raise ValidationError("El pedido no tiene productos para generar picking.")

    for order_item in order_items:
        variant = order_item.variant

        if variant.company_id != company.id:
            raise ValidationError(f"El SKU {variant.sku} pertenece " "a otra empresa.")

        commercial_quantity = _get_order_item_quantity(order_item)

        internal_quantity = convert_order_item_quantity_to_internal(
            variant=variant,
            quantity=commercial_quantity,
        )

        lines.append(
            {
                "allocation_id": None,
                "order_item": order_item,
                "variant": variant,
                "commercial_quantity": (Decimal(str(commercial_quantity))),
                "internal_quantity": (internal_quantity),
            }
        )

    return lines


def _create_stock_movement(
    *,
    company,
    warehouse,
    variant,
    movement_type,
    quantity,
    reference="",
    notes="",
    user=None,
):
    data = {}

    if model_has_field(StockMovement, "company"):
        data["company"] = company

    if model_has_field(StockMovement, "warehouse"):
        data["warehouse"] = warehouse

    if model_has_field(StockMovement, "variant"):
        data["variant"] = variant

    if model_has_field(StockMovement, "movement_type"):
        data["movement_type"] = movement_type

    if model_has_field(StockMovement, "quantity"):
        data["quantity"] = quantity

    if model_has_field(StockMovement, "reference"):
        data["reference"] = reference

    if model_has_field(StockMovement, "notes"):
        data["notes"] = notes

    if model_has_field(StockMovement, "created_by"):
        data["created_by"] = user

    if model_has_field(StockMovement, "user"):
        data["user"] = user

    return StockMovement.objects.create(**data)


def _create_audit_log(
    *,
    company,
    order=None,
    picking_order=None,
    picking_item=None,
    user=None,
    action,
    description,
    metadata=None,
):
    WarehouseAuditLog.objects.create(
        company=company,
        order=order,
        picking_order=picking_order,
        picking_item=picking_item,
        user=user,
        action=action,
        description=description,
        metadata=metadata or {},
    )


def _find_best_warehouse_for_lines(
    company,
    lines,
):
    """
    Compatibilidad para pickings antiguos sin cumplimiento.

    Busca una sola bodega capaz de cubrir todas las líneas,
    utilizando cantidades internas reales.
    """

    variants = [line["variant"] for line in lines]

    candidate_warehouse_ids = (
        Stock.objects.filter(
            company=company,
            variant__in=variants,
        )
        .values_list(
            "warehouse_id",
            flat=True,
        )
        .distinct()
        .order_by("warehouse_id")
    )

    for warehouse_id in candidate_warehouse_ids:
        can_fulfill = True

        for line in lines:
            variant = line["variant"]
            quantity = line["internal_quantity"]

            stock = Stock.objects.filter(
                company=company,
                warehouse_id=warehouse_id,
                variant=variant,
            ).first()

            if not stock or stock.available_stock < quantity:
                can_fulfill = False
                break

        if can_fulfill:
            stock_reference = (
                Stock.objects.select_related(
                    "warehouse",
                )
                .filter(
                    company=company,
                    warehouse_id=warehouse_id,
                )
                .first()
            )

            if stock_reference:
                return stock_reference.warehouse

    raise ValidationError(
        "No hay una bodega con stock suficiente " "para cubrir todo el picking."
    )


def _find_pickable_location(
    company,
    warehouse,
    variant,
    quantity,
):
    return (
        VariantLocation.objects.select_related(
            "location",
        )
        .filter(
            company=company,
            variant=variant,
            location__company=company,
            location__warehouse=warehouse,
            location__is_active=True,
            location__is_virtual=False,
            location__status=(WarehouseLocation.STATUS_ACTIVE),
            quantity__gte=quantity,
        )
        .order_by(
            "-is_primary",
            "location__aisle",
            "location__rack",
            "location__level",
            "location__bin_code",
        )
        .first()
    )



def _is_lot_controlled(variant):
    return (
        getattr(
            variant,
            "inventory_control",
            ProductVariant.INVENTORY_CONTROL_NORMAL,
        )
        != ProductVariant.INVENTORY_CONTROL_NORMAL
    )


def _fefo_lot_sort_key(lot):
    expiration = lot.expiration_date
    return (
        1 if expiration is None else 0,
        expiration or date.max,
        lot.lot_number,
        lot.id,
    )


def _locked_allocatable_lot_balances(*, company, warehouse, variant):
    """
    Devuelve saldos elegibles para reserva con locks PostgreSQL.

    Orden de lock LT-1C:
      Stock (adquirido por reserve_stock_for_picking)
      -> ProductLot
      -> WarehouseLocation
      -> LotBalance

    Los lotes caducados se excluyen por fecha en cada reserva; no dependen
    de un worker que cambie estados.
    """
    if not _is_lot_controlled(variant):
        return []

    today = timezone.localdate()

    lot_query = ProductLot.objects.select_for_update().filter(
        company_id=company.id,
        variant_id=variant.id,
        status=ProductLot.STATUS_ACTIVE,
    ).filter(
        Q(expiration_date__isnull=True)
        | Q(expiration_date__gte=today)
    )

    if (
        variant.inventory_control
        == ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY
    ):
        lot_query = lot_query.filter(expiration_date__isnull=False)

    lot_ids = list(
        lot_query.order_by(
            F("expiration_date").asc(nulls_last=True),
            "lot_number",
            "id",
        ).values_list("id", flat=True)
    )

    if not lot_ids:
        return []

    location_ids = list(
        WarehouseLocation.objects.select_for_update()
        .filter(
            company_id=company.id,
            warehouse_id=warehouse.id,
            is_active=True,
            is_virtual=False,
            status=WarehouseLocation.STATUS_ACTIVE,
        )
        .order_by("id")
        .values_list("id", flat=True)
    )

    if not location_ids:
        return []

    balance_ids = list(
        LotBalance.objects.select_for_update()
        .filter(
            company_id=company.id,
            lot_id__in=lot_ids,
            location_id__in=location_ids,
            status=LotBalance.STATUS_AVAILABLE,
        )
        .order_by("lot_id", "location_id", "id")
        .values_list("id", flat=True)
    )

    if not balance_ids:
        return []

    balances = list(
        LotBalance.objects.select_related(
            "lot",
            "lot__variant",
            "location",
            "location__warehouse",
        )
        .filter(
            company_id=company.id,
            id__in=balance_ids,
        )
    )

    eligible = []
    for balance in balances:
        if balance.lot.company_id != company.id:
            raise ValidationError("Se detectó un lote de otra empresa.")
        if balance.lot.variant_id != variant.id:
            raise ValidationError("Se detectó un lote de otro SKU.")
        if balance.location.company_id != company.id:
            raise ValidationError("Se detectó una ubicación de otra empresa.")
        if balance.location.warehouse_id != warehouse.id:
            raise ValidationError("Se detectó un saldo de otra bodega.")
        if not balance.lot.is_allocatable:
            continue
        if not balance.location.is_pickable:
            continue
        if balance.allocatable_quantity <= 0:
            continue
        eligible.append(balance)

    eligible.sort(
        key=lambda balance: (
            _fefo_lot_sort_key(balance.lot),
            _warehouse_location_sort_key(balance.location),
            balance.id,
        )
    )
    return eligible


def _create_lot_movement(
    *,
    company,
    lot,
    balance,
    movement_type,
    quantity,
    event_key,
    picking_order,
    picking_item,
    user=None,
    physical_before=None,
    physical_after=None,
    reserved_before=None,
    reserved_after=None,
    committed_before=None,
    committed_after=None,
    notes="",
):
    return LotMovement.objects.create(
        company=company,
        lot=lot,
        balance=balance,
        movement_type=movement_type,
        quantity=quantity,
        reference_type="picking_item",
        reference_id=picking_item.id,
        reference_code=(
            f"Picking {picking_order.id} / "
            f"Pedido {picking_order.order.folio}"
        ),
        event_key=event_key,
        physical_before=physical_before,
        physical_after=physical_after,
        reserved_before=reserved_before,
        reserved_after=reserved_after,
        committed_before=committed_before,
        committed_after=committed_after,
        notes=notes,
        created_by=(
            user if getattr(user, "is_authenticated", False) else None
        ),
    )

def _get_or_create_waste_location(
    company,
    warehouse,
):
    if warehouse.company_id != company.id:
        raise ValidationError("La bodega de merma pertenece a otra empresa.")

    location, _ = WarehouseLocation.objects.get_or_create(
        company=company,
        warehouse=warehouse,
        bin_code="MERMA",
        defaults={
            "aisle": "VIRTUAL",
            "rack": "MERMA",
            "level": "",
            "status": (WarehouseLocation.STATUS_WASTE),
            "is_virtual": True,
            "is_active": True,
            "blocked_reason": ("Ubicación virtual para " "producto dañado o merma."),
        },
    )

    if location.status != WarehouseLocation.STATUS_WASTE or not location.is_virtual:
        location.status = WarehouseLocation.STATUS_WASTE
        location.is_virtual = True
        location.is_active = True
        location.blocked_reason = "Ubicación virtual para " "producto dañado o merma."

        location.save(
            update_fields=[
                "status",
                "is_virtual",
                "is_active",
                "blocked_reason",
            ]
        )

    return location


@transaction.atomic
def reserve_stock_for_picking(picking_order, user=None):
    """
    LT-1C: reserva agregada + reserva exacta por lote.

    NORMAL conserva la ruta histórica por VariantLocation.
    LOT / LOT_EXPIRY usan saldos de lote elegibles; LOT_EXPIRY aplica FEFO
    por fecha de caducidad ascendente.

    La transacción bloquea primero stock agregado y después el detalle por
    lote para mantener coherencia con las recepciones LT-1B.
    """
    locked_picking_order = PickingOrder.objects.select_for_update().get(
        pk=picking_order.pk,
        company_id=picking_order.company_id,
    )

    picking_order = PickingOrder.objects.select_related(
        "company",
        "order",
        "warehouse",
        "fulfillment",
    ).get(
        pk=locked_picking_order.pk,
        company_id=locked_picking_order.company_id,
    )

    if picking_order.status == PickingOrder.STATUS_CANCELLED:
        raise ValidationError(
            "No puedes reservar stock para un picking cancelado."
        )

    # Idempotencia histórica y LT-1C.
    if picking_order.items.exists():
        return picking_order

    company = picking_order.company
    order = picking_order.order
    warehouse = picking_order.warehouse
    fulfillment = picking_order.fulfillment

    if fulfillment is None:
        raise ValidationError("El picking no tiene un cumplimiento asociado.")
    if fulfillment.company_id != company.id:
        raise ValidationError("El cumplimiento pertenece a otra empresa.")
    if fulfillment.order_id != order.id:
        raise ValidationError("El cumplimiento pertenece a otro pedido.")
    if warehouse is None:
        raise ValidationError("El picking no tiene una bodega asignada.")
    if fulfillment.warehouse_id != warehouse.id:
        raise ValidationError(
            "La bodega del picking no coincide con el cumplimiento."
        )
    if warehouse.company_id != company.id:
        raise ValidationError("La bodega pertenece a otra empresa.")

    allocation_ids = list(
        OrderItemAllocation.objects.select_for_update()
        .filter(
            company_id=company.id,
            fulfillment_id=fulfillment.id,
        )
        .order_by("id")
        .values_list("id", flat=True)
    )
    if not allocation_ids:
        raise ValidationError("El cumplimiento no tiene productos asignados.")

    allocations = list(
        OrderItemAllocation.objects.select_related(
            "company",
            "fulfillment",
            "order_item",
            "variant",
            "variant__product",
        )
        .filter(
            company_id=company.id,
            fulfillment_id=fulfillment.id,
            id__in=allocation_ids,
        )
    )
    allocations.sort(key=lambda row: (row.variant_id, row.id))

    required_by_variant = {}
    variants_by_id = {}

    for allocation in allocations:
        variant = allocation.variant
        order_item = allocation.order_item
        required_internal_qty = int(allocation.internal_quantity or 0)

        if variant is None:
            raise ValidationError("Existe una asignación sin SKU o variante.")
        if variant.company_id != company.id:
            raise ValidationError(
                f"El SKU {variant.sku} pertenece a otra empresa."
            )
        if order_item is None or order_item.order_id != order.id:
            raise ValidationError(
                f"La asignación del SKU {variant.sku} "
                "no corresponde a este pedido."
            )
        if required_internal_qty <= 0:
            raise ValidationError(
                f"La cantidad interna asignada al SKU "
                f"{variant.sku} no es válida."
            )

        variants_by_id[variant.id] = variant
        required_by_variant[variant.id] = (
            required_by_variant.get(variant.id, 0)
            + required_internal_qty
        )

    if not required_by_variant:
        raise ValidationError("No hay cantidades válidas para reservar.")

    # Lock order compatible con recepción LT-1B: Stock primero.
    locked_stocks = (
        Stock.objects.select_for_update()
        .filter(
            company_id=company.id,
            warehouse_id=warehouse.id,
            variant_id__in=sorted(required_by_variant),
        )
        .order_by("variant_id", "id")
    )
    stocks = {stock.variant_id: stock for stock in locked_stocks}

    for variant_id, required_quantity in required_by_variant.items():
        stock = stocks.get(variant_id)
        variant = variants_by_id[variant_id]
        if stock is None:
            raise ValidationError(
                f"No existe registro de stock para SKU "
                f"{variant.sku} en la bodega {warehouse.name}."
            )
        if _stock_available(stock) < required_quantity:
            raise ValidationError(
                f"Stock insuficiente para SKU {variant.sku}. "
                f"Disponible: "
                f"{get_commercial_quantity_label(variant, _stock_available(stock))}. "
                f"Requerido: "
                f"{get_commercial_quantity_label(variant, required_quantity)}."
            )

    route_candidates = []
    planned_by_balance = {}

    for allocation in allocations:
        variant = allocation.variant
        order_item = allocation.order_item
        required_internal_qty = int(allocation.internal_quantity or 0)
        remaining_qty = required_internal_qty

        if _is_lot_controlled(variant):
            balances = _locked_allocatable_lot_balances(
                company=company,
                warehouse=warehouse,
                variant=variant,
            )

            for balance in balances:
                if remaining_qty <= 0:
                    break

                already_planned = planned_by_balance.get(balance.id, 0)
                available_here = max(
                    int(balance.allocatable_quantity) - already_planned,
                    0,
                )
                if available_here <= 0:
                    continue

                qty_to_pick = min(remaining_qty, available_here)
                planned_by_balance[balance.id] = (
                    already_planned + qty_to_pick
                )

                route_candidates.append(
                    {
                        "order_item": order_item,
                        "variant": variant,
                        "location": balance.location,
                        "required_quantity": qty_to_pick,
                        "lot": balance.lot,
                        "lot_balance": balance,
                        "sort_key": _warehouse_location_sort_key(balance.location),
                    }
                )
                remaining_qty -= qty_to_pick
        else:
            pickable_locations = _find_pickable_locations(
                company=company,
                warehouse=warehouse,
                variant=variant,
                lock=True,
            )

            reserved_by_location = _active_location_reservations(
                company,
                variant,
                [row.location_id for row in pickable_locations],
            )

            for variant_location in pickable_locations:
                if remaining_qty <= 0:
                    break

                available_here = max(
                    int(variant_location.quantity or 0)
                    - reserved_by_location.get(
                        variant_location.location_id,
                        0,
                    ),
                    0,
                )
                if available_here <= 0:
                    continue

                qty_to_pick = min(remaining_qty, available_here)

                route_candidates.append(
                    {
                        "order_item": order_item,
                        "variant": variant,
                        "location": variant_location.location,
                        "required_quantity": qty_to_pick,
                        "lot": None,
                        "lot_balance": None,
                        "sort_key": _location_sort_key(variant_location),
                    }
                )
                remaining_qty -= qty_to_pick

        if remaining_qty > 0:
            missing_label = get_commercial_quantity_label(
                variant,
                remaining_qty,
            )
            if _is_lot_controlled(variant):
                raise ValidationError(
                    f"No hay lote elegible suficiente para SKU {variant.sku} "
                    f"en {warehouse.name}. Faltante: {missing_label}. "
                    "Se excluyen lotes caducados, bloqueados, recalled, "
                    "cuarentena y saldos sin disponibilidad."
                )
            raise ValidationError(
                f"No hay ubicación física suficiente para SKU "
                f"{variant.sku} en {warehouse.name}. "
                f"Faltante: {missing_label}."
            )

    route_candidates.sort(key=lambda item: item["sort_key"])

    for variant_id, required_quantity in required_by_variant.items():
        stock = stocks[variant_id]
        variant = variants_by_id[variant_id]

        stock.reserved_stock = int(stock.reserved_stock or 0) + required_quantity
        save_existing_fields(
            stock,
            ["reserved_stock", "updated_at"],
        )

        check_stock_critical(
            stock=stock,
            source="picking_reserved",
            reference=(
                f"Picking {picking_order.id} / "
                f"Pedido {order.folio} / "
                f"Cumplimiento {fulfillment.code}"
            ),
        )

        _create_stock_movement(
            company=company,
            warehouse=warehouse,
            variant=variant,
            movement_type="reservation",
            quantity=required_quantity,
            reference=(
                f"Picking {picking_order.id} / "
                f"Pedido {order.folio} / "
                f"Cumplimiento {fulfillment.code}"
            ),
            notes=(
                "Stock reservado por generación de picking. "
                + (
                    "Reserva detallada por lote FEFO."
                    if _is_lot_controlled(variant)
                    else "Reserva normal."
                )
            ),
            user=user,
        )

    for route_index, item in enumerate(route_candidates, start=1):
        picking_item = PickingItem.objects.create(
            picking_order=picking_order,
            order_item=item["order_item"],
            variant=item["variant"],
            location=item["location"],
            required_quantity=item["required_quantity"],
            picked_quantity=0,
            route_order=route_index,
            status=PickingItem.STATUS_PENDING,
            has_issue=False,
            issue_note="",
        )

        balance_reference = item["lot_balance"]
        lot = item["lot"]

        if balance_reference is not None:
            quantity = int(item["required_quantity"])

            # Puede haber dos líneas comerciales del mismo SKU que terminen
            # reservando el mismo saldo. Recargamos la fila ya bloqueada para
            # no escribir snapshots obsoletos entre ambas asignaciones.
            balance = LotBalance.objects.select_for_update().get(
                pk=balance_reference.pk,
                company_id=company.id,
            )

            if balance.lot_id != lot.id:
                raise ValidationError(
                    "El saldo bloqueado ya no corresponde al lote planificado."
                )

            physical_before = int(balance.physical_quantity or 0)
            reserved_before = int(balance.reserved_quantity or 0)
            committed_before = int(balance.committed_quantity or 0)

            if balance.allocatable_quantity < quantity:
                raise ValidationError(
                    f"El lote {lot.lot_number} perdió disponibilidad "
                    "durante la reserva."
                )

            balance.reserved_quantity = reserved_before + quantity
            balance.full_clean()
            balance.save(
                update_fields=["reserved_quantity", "updated_at"]
            )

            lot_allocation = PickingLotAllocation(
                company=company,
                picking_item=picking_item,
                lot=lot,
                balance=balance,
                quantity=quantity,
                status=PickingLotAllocation.STATUS_RESERVED,
            )
            lot_allocation.full_clean()
            lot_allocation.save()

            _create_lot_movement(
                company=company,
                lot=lot,
                balance=balance,
                movement_type=LotMovement.TYPE_RESERVATION,
                quantity=quantity,
                event_key=(
                    f"picking:{picking_order.id}:"
                    f"item:{picking_item.id}:reserve"
                ),
                picking_order=picking_order,
                picking_item=picking_item,
                user=user,
                physical_before=physical_before,
                physical_after=balance.physical_quantity,
                reserved_before=reserved_before,
                reserved_after=balance.reserved_quantity,
                committed_before=committed_before,
                committed_after=balance.committed_quantity,
                notes="Reserva FEFO por lote para picking.",
            )

    _create_audit_log(
        company=company,
        order=order,
        picking_order=picking_order,
        user=user,
        action="stock_reserved",
        description=(
            f"Stock reservado para el picking del pedido "
            f"{order.folio}, cumplimiento {fulfillment.code}."
        ),
        metadata={
            "picking_order_id": picking_order.id,
            "fulfillment_id": fulfillment.id,
            "warehouse_id": warehouse.id,
            "warehouse_name": warehouse.name,
            "lot_controlled_items": sum(
                1 for item in route_candidates if item["lot"] is not None
            ),
            "fefo": True,
        },
    )

    return picking_order



@transaction.atomic
def release_reserved_stock_for_cancelled_picking(
    picking_order,
    user=None,
    reason="",
):
    """
    Libera el agregado y, para SKU controlado, la reserva exacta del lote.
    """
    cancelled_status = getattr(
        picking_order,
        "STATUS_CANCELLED",
        "cancelled",
    )

    if picking_order.status == cancelled_status:
        return picking_order

    company = picking_order.company
    order = picking_order.order
    _validate_picking_context(picking_order)

    reason = reason or "Picking cancelado y stock reservado liberado."

    if getattr(order, "status", None) in ["shipped", "delivered"]:
        raise ValidationError(
            "No se puede liberar stock porque el pedido ya fue enviado o entregado."
        )

    picking_items = list(
        picking_order.items.select_related(
            "variant",
            "location",
            "location__warehouse",
        )
        .all()
        .order_by("id")
    )

    for picking_item in picking_items:
        variant = picking_item.variant

        if _is_lot_controlled(variant):
            historical_allocation = (
                PickingLotAllocation.objects.select_for_update()
                .filter(
                    company_id=company.id,
                    picking_item_id=picking_item.id,
                )
                .first()
            )
            if (
                historical_allocation is not None
                and historical_allocation.status
                == PickingLotAllocation.STATUS_DAMAGED
            ):
                # El daño LT-1E ya retiró físico y reserva; no se libera otra vez.
                continue

        quantity = (
            picking_item.required_quantity
            or picking_item.picked_quantity
            or 0
        )
        if quantity <= 0:
            continue

        warehouse = picking_order.warehouse
        if not warehouse and picking_item.location_id:
            warehouse = getattr(picking_item.location, "warehouse", None)

        stock_query = Stock.objects.select_for_update().filter(
            company=company,
            variant=variant,
        )

        if warehouse:
            if warehouse.company_id != company.id:
                raise ValidationError(
                    "La bodega del picking pertenece a otra empresa."
                )
            stock_query = stock_query.filter(warehouse=warehouse)
        elif picking_order.fulfillment_id:
            raise ValidationError(
                "No se puede liberar un cumplimiento multibodega "
                "sin bodega definida."
            )
        else:
            stock_query = stock_query.filter(
                reserved_stock__gt=0,
            ).order_by("-reserved_stock", "id")

        stock = stock_query.first()
        if not stock:
            raise ValidationError(
                f"No se encontró stock reservado para liberar "
                f"el SKU {variant.sku}."
            )

        if picking_order.fulfillment_id:
            if stock.reserved_stock < quantity:
                raise ValidationError(
                    f"Reserva inconsistente para SKU {variant.sku}. "
                    f"Reservado: {stock.reserved_stock}, "
                    f"requerido para liberar: {quantity}."
                )
            release_quantity = quantity
        else:
            release_quantity = min(stock.reserved_stock, quantity)

        if release_quantity <= 0:
            continue

        if _is_lot_controlled(variant):
            lot_allocation = (
                PickingLotAllocation.objects.select_for_update()
                .filter(
                    company_id=company.id,
                    picking_item_id=picking_item.id,
                )
                .first()
            )
            if lot_allocation is None:
                raise ValidationError(
                    f"El PickingItem #{picking_item.id} del SKU "
                    f"{variant.sku} no tiene asignación de lote."
                )
            if lot_allocation.status != PickingLotAllocation.STATUS_RESERVED:
                raise ValidationError(
                    f"La asignación de lote del PickingItem "
                    f"#{picking_item.id} ya no está reservada."
                )
            if int(lot_allocation.quantity) != int(release_quantity):
                raise ValidationError(
                    "La reserva agregada y la reserva del lote no coinciden."
                )

            balance = LotBalance.objects.select_for_update().get(
                pk=lot_allocation.balance_id,
                company_id=company.id,
            )
            if balance.lot_id != lot_allocation.lot_id:
                raise ValidationError("La asignación apunta a un saldo de otro lote.")
            if balance.reserved_quantity < release_quantity:
                raise ValidationError(
                    f"Reserva de lote insuficiente para "
                    f"{lot_allocation.lot.lot_number}."
                )

            physical_before = int(balance.physical_quantity or 0)
            reserved_before = int(balance.reserved_quantity or 0)
            committed_before = int(balance.committed_quantity or 0)

            balance.reserved_quantity = reserved_before - release_quantity
            balance.full_clean()
            balance.save(
                update_fields=["reserved_quantity", "updated_at"]
            )

            lot_allocation.status = PickingLotAllocation.STATUS_RELEASED
            lot_allocation.save(update_fields=["status", "updated_at"])

            _create_lot_movement(
                company=company,
                lot=lot_allocation.lot,
                balance=balance,
                movement_type=LotMovement.TYPE_RELEASE,
                quantity=release_quantity,
                event_key=(
                    f"picking:{picking_order.id}:"
                    f"item:{picking_item.id}:release"
                ),
                picking_order=picking_order,
                picking_item=picking_item,
                user=user,
                physical_before=physical_before,
                physical_after=balance.physical_quantity,
                reserved_before=reserved_before,
                reserved_after=balance.reserved_quantity,
                committed_before=committed_before,
                committed_after=balance.committed_quantity,
                notes=f"Reserva de lote liberada. Motivo: {reason}",
            )

        stock.reserved_stock -= release_quantity
        stock.save(update_fields=["reserved_stock", "updated_at"])

        check_stock_critical(
            stock=stock,
            source="picking_cancelled",
            reference=(
                f"Picking {picking_order.id} / "
                f"Pedido {order.folio}"
            ),
        )

        _create_stock_movement(
            company=company,
            warehouse=stock.warehouse,
            variant=variant,
            movement_type="release",
            quantity=release_quantity,
            reference=(
                f"Picking {picking_order.id} / Pedido {order.folio}"
            ),
            notes=(
                "Stock reservado liberado por cancelación de picking. "
                f"Motivo: {reason}"
            ),
        )

    picking_order.status = cancelled_status
    picking_order.cancelled_at = timezone.now()
    picking_order.cancel_reason = reason

    update_fields = [
        "status",
        "cancelled_at",
        "cancel_reason",
        "updated_at",
    ]
    if user and getattr(user, "is_authenticated", True):
        picking_order.cancelled_by = user
        update_fields.append("cancelled_by")

    picking_order.save(update_fields=update_fields)

    if not picking_order.fulfillment_id:
        if hasattr(order, "status") and order.status not in [
            "cancelled",
            "shipped",
            "delivered",
        ]:
            order.status = "paid"
            order.save(update_fields=["status", "updated_at"])

    _create_audit_log(
        company=company,
        order=order,
        picking_order=picking_order,
        user=user,
        action="stock_released",
        description=(
            f"Stock reservado liberado para el pedido {order.folio}."
        ),
        metadata={
            "picking_order_id": picking_order.id,
            "fulfillment_id": picking_order.fulfillment_id,
            "order_id": order.id,
            "order_folio": order.folio,
            "reason": reason,
            "legacy_without_warehouse": not bool(picking_order.warehouse_id),
            "multibodega": bool(picking_order.fulfillment_id),
            "lot_aware": True,
        },
    )

    return picking_order



@transaction.atomic
def consume_reserved_stock_for_shipping(
    picking_order,
    user=None,
):
    """
    Consume el agregado y, para SKU controlado, el lote exacto reservado.
    """
    _validate_picking_context(picking_order)

    if not picking_order.warehouse_id:
        raise ValidationError("El picking no tiene bodega asignada.")

    company = picking_order.company
    order = picking_order.order
    warehouse = picking_order.warehouse

    picking_items = list(
        picking_order.items.select_related(
            "variant",
            "location",
            "order_item",
        )
        .all()
        .order_by("id")
    )
    if not picking_items:
        raise ValidationError(
            "No hay productos de picking para consumir stock reservado."
        )

    for picking_item in picking_items:
        variant = picking_item.variant

        if _is_lot_controlled(variant):
            historical_allocation = (
                PickingLotAllocation.objects.select_for_update()
                .filter(
                    company_id=company.id,
                    picking_item_id=picking_item.id,
                )
                .first()
            )
            if (
                historical_allocation is not None
                and historical_allocation.status
                == PickingLotAllocation.STATUS_DAMAGED
            ):
                # LT-1E: el físico dañado ya fue retirado y no debe
                # consumirse otra vez durante shipping.
                continue

        quantity = _validate_positive_internal_quantity(
            picking_item.required_quantity,
            reference=f" en PickingItem #{picking_item.id}",
        )

        stock = (
            Stock.objects.select_for_update()
            .filter(
                company=company,
                warehouse=warehouse,
                variant=variant,
            )
            .first()
        )
        if not stock:
            raise ValidationError(
                f"No existe stock para SKU {variant.sku} "
                f"en bodega {warehouse.name}."
            )
        if stock.reserved_stock < quantity:
            raise ValidationError(
                f"Stock reservado insuficiente para SKU {variant.sku}. "
                f"Reservado actual: {stock.reserved_stock}, "
                f"requerido: {quantity}."
            )
        if stock.physical_stock < quantity:
            raise ValidationError(
                f"Stock físico insuficiente para SKU {variant.sku}. "
                f"Físico actual: {stock.physical_stock}, "
                f"requerido: {quantity}."
            )

        variant_location = None
        if picking_item.location_id:
            variant_location = (
                VariantLocation.objects.select_for_update()
                .filter(
                    company=company,
                    variant=variant,
                    location=picking_item.location,
                    location__company=company,
                    location__warehouse=warehouse,
                )
                .first()
            )
            if variant_location and variant_location.quantity < quantity:
                raise ValidationError(
                    f"Stock insuficiente en ubicación "
                    f"{picking_item.location.bin_code} "
                    f"para SKU {variant.sku}."
                )

        if _is_lot_controlled(variant):
            lot_allocation = (
                PickingLotAllocation.objects.select_for_update()
                .filter(
                    company_id=company.id,
                    picking_item_id=picking_item.id,
                )
                .first()
            )
            if lot_allocation is None:
                raise ValidationError(
                    f"El PickingItem #{picking_item.id} del SKU "
                    f"{variant.sku} no tiene asignación de lote."
                )
            if lot_allocation.status != PickingLotAllocation.STATUS_RESERVED:
                raise ValidationError(
                    "La asignación de lote ya fue liberada, dañada o consumida."
                )

            locked_lot = ProductLot.objects.select_for_update().get(
                pk=lot_allocation.lot_id,
                company_id=company.id,
            )
            if not locked_lot.is_allocatable:
                raise ValidationError(
                    f"El lote {locked_lot.lot_number} está bloqueado, "
                    "en recall o caducado. El envío fue detenido."
                )

            if int(lot_allocation.quantity) != quantity:
                raise ValidationError(
                    "La cantidad reservada por lote no coincide "
                    "con el PickingItem."
                )

            balance = LotBalance.objects.select_for_update().get(
                pk=lot_allocation.balance_id,
                company_id=company.id,
            )
            if balance.location_id != picking_item.location_id:
                raise ValidationError(
                    "El saldo de lote ya no coincide con la ubicación "
                    "del PickingItem."
                )
            if balance.reserved_quantity < quantity:
                raise ValidationError(
                    f"Reserva insuficiente en lote "
                    f"{lot_allocation.lot.lot_number}."
                )
            if balance.physical_quantity < quantity:
                raise ValidationError(
                    f"Físico insuficiente en lote "
                    f"{lot_allocation.lot.lot_number}."
                )

            physical_before = int(balance.physical_quantity or 0)
            reserved_before = int(balance.reserved_quantity or 0)
            committed_before = int(balance.committed_quantity or 0)

            balance.reserved_quantity = reserved_before - quantity
            balance.physical_quantity = physical_before - quantity
            balance.full_clean()
            balance.save(
                update_fields=[
                    "reserved_quantity",
                    "physical_quantity",
                    "updated_at",
                ]
            )

            lot_allocation.status = PickingLotAllocation.STATUS_CONSUMED
            lot_allocation.save(update_fields=["status", "updated_at"])

            _create_lot_movement(
                company=company,
                lot=lot_allocation.lot,
                balance=balance,
                movement_type=LotMovement.TYPE_CONSUME,
                quantity=quantity,
                event_key=(
                    f"picking:{picking_order.id}:"
                    f"item:{picking_item.id}:consume"
                ),
                picking_order=picking_order,
                picking_item=picking_item,
                user=user,
                physical_before=physical_before,
                physical_after=balance.physical_quantity,
                reserved_before=reserved_before,
                reserved_after=balance.reserved_quantity,
                committed_before=committed_before,
                committed_after=balance.committed_quantity,
                notes="Consumo del lote reservado por envío.",
            )

        if variant_location:
            variant_location.quantity = F("quantity") - quantity
            variant_location.save(update_fields=["quantity"])

        stock.reserved_stock = F("reserved_stock") - quantity
        stock.physical_stock = F("physical_stock") - quantity
        stock.save(
            update_fields=[
                "reserved_stock",
                "physical_stock",
                "updated_at",
            ]
        )
        stock.refresh_from_db(
            fields=[
                "physical_stock",
                "reserved_stock",
                "committed_stock",
                "updated_at",
            ]
        )

        check_stock_critical(
            stock=stock,
            source="order_shipped",
            reference=(
                f"Picking {picking_order.id} / "
                f"Pedido {order.folio}"
            ),
        )

        _create_stock_movement(
            company=company,
            warehouse=warehouse,
            variant=variant,
            movement_type="consume_reserved",
            quantity=quantity,
            reference=(
                f"Picking {picking_order.id} / Pedido {order.folio}"
            ),
            notes="Stock reservado consumido por envío del pedido.",
        )

    _create_audit_log(
        company=company,
        order=order,
        picking_order=picking_order,
        user=user,
        action="reserved_stock_consumed",
        description=(
            f"Stock reservado consumido por envío del pedido {order.folio}."
        ),
        metadata={
            "picking_order_id": picking_order.id,
            "fulfillment_id": picking_order.fulfillment_id,
            "warehouse_id": warehouse.id,
            "warehouse_name": warehouse.name,
            "multibodega": bool(picking_order.fulfillment_id),
            "lot_aware": True,
        },
    )

    from accounting.services.inventory_integration import (
        post_shipping_cost_entry,
    )

    post_shipping_cost_entry(
        picking_order=picking_order,
        user=user,
    )

    return picking_order





def _report_controlled_lot_damage(
    *,
    picking_item,
    quantity,
    reason,
    user,
):
    """
    LT-1E: resuelve daño de un PickingItem controlado por lote.

    Cada PickingItem LT-1C representa una sola asignación exacta de lote.
    Para evitar dividir una asignación histórica, el daño debe cubrir el
    PickingItem completo. Si existe stock elegible suficiente se crean
    nuevos PickingItem de reemplazo conservando FEFO y trazabilidad.
    """
    # PostgreSQL: warehouse es nullable. No combinar FOR UPDATE con
    # select_related("warehouse"), porque Django genera LEFT OUTER JOIN
    # y PostgreSQL rechaza bloquear el lado nullable del outer join.
    # El lock de la fila base persiste hasta el final de transaction.atomic().
    locked_picking_order_id = (
        PickingOrder.objects.select_for_update()
        .filter(pk=picking_item.picking_order_id)
        .values_list("pk", flat=True)
        .get()
    )
    picking_order = PickingOrder.objects.select_related(
        "company",
        "order",
        "warehouse",
    ).get(pk=locked_picking_order_id)

    company = picking_order.company
    order = picking_order.order
    warehouse = picking_order.warehouse
    variant = picking_item.variant

    if int(quantity) != int(picking_item.required_quantity or 0):
        raise ValidationError(
            "Para proteger la trazabilidad de un SKU controlado por lote, "
            "el daño debe reportarse por la cantidad completa del renglón "
            "de picking. El renglón ya representa un lote exacto."
        )

    stock = Stock.objects.select_for_update().get(
        company=company,
        warehouse=warehouse,
        variant=variant,
    )

    allocation = (
        PickingLotAllocation.objects.select_for_update()
        .select_related("lot", "balance", "balance__location")
        .get(
            company=company,
            picking_item=picking_item,
        )
    )

    if allocation.status != PickingLotAllocation.STATUS_RESERVED:
        raise ValidationError(
            "La asignación de lote ya no está reservada y no puede "
            "reportarse como daño."
        )

    balance = LotBalance.objects.select_for_update().get(
        pk=allocation.balance_id,
        company=company,
    )

    source_variant_location = VariantLocation.objects.select_for_update().get(
        company=company,
        variant=variant,
        location_id=balance.location_id,
    )

    if balance.reserved_quantity < quantity:
        raise ValidationError("La reserva del lote es insuficiente para reportar daño.")
    if balance.physical_quantity < quantity:
        raise ValidationError("El físico del lote es insuficiente para reportar daño.")
    if source_variant_location.quantity < quantity:
        raise ValidationError(
            "La ubicación agregada no tiene suficiente físico para reportar daño."
        )
    if stock.reserved_stock < quantity or stock.physical_stock < quantity:
        raise ValidationError(
            "El stock agregado es inconsistente con la asignación de lote."
        )

    physical_before = int(balance.physical_quantity or 0)
    reserved_before = int(balance.reserved_quantity or 0)
    committed_before = int(balance.committed_quantity or 0)

    balance.physical_quantity = physical_before - quantity
    balance.reserved_quantity = reserved_before - quantity
    balance.full_clean()
    balance.save(
        update_fields=[
            "physical_quantity",
            "reserved_quantity",
            "updated_at",
        ]
    )

    source_variant_location.quantity = F("quantity") - quantity
    source_variant_location.save(update_fields=["quantity"])

    stock.physical_stock = F("physical_stock") - quantity
    stock.reserved_stock = F("reserved_stock") - quantity
    stock.save(
        update_fields=[
            "physical_stock",
            "reserved_stock",
            "updated_at",
        ]
    )
    stock.refresh_from_db(
        fields=[
            "physical_stock",
            "reserved_stock",
            "committed_stock",
            "updated_at",
        ]
    )

    allocation.status = PickingLotAllocation.STATUS_DAMAGED
    allocation.save(update_fields=["status", "updated_at"])

    _create_lot_movement(
        company=company,
        lot=allocation.lot,
        balance=balance,
        movement_type=LotMovement.TYPE_WASTE,
        quantity=quantity,
        event_key=(
            f"picking:{picking_order.id}:"
            f"item:{picking_item.id}:damage"
        ),
        picking_order=picking_order,
        picking_item=picking_item,
        user=user,
        physical_before=physical_before,
        physical_after=balance.physical_quantity,
        reserved_before=reserved_before,
        reserved_after=balance.reserved_quantity,
        committed_before=committed_before,
        committed_after=balance.committed_quantity,
        notes=reason or "Producto dañado durante picking.",
    )

    picking_item.mark_damaged(
        quantity=quantity,
        note=reason or "Producto dañado durante picking por lote.",
    )

    replacement_items = []
    eligible = _locked_allocatable_lot_balances(
        company=company,
        warehouse=warehouse,
        variant=variant,
    )

    if sum(int(row.allocatable_quantity or 0) for row in eligible) >= quantity:
        remaining = quantity
        next_route_order = (
            PickingItem.objects.filter(picking_order=picking_order)
            .order_by("-route_order")
            .values_list("route_order", flat=True)
            .first()
            or 0
        )

        for candidate in eligible:
            if remaining <= 0:
                break

            candidate = LotBalance.objects.select_for_update().select_related(
                "lot",
                "location",
            ).get(
                pk=candidate.pk,
                company=company,
            )

            take = min(
                remaining,
                int(candidate.allocatable_quantity or 0),
            )
            if take <= 0:
                continue

            physical_before_replacement = int(
                candidate.physical_quantity or 0
            )
            reserved_before_replacement = int(
                candidate.reserved_quantity or 0
            )
            committed_before_replacement = int(
                candidate.committed_quantity or 0
            )

            candidate.reserved_quantity = (
                reserved_before_replacement + take
            )
            candidate.full_clean()
            candidate.save(
                update_fields=["reserved_quantity", "updated_at"]
            )

            next_route_order += 1
            replacement_item = PickingItem.objects.create(
                picking_order=picking_order,
                order_item=picking_item.order_item,
                variant=variant,
                location=candidate.location,
                required_quantity=take,
                picked_quantity=0,
                damaged_quantity=0,
                route_order=next_route_order,
                status=PickingItem.STATUS_PENDING,
                has_issue=False,
                issue_note="",
            )

            replacement_allocation = PickingLotAllocation(
                company=company,
                picking_item=replacement_item,
                lot=candidate.lot,
                balance=candidate,
                quantity=take,
                status=PickingLotAllocation.STATUS_RESERVED,
            )
            replacement_allocation.full_clean()
            replacement_allocation.save()

            _create_lot_movement(
                company=company,
                lot=candidate.lot,
                balance=candidate,
                movement_type=LotMovement.TYPE_RESERVATION,
                quantity=take,
                event_key=(
                    f"picking:{picking_order.id}:"
                    f"item:{replacement_item.id}:reserve-after-damage"
                ),
                picking_order=picking_order,
                picking_item=replacement_item,
                user=user,
                physical_before=physical_before_replacement,
                physical_after=candidate.physical_quantity,
                reserved_before=reserved_before_replacement,
                reserved_after=candidate.reserved_quantity,
                committed_before=committed_before_replacement,
                committed_after=candidate.committed_quantity,
                notes=(
                    "Reserva FEFO de reemplazo después de daño "
                    f"del PickingItem #{picking_item.id}."
                ),
            )

            replacement_items.append(replacement_item)
            remaining -= take

        if remaining != 0:
            raise ValidationError(
                "La reserva de reemplazo por lote quedó incompleta."
            )

        stock.reserved_stock = F("reserved_stock") + quantity
        stock.save(update_fields=["reserved_stock", "updated_at"])

        picking_item.status = PickingItem.STATUS_DAMAGED_RESOLVED
        picking_item.issue_note = (
            (reason or "Producto dañado durante picking por lote.")
            + " Reemplazo FEFO generado."
        )
        picking_item.save(
            update_fields=["status", "issue_note"]
        )

        stock.refresh_from_db(
            fields=[
                "physical_stock",
                "reserved_stock",
                "committed_stock",
                "updated_at",
            ]
        )

    check_stock_critical(
        stock=stock,
        source="damaged_lot_stock",
        reference=(
            f"Picking {picking_order.id} / Pedido {order.folio}"
        ),
    )

    _create_stock_movement(
        company=company,
        warehouse=warehouse,
        variant=variant,
        movement_type="damage",
        quantity=quantity,
        reference=(
            f"Picking {picking_order.id} / Pedido {order.folio}"
        ),
        notes=reason or "Producto dañado durante picking por lote.",
    )

    _create_audit_log(
        company=company,
        order=order,
        picking_order=picking_order,
        picking_item=picking_item,
        user=user,
        action="damaged_lot_stock_reported",
        description=(
            f"Daño por lote reportado para SKU {variant.sku} "
            f"en pedido {order.folio}."
        ),
        metadata={
            "picking_item_id": picking_item.id,
            "lot_id": allocation.lot_id,
            "lot_number": allocation.lot.lot_number,
            "quantity": quantity,
            "replacement_picking_item_ids": [
                row.id for row in replacement_items
            ],
            "reason": reason,
        },
    )

    return {
        "picking_item": picking_item,
        "reassigned": bool(replacement_items),
        "replacement_location": (
            replacement_items[0].location if replacement_items else None
        ),
        "waste_location": None,
        "replacement_items": replacement_items,
    }


@transaction.atomic
def report_damaged_stock_during_picking(
    picking_item,
    quantity,
    reason="",
    user=None,
):
    """
    Reporta producto dañado durante picking.

    Regla:
    - Mueve cantidad de ubicación física a MERMA.
    - Baja physical_stock porque ya no es stock vendible.
    - Si hay stock alternativo, reasigna ubicación.
    - No afecta otra bodega ni otro cumplimiento.
    """

    quantity = _validate_positive_internal_quantity(
        quantity,
        reference=" reportada como daño",
    )

    if quantity > picking_item.required_quantity:
        raise ValidationError(
            "La cantidad dañada no puede superar " "la cantidad requerida."
        )

    if not picking_item.location_id:
        raise ValidationError(
            "Este producto de picking " "no tiene ubicación asignada."
        )

    picking_order = picking_item.picking_order

    _validate_picking_context(picking_order)

    company = picking_order.company
    order = picking_order.order
    warehouse = picking_order.warehouse
    variant = picking_item.variant

    if _is_lot_controlled(variant):
        return _report_controlled_lot_damage(
            picking_item=picking_item,
            quantity=quantity,
            reason=reason,
            user=user,
        )

    if not warehouse:
        raise ValidationError("El picking no tiene bodega asignada.")

    if picking_item.location.warehouse_id != warehouse.id:
        raise ValidationError(
            "La ubicación del producto no pertenece " "a la bodega del picking."
        )

    stock = (
        Stock.objects.select_for_update()
        .filter(
            company=company,
            warehouse=warehouse,
            variant=variant,
        )
        .first()
    )

    if not stock:
        raise ValidationError(
            f"No existe stock para SKU "
            f"{variant.sku} en bodega "
            f"{warehouse.name}."
        )

    source_variant_location = (
        VariantLocation.objects.select_for_update()
        .filter(
            company=company,
            variant=variant,
            location=picking_item.location,
            location__company=company,
            location__warehouse=warehouse,
        )
        .first()
    )

    if not source_variant_location:
        raise ValidationError(
            f"No existe stock por ubicación "
            f"para SKU {variant.sku} en "
            f"{picking_item.location.bin_code}."
        )

    if source_variant_location.quantity < quantity:
        raise ValidationError(
            f"No hay suficiente stock en la ubicación "
            f"{picking_item.location.bin_code} "
            "para reportar daño."
        )

    can_keep_reserved = stock.available_stock >= quantity

    if stock.physical_stock < quantity:
        raise ValidationError(
            f"Stock físico insuficiente "
            f"para reportar daño en "
            f"SKU {variant.sku}."
        )

    waste_location = _get_or_create_waste_location(
        company,
        warehouse,
    )

    (
        waste_variant_location,
        _,
    ) = VariantLocation.objects.select_for_update().get_or_create(
        company=company,
        variant=variant,
        location=waste_location,
        defaults={
            "quantity": 0,
            "is_primary": False,
        },
    )

    source_variant_location.quantity = F("quantity") - quantity

    source_variant_location.save(
        update_fields=[
            "quantity",
        ]
    )

    waste_variant_location.quantity = F("quantity") + quantity

    waste_variant_location.save(
        update_fields=[
            "quantity",
        ]
    )

    stock.physical_stock = F("physical_stock") - quantity

    if not can_keep_reserved:
        if stock.reserved_stock < quantity:
            raise ValidationError(
                f"No se puede ajustar reserva "
                f"del SKU {variant.sku}. "
                f"Reservado actual: "
                f"{stock.reserved_stock}, "
                f"dañado: {quantity}."
            )

        stock.reserved_stock = F("reserved_stock") - quantity

        stock.save(
            update_fields=[
                "physical_stock",
                "reserved_stock",
                "updated_at",
            ]
        )
    else:
        stock.save(
            update_fields=[
                "physical_stock",
                "updated_at",
            ]
        )

    replacement_location = None
    
    stock.refresh_from_db(
        fields=[
            "physical_stock",
            "reserved_stock",
            "committed_stock",
            "updated_at",
        ]
    )

    check_stock_critical(
        stock=stock,
        source="damaged_stock",
        reference=(
            f"Picking {picking_order.id} / "
            f"Pedido {order.folio}"
        ),
    )

    if can_keep_reserved:
        replacement_variant_location = (
            VariantLocation.objects.select_for_update()
            .filter(
                company=company,
                variant=variant,
                location__company=company,
                location__warehouse=warehouse,
                location__is_active=True,
                location__is_virtual=False,
                location__status=(WarehouseLocation.STATUS_ACTIVE),
                quantity__gte=quantity,
            )
            .exclude(
                location=picking_item.location,
            )
            .order_by(
                "-is_primary",
                "location__aisle",
                "location__rack",
                "location__level",
                "location__bin_code",
            )
            .first()
        )

        if replacement_variant_location:
            replacement_location = replacement_variant_location.location

            picking_item.mark_reassigned(
                new_location=replacement_location,
                note=(reason or ("Producto dañado. " "Ubicación reasignada.")),
            )

    if not replacement_location:
        picking_item.mark_damaged(
            quantity=quantity,
            note=(
                reason
                or ("Producto dañado. No hay ubicación " "alternativa disponible.")
            ),
        )

    _create_stock_movement(
        company=company,
        warehouse=warehouse,
        variant=variant,
        movement_type="damage",
        quantity=quantity,
        reference=(f"Picking {picking_order.id} " f"/ Pedido {order.folio}"),
        notes=(reason or "Producto dañado durante picking."),
    )

    _create_audit_log(
        company=company,
        order=order,
        picking_order=picking_order,
        picking_item=picking_item,
        user=user,
        action="damaged_stock_reported",
        description=(
            f"Producto dañado reportado para "
            f"SKU {variant.sku} en picking "
            f"del pedido {order.folio}."
        ),
        metadata={
            "picking_order_id": (picking_order.id),
            "fulfillment_id": (picking_order.fulfillment_id),
            "picking_item_id": (picking_item.id),
            "variant_id": variant.id,
            "sku": variant.sku,
            "quantity": quantity,
            "source_location": (picking_item.location.bin_code),
            "waste_location": (waste_location.bin_code),
            "replacement_location": (
                replacement_location.bin_code if replacement_location else None
            ),
            "reason": reason,
        },
    )

    return {
        "picking_item": picking_item,
        "reassigned": bool(replacement_location),
        "replacement_location": (replacement_location),
        "waste_location": (waste_location),
    }
