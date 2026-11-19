from __future__ import annotations

from datetime import date

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import F, Q
from django.utils import timezone

from inventory.models import (
    LotBalance,
    LotMovement,
    ProductLot,
    ProductVariant,
    Stock,
)
from warehouse.models import (
    TransferLotAllocation,
    VariantLocation,
    WarehouseLocation,
)


def is_lot_controlled(variant):
    return (
        getattr(
            variant,
            "inventory_control",
            ProductVariant.INVENTORY_CONTROL_NORMAL,
        )
        != ProductVariant.INVENTORY_CONTROL_NORMAL
    )


def _lot_sort_key(balance):
    expiration = balance.lot.expiration_date
    return (
        1 if expiration is None else 0,
        expiration or date.max,
        balance.lot.lot_number,
        balance.location.bin_code,
        balance.id,
    )


def _eligible_origin_balances(*, company, warehouse, variant):
    today = timezone.localdate()

    lot_ids = list(
        ProductLot.objects.select_for_update()
        .filter(
            company=company,
            variant=variant,
            status=ProductLot.STATUS_ACTIVE,
        )
        .filter(
            Q(expiration_date__isnull=True)
            | Q(expiration_date__gte=today)
        )
        .order_by(
            F("expiration_date").asc(nulls_last=True),
            "lot_number",
            "id",
        )
        .values_list("id", flat=True)
    )

    if (
        variant.inventory_control
        == ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY
    ):
        lot_ids = list(
            ProductLot.objects.select_for_update()
            .filter(
                company=company,
                variant=variant,
                status=ProductLot.STATUS_ACTIVE,
                expiration_date__isnull=False,
                expiration_date__gte=today,
            )
            .order_by("expiration_date", "lot_number", "id")
            .values_list("id", flat=True)
        )

    if not lot_ids:
        return []

    location_ids = list(
        WarehouseLocation.objects.select_for_update()
        .filter(
            company=company,
            warehouse=warehouse,
            status=WarehouseLocation.STATUS_ACTIVE,
            is_active=True,
            is_virtual=False,
        )
        .order_by("id")
        .values_list("id", flat=True)
    )

    if not location_ids:
        return []

    balance_ids = list(
        LotBalance.objects.select_for_update()
        .filter(
            company=company,
            lot_id__in=lot_ids,
            location_id__in=location_ids,
            status=LotBalance.STATUS_AVAILABLE,
        )
        .order_by("lot_id", "location_id", "id")
        .values_list("id", flat=True)
    )

    balances = list(
        LotBalance.objects.select_related(
            "lot",
            "location",
            "location__warehouse",
        ).filter(
            company=company,
            id__in=balance_ids,
        )
    )

    result = []
    for balance in balances:
        if balance.lot.variant_id != variant.id:
            raise ValidationError("Se detectó un saldo de lote de otro SKU.")
        if balance.location.warehouse_id != warehouse.id:
            raise ValidationError("Se detectó un saldo de lote de otra bodega.")
        if balance.allocatable_quantity <= 0:
            continue
        result.append(balance)

    result.sort(key=_lot_sort_key)
    return result


def _movement(
    *,
    company,
    lot,
    balance,
    movement_type,
    quantity,
    event_key,
    transfer,
    transfer_item,
    user,
    physical_before=None,
    physical_after=None,
    reserved_before=None,
    reserved_after=None,
    notes="",
):
    movement = LotMovement(
        company=company,
        lot=lot,
        balance=balance,
        movement_type=movement_type,
        quantity=quantity,
        reference_type="warehouse_transfer_item",
        reference_id=transfer_item.id,
        reference_code=f"Traspaso {transfer.folio}",
        event_key=event_key,
        physical_before=physical_before,
        physical_after=physical_after,
        reserved_before=reserved_before,
        reserved_after=reserved_after,
        committed_before=balance.committed_quantity if balance else None,
        committed_after=balance.committed_quantity if balance else None,
        notes=notes,
        created_by=(
            user if getattr(user, "is_authenticated", False) else None
        ),
    )
    movement.full_clean()
    movement.save()
    return movement


def reserve_transfer_item_lots(
    *,
    company,
    transfer,
    item,
    user=None,
):
    if not is_lot_controlled(item.variant):
        return []

    if item.lot_allocations.exists():
        raise ValidationError(
            f"El SKU {item.variant.sku} ya tiene asignaciones de lote."
        )

    quantity = int(item.requested_quantity or 0)
    balances = _eligible_origin_balances(
        company=company,
        warehouse=transfer.origin_warehouse,
        variant=item.variant,
    )

    if sum(int(row.allocatable_quantity or 0) for row in balances) < quantity:
        raise ValidationError(
            f"No hay lote elegible suficiente para el SKU "
            f"{item.variant.sku} en la bodega de origen."
        )

    remaining = quantity
    allocations = []

    # En una reversa de traspaso controlada, se intenta conservar los mismos
    # lotes recibidos originalmente antes de caer al FEFO general.
    preferred = []
    if transfer.reversal_of_id:
        original_allocations = (
            TransferLotAllocation.objects.select_for_update()
            .select_related(
                "lot",
                "destination_balance",
                "destination_balance__location",
            )
            .filter(
                company=company,
                transfer_item__transfer_id=transfer.reversal_of_id,
                transfer_item__variant_id=item.variant_id,
                received_quantity__gt=0,
                destination_balance__isnull=False,
            )
            .order_by("id")
        )
        for original in original_allocations:
            if (
                original.destination_balance.location.warehouse_id
                == transfer.origin_warehouse_id
            ):
                preferred.append(
                    (
                        original.lot_id,
                        original.destination_balance_id,
                        int(original.received_quantity or 0),
                    )
                )

    ordered_balances = []
    used_ids = set()

    for lot_id, balance_id, preferred_qty in preferred:
        for balance in balances:
            if (
                balance.id == balance_id
                and balance.lot_id == lot_id
                and balance.id not in used_ids
            ):
                ordered_balances.append(
                    (balance, min(preferred_qty, balance.allocatable_quantity))
                )
                used_ids.add(balance.id)
                break

    for balance in balances:
        if balance.id not in used_ids:
            ordered_balances.append((balance, balance.allocatable_quantity))

    for balance_ref, cap in ordered_balances:
        if remaining <= 0:
            break

        balance = (
            LotBalance.objects.select_for_update()
            .select_related("lot", "location")
            .get(
                pk=balance_ref.pk,
                company=company,
            )
        )
        take = min(
            remaining,
            int(cap or 0),
            int(balance.allocatable_quantity or 0),
        )
        if take <= 0:
            continue

        physical_before = int(balance.physical_quantity or 0)
        reserved_before = int(balance.reserved_quantity or 0)

        balance.reserved_quantity = reserved_before + take
        balance.full_clean()
        balance.save(
            update_fields=["reserved_quantity", "updated_at"]
        )

        allocation = TransferLotAllocation(
            company=company,
            transfer_item=item,
            lot=balance.lot,
            source_balance=balance,
            quantity=take,
            status=TransferLotAllocation.STATUS_RESERVED,
        )
        allocation.full_clean()
        allocation.save()
        allocations.append(allocation)

        _movement(
            company=company,
            lot=balance.lot,
            balance=balance,
            movement_type=LotMovement.TYPE_RESERVATION,
            quantity=take,
            event_key=(
                f"transfer:{transfer.id}:allocation:{allocation.id}:reserve"
            ),
            transfer=transfer,
            transfer_item=item,
            user=user,
            physical_before=physical_before,
            physical_after=balance.physical_quantity,
            reserved_before=reserved_before,
            reserved_after=balance.reserved_quantity,
            notes="Reserva exacta de lote para traspaso.",
        )

        remaining -= take

    if remaining != 0:
        raise ValidationError(
            f"La reserva por lote del SKU {item.variant.sku} "
            "quedó incompleta."
        )

    return allocations


def release_transfer_item_lots(
    *,
    company,
    transfer,
    item,
    user=None,
):
    if not is_lot_controlled(item.variant):
        return []

    allocations = list(
        TransferLotAllocation.objects.select_for_update()
        .select_related("lot", "source_balance")
        .filter(
            company=company,
            transfer_item=item,
            status=TransferLotAllocation.STATUS_RESERVED,
        )
        .order_by("id")
    )

    released = []

    for allocation in allocations:
        balance = LotBalance.objects.select_for_update().get(
            pk=allocation.source_balance_id,
            company=company,
        )
        quantity = int(allocation.quantity or 0)

        if balance.reserved_quantity < quantity:
            raise ValidationError(
                "La reserva del lote es menor que la asignación "
                "del traspaso."
            )

        physical_before = int(balance.physical_quantity or 0)
        reserved_before = int(balance.reserved_quantity or 0)

        balance.reserved_quantity = reserved_before - quantity
        balance.full_clean()
        balance.save(
            update_fields=["reserved_quantity", "updated_at"]
        )

        allocation.status = TransferLotAllocation.STATUS_RELEASED
        allocation.save(update_fields=["status", "updated_at"])

        _movement(
            company=company,
            lot=allocation.lot,
            balance=balance,
            movement_type=LotMovement.TYPE_RELEASE,
            quantity=quantity,
            event_key=(
                f"transfer:{transfer.id}:allocation:{allocation.id}:release"
            ),
            transfer=transfer,
            transfer_item=item,
            user=user,
            physical_before=physical_before,
            physical_after=balance.physical_quantity,
            reserved_before=reserved_before,
            reserved_after=balance.reserved_quantity,
            notes="Liberación de lote por cancelación de traspaso.",
        )
        released.append(allocation)

    return released


def dispatch_transfer_item_lots(
    *,
    company,
    transfer,
    item,
    user=None,
):
    if not is_lot_controlled(item.variant):
        return []

    allocations = list(
        TransferLotAllocation.objects.select_for_update()
        .select_related(
            "lot",
            "source_balance",
            "source_balance__location",
        )
        .filter(
            company=company,
            transfer_item=item,
            status=TransferLotAllocation.STATUS_RESERVED,
        )
        .order_by("id")
    )

    if sum(int(row.quantity or 0) for row in allocations) != int(
        item.reserved_quantity or 0
    ):
        raise ValidationError(
            f"Las asignaciones de lote del SKU {item.variant.sku} "
            "no coinciden con su reserva agregada."
        )

    for allocation in allocations:
        balance = LotBalance.objects.select_for_update().get(
            pk=allocation.source_balance_id,
            company=company,
        )
        quantity = int(allocation.quantity or 0)

        if balance.reserved_quantity < quantity:
            raise ValidationError("Reserva de lote insuficiente para despacho.")
        if balance.physical_quantity < quantity:
            raise ValidationError("Físico de lote insuficiente para despacho.")

        variant_location = VariantLocation.objects.select_for_update().get(
            company=company,
            variant=item.variant,
            location_id=balance.location_id,
        )
        if variant_location.quantity < quantity:
            raise ValidationError(
                "La ubicación agregada es inconsistente con el lote."
            )

        physical_before = int(balance.physical_quantity or 0)
        reserved_before = int(balance.reserved_quantity or 0)

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

        variant_location.quantity = F("quantity") - quantity
        variant_location.save(update_fields=["quantity"])

        allocation.status = TransferLotAllocation.STATUS_IN_TRANSIT
        allocation.save(update_fields=["status", "updated_at"])

        _movement(
            company=company,
            lot=allocation.lot,
            balance=balance,
            movement_type=LotMovement.TYPE_TRANSFER_OUT,
            quantity=quantity,
            event_key=(
                f"transfer:{transfer.id}:allocation:{allocation.id}:out"
            ),
            transfer=transfer,
            transfer_item=item,
            user=user,
            physical_before=physical_before,
            physical_after=balance.physical_quantity,
            reserved_before=reserved_before,
            reserved_after=balance.reserved_quantity,
            notes=(
                f"Salida por traspaso hacia "
                f"{transfer.destination_warehouse.name}."
            ),
        )

    return allocations


def get_or_create_quarantine_location(*, company, warehouse):
    location = (
        WarehouseLocation.objects.select_for_update()
        .filter(
            company=company,
            warehouse=warehouse,
            status=WarehouseLocation.STATUS_QUARANTINE,
            is_virtual=True,
            aisle="VIRTUAL",
            rack="CUARENTENA",
        )
        .order_by("id")
        .first()
    )

    if location is None:
        location = WarehouseLocation(
            company=company,
            warehouse=warehouse,
            aisle="VIRTUAL",
            rack="CUARENTENA",
            level="",
            bin_code="VIRTUALCUARENTENA",
            status=WarehouseLocation.STATUS_QUARANTINE,
            is_virtual=True,
            is_active=True,
            blocked_reason="Cuarentena operativa por lotes.",
        )
        location.full_clean()
        location.save()

    return location


def receive_transfer_lot_allocation(
    *,
    company,
    transfer,
    allocation,
    received,
    damaged,
    rejected,
    missing,
    destination_location,
    notes,
    user=None,
):
    preview = (
        TransferLotAllocation.objects.select_related(
            "lot",
            "transfer_item",
            "transfer_item__variant",
        )
        .filter(
            pk=allocation.pk,
            company=company,
            transfer_item__transfer=transfer,
        )
        .first()
    )
    if preview is None:
        raise ValidationError(
            "La asignación de lote no pertenece a este traspaso."
        )

    item = preview.transfer_item
    variant = item.variant

    # Orden canónico compatible con reserva/picking y LT-1B:
    # Stock -> ProductLot -> TransferLotAllocation -> ubicación/balances.
    stock, _ = Stock.objects.select_for_update().get_or_create(
        company=company,
        warehouse=transfer.destination_warehouse,
        variant=variant,
        defaults={
            "physical_stock": 0,
            "reserved_stock": 0,
            "committed_stock": 0,
        },
    )

    locked_lot = ProductLot.objects.select_for_update().get(
        pk=preview.lot_id,
        company=company,
    )

    locked_allocation_id = (
        TransferLotAllocation.objects.select_for_update()
        .filter(
            pk=preview.pk,
            company=company,
            transfer_item__transfer=transfer,
            lot_id=locked_lot.id,
        )
        .values_list("id", flat=True)
        .first()
    )
    if locked_allocation_id is None:
        raise ValidationError(
            "La asignación cambió mientras se intentaba recibir."
        )

    allocation = TransferLotAllocation.objects.select_related(
        "lot",
        "transfer_item",
        "transfer_item__variant",
        "destination_balance",
        "destination_balance__location",
    ).get(
        pk=locked_allocation_id,
        company=company,
    )

    if allocation.transfer_item_id != item.id:
        raise ValidationError(
            "La asignación cambió de detalle durante la recepción."
        )
    if allocation.lot_id != locked_lot.id:
        raise ValidationError(
            "La asignación cambió de lote durante la recepción."
        )

    if allocation.status not in {
        TransferLotAllocation.STATUS_IN_TRANSIT,
        TransferLotAllocation.STATUS_PARTIAL,
    }:
        raise ValidationError(
            "La asignación de lote no está disponible para recepción."
        )

    if locked_lot.is_expired and int(received or 0) > 0:
        raise ValidationError(
            f"El lote {locked_lot.lot_number} caducó durante el tránsito. "
            "No puede recibirse como conforme ni entrar a inventario disponible; "
            "registra esa cantidad como rechazada para enviarla a cuarentena."
        )

    values = [int(received), int(damaged), int(rejected), int(missing)]
    if any(value < 0 for value in values):
        raise ValidationError("Las cantidades de lote no pueden ser negativas.")
    if sum(values) <= 0:
        raise ValidationError("Debes contabilizar alguna cantidad del lote.")

    new_received = allocation.received_quantity + received
    new_damaged = allocation.damaged_quantity + damaged
    new_rejected = allocation.rejected_quantity + rejected
    new_missing = allocation.missing_quantity + missing

    accounted = (
        new_received + new_damaged + new_rejected + new_missing
    )
    if accounted > allocation.quantity:
        raise ValidationError(
            f"El lote {allocation.lot.lot_number} supera "
            "la cantidad despachada."
        )

    physical_arrival = received + damaged + rejected

    destination_balance = allocation.destination_balance

    if received > 0:
        if destination_location is None:
            raise ValidationError(
                "Selecciona una ubicación destino para la cantidad conforme."
            )

        destination_location = (
            WarehouseLocation.objects.select_for_update()
            .filter(
                pk=destination_location.id,
                company=company,
                warehouse=transfer.destination_warehouse,
            )
            .first()
        )
        if destination_location is None:
            raise ValidationError(
                "La ubicación destino ya no pertenece a esta bodega."
            )

        if (
            allocation.destination_balance_id
            and allocation.destination_balance.location_id
            != destination_location.id
        ):
            raise ValidationError(
                "La recepción parcial de un mismo lote debe continuar "
                "en la misma ubicación destino."
            )

        if destination_location.company_id != company.id:
            raise ValidationError("La ubicación destino pertenece a otra empresa.")
        if (
            destination_location.warehouse_id
            != transfer.destination_warehouse_id
        ):
            raise ValidationError("La ubicación destino pertenece a otra bodega.")
        if not destination_location.is_pickable:
            raise ValidationError(
                "La ubicación destino debe estar activa y disponible para picking."
            )

        destination_variant_location, _ = (
            VariantLocation.objects.select_for_update().get_or_create(
                company=company,
                variant=variant,
                location=destination_location,
                defaults={"quantity": 0, "is_primary": False},
            )
        )
        destination_variant_location.quantity = F("quantity") + received
        destination_variant_location.save(update_fields=["quantity"])

        destination_balance, _ = (
            LotBalance.objects.select_for_update().get_or_create(
                company=company,
                lot=allocation.lot,
                location=destination_location,
                defaults={
                    "status": LotBalance.STATUS_AVAILABLE,
                    "physical_quantity": 0,
                    "reserved_quantity": 0,
                    "committed_quantity": 0,
                },
            )
        )
        if destination_balance.status != LotBalance.STATUS_AVAILABLE:
            raise ValidationError(
                "El saldo destino del lote no está disponible."
            )
        before = int(destination_balance.physical_quantity or 0)
        destination_balance.physical_quantity = before + received
        destination_balance.full_clean()
        destination_balance.save(
            update_fields=["physical_quantity", "updated_at"]
        )

        _movement(
            company=company,
            lot=allocation.lot,
            balance=destination_balance,
            movement_type=LotMovement.TYPE_TRANSFER_IN,
            quantity=received,
            event_key=(
                f"transfer:{transfer.id}:allocation:{allocation.id}:"
                f"in:{new_received}"
            ),
            transfer=transfer,
            transfer_item=item,
            user=user,
            physical_before=before,
            physical_after=destination_balance.physical_quantity,
            reserved_before=destination_balance.reserved_quantity,
            reserved_after=destination_balance.reserved_quantity,
            notes=notes or "Recepción conforme por traspaso.",
        )

    quarantine_quantity = damaged + rejected
    if quarantine_quantity > 0:
        quarantine_location = get_or_create_quarantine_location(
            company=company,
            warehouse=transfer.destination_warehouse,
        )
        quarantine_variant_location, _ = (
            VariantLocation.objects.select_for_update().get_or_create(
                company=company,
                variant=variant,
                location=quarantine_location,
                defaults={"quantity": 0, "is_primary": False},
            )
        )
        quarantine_variant_location.quantity = (
            F("quantity") + quarantine_quantity
        )
        quarantine_variant_location.save(update_fields=["quantity"])

        quarantine_balance, _ = (
            LotBalance.objects.select_for_update().get_or_create(
                company=company,
                lot=allocation.lot,
                location=quarantine_location,
                defaults={
                    "status": LotBalance.STATUS_QUARANTINE,
                    "physical_quantity": 0,
                    "reserved_quantity": 0,
                    "committed_quantity": 0,
                },
            )
        )
        if quarantine_balance.status != LotBalance.STATUS_QUARANTINE:
            raise ValidationError(
                "El saldo virtual de cuarentena tiene estado inconsistente."
            )
        before = int(quarantine_balance.physical_quantity or 0)
        quarantine_balance.physical_quantity = (
            before + quarantine_quantity
        )
        quarantine_balance.full_clean()
        quarantine_balance.save(
            update_fields=["physical_quantity", "updated_at"]
        )

        _movement(
            company=company,
            lot=allocation.lot,
            balance=quarantine_balance,
            movement_type=LotMovement.TYPE_RETURN_QUARANTINE,
            quantity=quarantine_quantity,
            event_key=(
                f"transfer:{transfer.id}:allocation:{allocation.id}:"
                f"quarantine:{new_damaged}:{new_rejected}"
            ),
            transfer=transfer,
            transfer_item=item,
            user=user,
            physical_before=before,
            physical_after=quarantine_balance.physical_quantity,
            reserved_before=0,
            reserved_after=0,
            notes=(
                f"Dañado={damaged}; rechazado={rejected}. "
                + (notes or "")
            ).strip(),
        )

    if physical_arrival > 0:
        stock.physical_stock = F("physical_stock") + physical_arrival
        stock.save(update_fields=["physical_stock", "updated_at"])

    allocation.received_quantity = new_received
    allocation.damaged_quantity = new_damaged
    allocation.rejected_quantity = new_rejected
    allocation.missing_quantity = new_missing
    allocation.destination_balance = destination_balance
    allocation.status = (
        TransferLotAllocation.STATUS_CLOSED
        if accounted == allocation.quantity
        else TransferLotAllocation.STATUS_PARTIAL
    )
    allocation.full_clean()
    allocation.save(
        update_fields=[
            "received_quantity",
            "damaged_quantity",
            "rejected_quantity",
            "missing_quantity",
            "destination_balance",
            "status",
            "updated_at",
        ]
    )

    return allocation
