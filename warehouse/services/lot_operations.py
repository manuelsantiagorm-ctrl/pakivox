from __future__ import annotations

from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from accounts.warehouse_access import (
    ACCESS_MANAGE,
    get_user_company,
    user_can_access_warehouse,
)
from inventory.models import (
    LotBalance,
    LotMovement,
    ProductLot,
    Stock,
)
from sales.models import Order
from warehouse.models import (
    PickingLotAllocation,
    VariantLocation,
    WarehouseLocation,
)
from warehouse.services.transfer_lots import (
    get_or_create_quarantine_location,
)


def _company(user):
    company = get_user_company(user)
    if company is None:
        raise PermissionDenied("No se detectó una empresa activa.")
    return company


def _request_key(value):
    if value in (None, ""):
        return None
    if isinstance(value, UUID):
        return str(value)
    try:
        return str(UUID(str(value)))
    except Exception as exc:
        raise ValidationError("request_id no es válido.") from exc


def _require_manage(user, warehouse):
    company = _company(user)
    if warehouse.company_id != company.id:
        raise PermissionDenied("La bodega pertenece a otra empresa.")
    if not user_can_access_warehouse(
        user=user,
        warehouse=warehouse,
        required_level=ACCESS_MANAGE,
    ):
        raise PermissionDenied(
            "No tienes nivel manage para esta bodega."
        )
    return company


def _locked_balance_context(*, balance_id, user):
    """
    Bloquea en el orden canónico de inventario:
    Stock -> ProductLot -> LotBalance.

    El primer SELECT es solo una lectura de contexto para resolver company,
    bodega y variante sin tomar locks en joins. Después se revalida todo
    bajo locks de filas base.
    """
    company = _company(user)

    preview = (
        LotBalance.objects.select_related(
            "lot",
            "location",
            "location__warehouse",
        )
        .filter(pk=balance_id, company=company)
        .first()
    )
    if preview is None:
        raise PermissionDenied("El saldo de lote no pertenece a tu empresa.")

    warehouse = preview.location.warehouse
    _require_manage(user, warehouse)

    stock = Stock.objects.select_for_update().get(
        company=company,
        warehouse_id=warehouse.id,
        variant_id=preview.lot.variant_id,
    )

    lot = ProductLot.objects.select_for_update().get(
        pk=preview.lot_id,
        company=company,
    )

    locked_balance_id = (
        LotBalance.objects.select_for_update()
        .filter(pk=balance_id, company=company, lot_id=lot.id)
        .values_list("id", flat=True)
        .first()
    )
    if locked_balance_id is None:
        raise ValidationError(
            "El saldo de lote cambió mientras se intentaba operar."
        )

    source = LotBalance.objects.select_related(
        "lot",
        "location",
        "location__warehouse",
    ).get(
        pk=locked_balance_id,
        company=company,
    )

    if source.location.warehouse_id != warehouse.id:
        raise ValidationError(
            "La bodega del saldo cambió durante la operación."
        )
    if source.lot.variant_id != stock.variant_id:
        raise ValidationError(
            "El SKU del lote no coincide con el stock bloqueado."
        )

    return company, warehouse, stock, lot, source


def _movement(
    *,
    company,
    lot,
    balance,
    movement_type,
    quantity,
    reference_type,
    reference_id,
    reference_code,
    event_key,
    user,
    physical_before=None,
    physical_after=None,
    notes="",
):
    movement = LotMovement(
        company=company,
        lot=lot,
        balance=balance,
        movement_type=movement_type,
        quantity=quantity,
        reference_type=reference_type,
        reference_id=reference_id,
        reference_code=reference_code,
        event_key=event_key,
        physical_before=physical_before,
        physical_after=physical_after,
        reserved_before=(
            balance.reserved_quantity if balance else None
        ),
        reserved_after=(
            balance.reserved_quantity if balance else None
        ),
        committed_before=(
            balance.committed_quantity if balance else None
        ),
        committed_after=(
            balance.committed_quantity if balance else None
        ),
        notes=notes,
        created_by=(
            user if getattr(user, "is_authenticated", False) else None
        ),
    )
    movement.full_clean()
    movement.save()
    return movement


@transaction.atomic
def set_product_lot_status(
    *,
    lot_id,
    status,
    reason,
    user,
    request_id=None,
):
    company = _company(user)
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError("El motivo del cambio de estado es obligatorio.")

    allowed = {
        ProductLot.STATUS_ACTIVE,
        ProductLot.STATUS_BLOCKED,
        ProductLot.STATUS_RECALLED,
    }
    if status not in allowed:
        raise ValidationError("El estado solicitado no es válido.")

    lot = (
        ProductLot.objects.select_for_update()
        .select_related("company", "variant")
        .filter(pk=lot_id, company=company)
        .first()
    )
    if lot is None:
        raise PermissionDenied("El lote no pertenece a tu empresa.")

    warehouse_ids = list(
        LotBalance.objects.filter(
            company=company,
            lot=lot,
            physical_quantity__gt=0,
        )
        .values_list("location__warehouse_id", flat=True)
        .distinct()
    )

    from inventory.models import Warehouse

    warehouses = list(
        Warehouse.objects.filter(
            company=company,
            id__in=warehouse_ids,
        ).order_by("id")
    )

    for warehouse in warehouses:
        _require_manage(user, warehouse)

    key = _request_key(request_id)
    event_key = (
        f"lot-status:{lot.id}:{key}" if key else None
    )
    if event_key and LotMovement.objects.filter(
        company=company,
        event_key=event_key,
    ).exists():
        return lot

    if lot.status == status:
        return lot

    previous = lot.status
    lot.status = status
    lot.status_reason = reason
    lot.status_changed_at = timezone.now()
    lot.status_changed_by = user
    lot.full_clean()
    lot.save(
        update_fields=[
            "status",
            "status_reason",
            "status_changed_at",
            "status_changed_by",
            "updated_at",
        ]
    )

    movement_type = (
        LotMovement.TYPE_RECALL
        if status == ProductLot.STATUS_RECALLED
        else (
            LotMovement.TYPE_BLOCK
            if status == ProductLot.STATUS_BLOCKED
            else LotMovement.TYPE_UNBLOCK
        )
    )

    _movement(
        company=company,
        lot=lot,
        balance=None,
        movement_type=movement_type,
        quantity=0,
        reference_type="product_lot",
        reference_id=lot.id,
        reference_code=f"Lote {lot.lot_number}",
        event_key=event_key,
        user=user,
        notes=f"{previous} -> {status}. {reason}",
    )

    return lot


@transaction.atomic
def register_customer_return_to_quarantine(
    *,
    lot_id,
    order_id,
    warehouse_id,
    quantity,
    reason,
    user,
    request_id=None,
):
    company = _company(user)

    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise ValidationError("La cantidad devuelta no es válida.")
    if quantity <= 0:
        raise ValidationError("La cantidad devuelta debe ser mayor a cero.")

    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError("El motivo de devolución es obligatorio.")

    lot_preview = (
        ProductLot.objects.select_related("variant")
        .filter(pk=lot_id, company=company)
        .first()
    )
    if lot_preview is None:
        raise PermissionDenied("El lote no pertenece a tu empresa.")

    from inventory.models import Warehouse

    warehouse = Warehouse.objects.filter(
        pk=warehouse_id,
        company=company,
    ).first()
    if warehouse is None:
        raise PermissionDenied("La bodega no pertenece a tu empresa.")
    _require_manage(user, warehouse)

    # Orden ajeno a inventario primero; después se conserva el orden canónico
    # de inventario Stock -> ProductLot -> detalle.
    order = Order.objects.select_for_update().filter(
        pk=order_id,
        company=company,
    ).first()
    if order is None:
        raise PermissionDenied("El pedido no pertenece a esta empresa.")

    stock, _ = Stock.objects.select_for_update().get_or_create(
        company=company,
        warehouse=warehouse,
        variant_id=lot_preview.variant_id,
        defaults={
            "physical_stock": 0,
            "reserved_stock": 0,
            "committed_stock": 0,
        },
    )

    lot = ProductLot.objects.select_for_update().get(
        pk=lot_preview.id,
        company=company,
    )
    if lot.variant_id != stock.variant_id:
        raise ValidationError(
            "El lote cambió de SKU durante la devolución."
        )

    consumed = (
        PickingLotAllocation.objects.filter(
            company=company,
            lot=lot,
            status=PickingLotAllocation.STATUS_CONSUMED,
            picking_item__picking_order__order=order,
            picking_item__picking_order__company=company,
        ).aggregate(total=Sum("quantity"))["total"]
        or 0
    )
    if consumed <= 0:
        raise ValidationError(
            "Ese pedido no tiene unidades consumidas de este lote."
        )

    already_returned = (
        LotMovement.objects.filter(
            company=company,
            lot=lot,
            movement_type=LotMovement.TYPE_RETURN_QUARANTINE,
            reference_type="customer_return_order",
            reference_id=order.id,
        ).aggregate(total=Sum("quantity"))["total"]
        or 0
    )
    if already_returned + quantity > consumed:
        raise ValidationError(
            "La devolución acumulada supera la cantidad enviada "
            "de este lote al pedido."
        )

    key = _request_key(request_id)
    event_key = (
        f"customer-return:{lot.id}:{order.id}:{key}"
        if key else None
    )
    if event_key:
        existing = LotMovement.objects.filter(
            company=company,
            event_key=event_key,
        ).first()
        if existing is not None:
            return existing.balance

    quarantine_location = get_or_create_quarantine_location(
        company=company,
        warehouse=warehouse,
    )

    variant_location, _ = (
        VariantLocation.objects.select_for_update().get_or_create(
            company=company,
            variant=lot.variant,
            location=quarantine_location,
            defaults={"quantity": 0, "is_primary": False},
        )
    )

    balance, _ = LotBalance.objects.select_for_update().get_or_create(
        company=company,
        lot=lot,
        location=quarantine_location,
        defaults={
            "status": LotBalance.STATUS_QUARANTINE,
            "physical_quantity": 0,
            "reserved_quantity": 0,
            "committed_quantity": 0,
        },
    )
    if balance.status != LotBalance.STATUS_QUARANTINE:
        raise ValidationError(
            "El saldo de devolución no está en cuarentena."
        )

    physical_before = int(balance.physical_quantity or 0)

    balance.physical_quantity = physical_before + quantity
    balance.full_clean()
    balance.save(update_fields=["physical_quantity", "updated_at"])

    variant_location.quantity = F("quantity") + quantity
    variant_location.save(update_fields=["quantity"])

    stock.physical_stock = F("physical_stock") + quantity
    stock.save(update_fields=["physical_stock", "updated_at"])

    _movement(
        company=company,
        lot=lot,
        balance=balance,
        movement_type=LotMovement.TYPE_RETURN_QUARANTINE,
        quantity=quantity,
        reference_type="customer_return_order",
        reference_id=order.id,
        reference_code=f"Pedido {order.folio}",
        event_key=event_key,
        user=user,
        physical_before=physical_before,
        physical_after=balance.physical_quantity,
        notes=reason,
    )

    return balance


@transaction.atomic
def quarantine_available_stock(
    *,
    balance_id,
    quantity,
    reason,
    user,
    request_id=None,
):
    company = _company(user)
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise ValidationError("La cantidad no es válida.")
    if quantity <= 0:
        raise ValidationError("La cantidad debe ser mayor a cero.")

    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError("El motivo de cuarentena es obligatorio.")

    company, warehouse, stock, lot, source = _locked_balance_context(
        balance_id=balance_id,
        user=user,
    )

    if source.status != LotBalance.STATUS_AVAILABLE:
        raise ValidationError(
            "Solo un saldo disponible puede enviarse a cuarentena."
        )
    if source.available_quantity < quantity:
        raise ValidationError(
            "No hay cantidad libre suficiente para poner en cuarentena."
        )

    key = _request_key(request_id)
    event_key = (
        f"lot-quarantine:{source.id}:{key}" if key else None
    )
    if event_key and LotMovement.objects.filter(
        company=company,
        event_key=event_key,
    ).exists():
        return source

    quarantine_location = get_or_create_quarantine_location(
        company=company,
        warehouse=warehouse,
    )

    source_variant_location = VariantLocation.objects.select_for_update().get(
        company=company,
        variant=source.lot.variant,
        location=source.location,
    )
    quarantine_variant_location, _ = (
        VariantLocation.objects.select_for_update().get_or_create(
            company=company,
            variant=source.lot.variant,
            location=quarantine_location,
            defaults={"quantity": 0, "is_primary": False},
        )
    )
    target, _ = LotBalance.objects.select_for_update().get_or_create(
        company=company,
        lot=source.lot,
        location=quarantine_location,
        defaults={
            "status": LotBalance.STATUS_QUARANTINE,
            "physical_quantity": 0,
            "reserved_quantity": 0,
            "committed_quantity": 0,
        },
    )
    if target.status != LotBalance.STATUS_QUARANTINE:
        raise ValidationError(
            "La ubicación de cuarentena tiene un saldo inconsistente."
        )

    source_before = int(source.physical_quantity or 0)
    target_before = int(target.physical_quantity or 0)

    source.physical_quantity = source_before - quantity
    source.full_clean()
    source.save(update_fields=["physical_quantity", "updated_at"])

    target.physical_quantity = target_before + quantity
    target.full_clean()
    target.save(update_fields=["physical_quantity", "updated_at"])

    source_variant_location.quantity = F("quantity") - quantity
    source_variant_location.save(update_fields=["quantity"])
    quarantine_variant_location.quantity = F("quantity") + quantity
    quarantine_variant_location.save(update_fields=["quantity"])

    _movement(
        company=company,
        lot=source.lot,
        balance=target,
        movement_type=LotMovement.TYPE_BLOCK,
        quantity=quantity,
        reference_type="lot_balance_quarantine",
        reference_id=source.id,
        reference_code=f"Lote {source.lot.lot_number}",
        event_key=event_key,
        user=user,
        physical_before=target_before,
        physical_after=target.physical_quantity,
        notes=f"Cuarentena local: {reason}",
    )

    # Stock agregado no cambia: sigue siendo físico de la bodega.
    stock.refresh_from_db()
    return target


@transaction.atomic
def release_quarantine_stock(
    *,
    balance_id,
    destination_location_id,
    quantity,
    reason,
    user,
    request_id=None,
):
    company = _company(user)
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise ValidationError("La cantidad no es válida.")
    if quantity <= 0:
        raise ValidationError("La cantidad debe ser mayor a cero.")

    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError("El motivo de liberación es obligatorio.")

    company, warehouse, stock, lot, source = _locked_balance_context(
        balance_id=balance_id,
        user=user,
    )

    if source.status != LotBalance.STATUS_QUARANTINE:
        raise ValidationError("El saldo no está en cuarentena.")
    if source.physical_quantity < quantity:
        raise ValidationError("La cuarentena no tiene cantidad suficiente.")
    if not lot.is_allocatable:
        raise ValidationError(
            "El lote está bloqueado, en recall o caducado y no puede "
            "liberarse a inventario disponible."
        )

    destination = (
        WarehouseLocation.objects.select_for_update()
        .filter(
            pk=destination_location_id,
            company=company,
            warehouse=warehouse,
        )
        .first()
    )
    if destination is None or not destination.is_pickable:
        raise ValidationError(
            "Selecciona una ubicación activa y pickeable de la misma bodega."
        )

    key = _request_key(request_id)
    event_key = (
        f"quarantine-release:{source.id}:{key}" if key else None
    )
    if event_key and LotMovement.objects.filter(
        company=company,
        event_key=event_key,
    ).exists():
        return source

    source_variant_location = VariantLocation.objects.select_for_update().get(
        company=company,
        variant=source.lot.variant,
        location=source.location,
    )
    target_variant_location, _ = (
        VariantLocation.objects.select_for_update().get_or_create(
            company=company,
            variant=source.lot.variant,
            location=destination,
            defaults={"quantity": 0, "is_primary": False},
        )
    )
    target, _ = LotBalance.objects.select_for_update().get_or_create(
        company=company,
        lot=source.lot,
        location=destination,
        defaults={
            "status": LotBalance.STATUS_AVAILABLE,
            "physical_quantity": 0,
            "reserved_quantity": 0,
            "committed_quantity": 0,
        },
    )
    if target.status != LotBalance.STATUS_AVAILABLE:
        raise ValidationError(
            "El saldo destino no está disponible para picking."
        )

    source_before = int(source.physical_quantity or 0)
    target_before = int(target.physical_quantity or 0)

    source.physical_quantity = source_before - quantity
    source.full_clean()
    source.save(update_fields=["physical_quantity", "updated_at"])

    target.physical_quantity = target_before + quantity
    target.full_clean()
    target.save(update_fields=["physical_quantity", "updated_at"])

    source_variant_location.quantity = F("quantity") - quantity
    source_variant_location.save(update_fields=["quantity"])
    target_variant_location.quantity = F("quantity") + quantity
    target_variant_location.save(update_fields=["quantity"])

    _movement(
        company=company,
        lot=source.lot,
        balance=target,
        movement_type=LotMovement.TYPE_QUARANTINE_RELEASE,
        quantity=quantity,
        reference_type="lot_balance_quarantine",
        reference_id=source.id,
        reference_code=f"Lote {source.lot.lot_number}",
        event_key=event_key,
        user=user,
        physical_before=target_before,
        physical_after=target.physical_quantity,
        notes=reason,
    )

    return target


@transaction.atomic
def dispose_quarantine_stock(
    *,
    balance_id,
    quantity,
    reason,
    user,
    request_id=None,
):
    company = _company(user)
    try:
        quantity = int(quantity)
    except (TypeError, ValueError):
        raise ValidationError("La cantidad no es válida.")
    if quantity <= 0:
        raise ValidationError("La cantidad debe ser mayor a cero.")

    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError("El motivo de desecho es obligatorio.")

    company, warehouse, stock, lot, source = _locked_balance_context(
        balance_id=balance_id,
        user=user,
    )

    if source.status != LotBalance.STATUS_QUARANTINE:
        raise ValidationError("Solo se puede desechar desde cuarentena.")
    if source.physical_quantity < quantity:
        raise ValidationError("La cuarentena no tiene cantidad suficiente.")

    if stock.physical_stock < quantity:
        raise ValidationError(
            "El stock agregado es menor que la cantidad a desechar."
        )

    key = _request_key(request_id)
    event_key = (
        f"quarantine-waste:{source.id}:{key}" if key else None
    )
    if event_key and LotMovement.objects.filter(
        company=company,
        event_key=event_key,
    ).exists():
        return source

    source_variant_location = VariantLocation.objects.select_for_update().get(
        company=company,
        variant=source.lot.variant,
        location=source.location,
    )
    if source_variant_location.quantity < quantity:
        raise ValidationError(
            "La ubicación de cuarentena es inconsistente."
        )

    before = int(source.physical_quantity or 0)
    source.physical_quantity = before - quantity
    source.full_clean()
    source.save(update_fields=["physical_quantity", "updated_at"])

    source_variant_location.quantity = F("quantity") - quantity
    source_variant_location.save(update_fields=["quantity"])

    stock.physical_stock = F("physical_stock") - quantity
    stock.save(update_fields=["physical_stock", "updated_at"])

    _movement(
        company=company,
        lot=source.lot,
        balance=source,
        movement_type=LotMovement.TYPE_WASTE,
        quantity=quantity,
        reference_type="lot_balance_quarantine",
        reference_id=source.id,
        reference_code=f"Lote {source.lot.lot_number}",
        event_key=event_key,
        user=user,
        physical_before=before,
        physical_after=source.physical_quantity,
        notes=reason,
    )

    return source
