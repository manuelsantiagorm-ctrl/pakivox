from __future__ import annotations

from collections import defaultdict

from django.core.exceptions import ValidationError
from django.db import transaction

from inventory.models import (
    LotBalance,
    LotMovement,
    ProductLot,
    ProductVariant,
    Stock,
)
from warehouse.models import VariantLocation, WarehouseLocation


def _normalize_lot_number(value):
    return (value or "").strip().upper()


@transaction.atomic
def adopt_variant_to_lot_control(
    *,
    company,
    variant,
    target_mode,
    allocations,
    user=None,
    allowed_warehouse_ids=None,
):
    """
    Convierte un SKU NORMAL a LOT o LOT_EXPIRY clasificando el 100%
    de su stock físico existente.

    Seguridad:
    - Bloquea variante, Stock y VariantLocation.
    - No permite adopción con reservado/comprometido > 0 porque LT-1C
      todavía no ha clasificado reservas por lote.
    - Stock físico por bodega debe coincidir exactamente con ubicaciones.
    - La clasificación enviada debe coincidir exactamente con cada
      VariantLocation positiva.
    - Todo sucede en una sola transacción.
    """
    if target_mode not in {
        ProductVariant.INVENTORY_CONTROL_LOT,
        ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY,
    }:
        raise ValidationError("Modo de control por lote no válido.")

    variant = (
        ProductVariant.objects.select_for_update()
        .select_related("product", "company")
        .get(
            pk=variant.pk,
            company=company,
        )
    )

    if variant.is_bundle:
        raise ValidationError(
            "Un combo virtual no puede adoptar lote propio; "
            "los lotes pertenecen a sus componentes físicos."
        )

    if variant.inventory_control != ProductVariant.INVENTORY_CONTROL_NORMAL:
        raise ValidationError(
            f"El SKU {variant.sku} ya usa control "
            f"{variant.get_inventory_control_display()}."
        )

    stock_rows = list(
        Stock.objects.select_for_update()
        .filter(
            company=company,
            variant=variant,
        )
        .select_related("warehouse")
        .order_by("warehouse_id", "pk")
    )

    location_rows = list(
        VariantLocation.objects.select_for_update()
        .filter(
            company=company,
            variant=variant,
        )
        .select_related(
            "location",
            "location__warehouse",
        )
        .order_by("location__warehouse_id", "location_id", "pk")
    )

    stock_by_warehouse = {
        row.warehouse_id: int(row.physical_stock or 0)
        for row in stock_rows
    }

    if any(
        int(row.reserved_stock or 0) > 0
        or int(row.committed_stock or 0) > 0
        for row in stock_rows
    ):
        raise ValidationError(
            "No puedes activar lotes mientras exista stock reservado o "
            "comprometido. Libera/cierra esas operaciones antes de adoptar."
        )

    located_by_warehouse = defaultdict(int)

    for row in location_rows:
        located_by_warehouse[row.location.warehouse_id] += int(row.quantity or 0)

    all_warehouse_ids = set(stock_by_warehouse) | set(located_by_warehouse)

    if allowed_warehouse_ids is not None:
        allowed = {int(value) for value in allowed_warehouse_ids}
        inaccessible = {
            warehouse_id
            for warehouse_id in all_warehouse_ids
            if (
                stock_by_warehouse.get(warehouse_id, 0) > 0
                or located_by_warehouse.get(warehouse_id, 0) > 0
            )
            and warehouse_id not in allowed
        }

        if inaccessible:
            raise ValidationError(
                "El SKU tiene stock en una bodega que tu usuario no puede "
                "operar. La adopción debe cubrir todas las bodegas con stock."
            )

    for warehouse_id in sorted(all_warehouse_ids):
        physical = stock_by_warehouse.get(warehouse_id, 0)
        located = located_by_warehouse.get(warehouse_id, 0)

        if physical != located:
            raise ValidationError(
                "Antes de adoptar lotes debes conciliar Stock y ubicaciones. "
                f"Bodega {warehouse_id}: físico={physical}, ubicado={located}."
            )

    positive_locations = {
        row.location_id: row
        for row in location_rows
        if int(row.quantity or 0) > 0
    }

    normalized = []
    grouped_qty = defaultdict(int)
    lot_dates = {}

    for raw in allocations or []:
        try:
            location_id = int(raw.get("location_id"))
            quantity = int(raw.get("quantity"))
        except (TypeError, ValueError):
            raise ValidationError("Ubicación o cantidad de adopción inválida.")

        lot_number = _normalize_lot_number(raw.get("lot_number"))
        manufactured_date = raw.get("manufactured_date")
        expiration_date = raw.get("expiration_date")

        if location_id not in positive_locations:
            raise ValidationError(
                "La clasificación contiene una ubicación sin stock "
                "positivo de este SKU."
            )

        if quantity <= 0:
            raise ValidationError("Cada clasificación debe ser mayor a cero.")

        if not lot_number:
            raise ValidationError("El número de lote es obligatorio.")

        if (
            target_mode == ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY
            and not expiration_date
        ):
            raise ValidationError(
                f"LOT_EXPIRY exige caducidad para el lote {lot_number}."
            )

        if (
            manufactured_date
            and expiration_date
            and manufactured_date > expiration_date
        ):
            raise ValidationError(
                f"La caducidad del lote {lot_number} no puede ser "
                "anterior a su fabricación."
            )

        dates = (manufactured_date, expiration_date)

        if lot_number in lot_dates and lot_dates[lot_number] != dates:
            raise ValidationError(
                f"El lote {lot_number} fue capturado con fechas distintas."
            )

        lot_dates[lot_number] = dates
        grouped_qty[(location_id, lot_number)] += quantity

    submitted_by_location = defaultdict(int)
    for (location_id, lot_number), quantity in grouped_qty.items():
        submitted_by_location[location_id] += quantity

    if not positive_locations and allocations:
        raise ValidationError(
            "El SKU no tiene stock físico que clasificar; no envíes lotes."
        )

    for location_id, location_row in positive_locations.items():
        expected = int(location_row.quantity or 0)
        received = submitted_by_location.get(location_id, 0)

        if received != expected:
            raise ValidationError(
                "La clasificación debe cubrir exactamente el stock de cada "
                f"ubicación. {location_row.location.bin_code}: "
                f"esperado={expected}, clasificado={received}."
            )

    unknown_submitted = set(submitted_by_location) - set(positive_locations)
    if unknown_submitted:
        raise ValidationError("Se recibió una ubicación no válida en la adopción.")

    # Después de validar el 100%, activamos el modo dentro de la misma
    # transacción. Si cualquier creación posterior falla, todo se revierte.
    variant.inventory_control = target_mode
    variant._inventory_control_transition_authorized = True
    variant.full_clean()
    variant.save(
        update_fields=[
            "inventory_control",
            "updated_at",
        ]
    )

    lots_by_number = {}

    for lot_number, (manufactured_date, expiration_date) in lot_dates.items():
        lot = ProductLot(
            company=company,
            variant=variant,
            lot_number=lot_number,
            manufactured_date=manufactured_date,
            expiration_date=expiration_date,
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )
        lot.full_clean()
        lot.save()
        lots_by_number[lot_number] = lot

    for (location_id, lot_number), quantity in sorted(grouped_qty.items()):
        location_row = positive_locations[location_id]
        lot = lots_by_number[lot_number]

        location_status = location_row.location.status

        if location_status == WarehouseLocation.STATUS_QUARANTINE:
            balance_status = LotBalance.STATUS_QUARANTINE
        elif location_status == WarehouseLocation.STATUS_WASTE:
            balance_status = LotBalance.STATUS_WASTE
        elif location_status == WarehouseLocation.STATUS_BLOCKED:
            balance_status = LotBalance.STATUS_BLOCKED
        else:
            balance_status = LotBalance.STATUS_AVAILABLE

        balance = LotBalance(
            company=company,
            lot=lot,
            location=location_row.location,
            status=balance_status,
            physical_quantity=quantity,
            reserved_quantity=0,
            committed_quantity=0,
        )
        balance.full_clean()
        balance.save()

        LotMovement.objects.create(
            company=company,
            lot=lot,
            balance=balance,
            movement_type=LotMovement.TYPE_RECONCILIATION,
            quantity=quantity,
            reference_type="lot_adoption",
            reference_id=variant.pk,
            reference_code=f"ADOPCION-{variant.sku}",
            event_key=(
                f"lot-adoption:{variant.pk}:{location_id}:{lot_number}"
            )[:180],
            physical_before=0,
            physical_after=quantity,
            reserved_before=0,
            reserved_after=0,
            committed_before=0,
            committed_after=0,
            notes=(
                "Clasificación inicial de stock existente al activar "
                "control por lotes."
            ),
            created_by=user if getattr(user, "is_authenticated", False) else None,
        )

    # Verificación posterior: cada ubicación debe seguir cuadrando.
    balance_by_location = defaultdict(int)

    for row in LotBalance.objects.filter(
        company=company,
        lot__variant=variant,
    ).values("location_id", "physical_quantity"):
        balance_by_location[row["location_id"]] += int(row["physical_quantity"] or 0)

    for location_id, location_row in positive_locations.items():
        if balance_by_location.get(location_id, 0) != int(location_row.quantity or 0):
            raise ValidationError(
                "La conciliación final por lote no coincide con VariantLocation."
            )

    for warehouse_id, physical in stock_by_warehouse.items():
        lot_total = sum(
            quantity
            for location_id, quantity in balance_by_location.items()
            if positive_locations.get(location_id)
            and positive_locations[location_id].location.warehouse_id == warehouse_id
        )

        if lot_total != physical:
            raise ValidationError(
                "La conciliación final por lote no coincide con Stock."
            )

    return variant
