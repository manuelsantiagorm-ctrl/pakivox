from collections import defaultdict
from uuid import UUID

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from accounts.warehouse_access import (
    ACCESS_MANAGE,
    ACCESS_OPERATE,
    ACCESS_VIEW,
    get_user_company,
    user_can_access_warehouse,
)
from inventory.models import (
    LotBalance,
    LotMovement,
    ProductLot,
    ProductVariant,
    Stock,
    StockMovement,
)
from inventory.services import check_stock_critical

from .models import (
    WarehouseTransfer,
    WarehouseTransferEvent,
    TransferLotAllocation,
    VariantLocation,
    WarehouseLocation,
    WarehouseTransferItem,
)

from .services.transfer_lots import (
    dispatch_transfer_item_lots,
    is_lot_controlled,
    receive_transfer_lot_allocation,
    release_transfer_item_lots,
    reserve_transfer_item_lots,
)

# ============================================================
# EXCEPCIONES
# ============================================================


class WarehouseTransferError(ValidationError):
    """
    Error controlado de negocio para el módulo de traspasos.
    """


class WarehouseTransferStateError(WarehouseTransferError):
    """
    Se intenta ejecutar una transición desde un estado no permitido.
    """


class WarehouseTransferStockError(WarehouseTransferError):
    """
    No existe inventario suficiente o el stock es inconsistente.
    """


# ============================================================
# HELPERS GENERALES
# ============================================================


def _authenticated_user(user):
    if not user or not getattr(user, "is_authenticated", False):
        raise PermissionDenied("Debes iniciar sesión para operar traspasos.")

    return user


def _user_company(user):
    _authenticated_user(user)

    company = get_user_company(user)

    if company is None:
        raise PermissionDenied("No se detectó una empresa activa asignada al usuario.")

    return company


def _normalize_request_id(request_id):
    if request_id in (None, ""):
        return None

    if isinstance(request_id, UUID):
        return request_id

    try:
        return UUID(str(request_id))
    except (TypeError, ValueError, AttributeError):
        raise WarehouseTransferError("El identificador de la petición no es válido.")


def _normalize_ip(ip_address):
    value = str(ip_address or "").strip()
    return value or None


def _normalize_text(value, field_name, max_length=None, required=False):
    value = str(value or "").strip()

    if required and not value:
        raise WarehouseTransferError(
            {field_name: (f"El campo {field_name.replace('_', ' ')} es obligatorio.")}
        )

    if max_length is not None and len(value) > max_length:
        raise WarehouseTransferError(
            {
                field_name: (
                    f"El campo {field_name.replace('_', ' ')} "
                    f"no puede superar {max_length} caracteres."
                )
            }
        )

    return value


def _assert_warehouse_belongs_to_company(warehouse, company, field_name):
    if warehouse is None:
        raise WarehouseTransferError({field_name: "La bodega indicada no existe."})

    if warehouse.company_id != company.id:
        raise PermissionDenied("La bodega indicada no pertenece a tu empresa.")


def _require_warehouse_access(user, warehouse, required_level):
    company = _user_company(user)

    _assert_warehouse_belongs_to_company(
        warehouse=warehouse,
        company=company,
        field_name="warehouse",
    )

    if not user_can_access_warehouse(
        user=user,
        warehouse=warehouse,
        required_level=required_level,
    ):
        raise PermissionDenied(
            "No tienes el nivel de acceso necesario para esta bodega."
        )


def _require_transfer_company(transfer, company):
    if transfer.company_id != company.id:
        raise PermissionDenied("El traspaso no pertenece a tu empresa.")


def _lock_transfer(transfer_id, company):
    try:
        transfer_id = int(transfer_id)
    except (TypeError, ValueError):
        raise WarehouseTransferError("El identificador del traspaso no es válido.")

    transfer = (
        WarehouseTransfer.objects.select_for_update()
        .select_related(
            "company",
            "origin_warehouse",
            "destination_warehouse",
            "requested_by",
        )
        .filter(
            pk=transfer_id,
            company=company,
        )
        .first()
    )

    if transfer is None:
        raise PermissionDenied("El traspaso no existe o no pertenece a tu empresa.")

    return transfer


def _event_exists(transfer, action, request_id):
    if request_id is None:
        return False

    return WarehouseTransferEvent.objects.filter(
        company_id=transfer.company_id,
        transfer=transfer,
        action=action,
        request_id=request_id,
    ).exists()


def _create_event(
    *,
    transfer,
    action,
    user,
    description,
    previous_status="",
    new_status="",
    ip_address=None,
    request_id=None,
    metadata=None,
):
    event = WarehouseTransferEvent(
        company=transfer.company,
        transfer=transfer,
        action=action,
        previous_status=previous_status or "",
        new_status=new_status or "",
        user=user,
        description=str(description).strip(),
        ip_address=_normalize_ip(ip_address),
        request_id=_normalize_request_id(request_id),
        metadata=metadata or {},
    )

    event.full_clean()
    event.save()

    return event


def _create_stock_movement(
    *,
    transfer,
    stock,
    movement_type,
    quantity,
    user,
    stock_before,
    stock_after,
    notes,
):
    movement = StockMovement(
        company=transfer.company,
        warehouse=stock.warehouse,
        variant=stock.variant,
        movement_type=movement_type,
        quantity=int(quantity),
        reference=f"Traspaso {transfer.folio}",
        notes=notes,
        stock_before=int(stock_before),
        stock_after=int(stock_after),
        created_by=user,
    )

    # No se ocultan excepciones. Si el Kardex falla, toda la
    # transacción debe hacer rollback.
    movement.full_clean()
    movement.save()

    return movement


def _generate_locked_folio(company):
    """
    Genera un folio dentro de una transacción que ya bloqueó la empresa.

    Formato:
        TR-260727-0001
    """
    date_part = timezone.localdate().strftime("%y%m%d")
    base = f"TR-{date_part}"

    last_folio = (
        WarehouseTransfer.objects.filter(
            company=company,
            folio__startswith=f"{base}-",
        )
        .order_by("-folio")
        .values_list("folio", flat=True)
        .first()
    )

    last_number = 0

    if last_folio:
        try:
            last_number = int(last_folio.rsplit("-", 1)[-1])
        except (TypeError, ValueError):
            last_number = 0

    return f"{base}-{last_number + 1:04d}"


def _normalize_items(items, company):
    """
    Formatos aceptados:

    [
        {"variant_id": 5, "quantity": 10, "notes": ""},
        {"variant": variante, "requested_quantity": 10},
    ]
    """
    if not items:
        raise WarehouseTransferError(
            {"items": "Debes agregar al menos un producto al traspaso."}
        )

    normalized = []
    quantities_by_variant = defaultdict(int)
    notes_by_variant = {}

    for position, raw_item in enumerate(items, start=1):
        if not isinstance(raw_item, dict):
            raise WarehouseTransferError(
                {
                    "items": (
                        f"El producto número {position} " "tiene un formato inválido."
                    )
                }
            )

        variant = raw_item.get("variant")
        variant_id = raw_item.get("variant_id")

        if variant is not None:
            variant_id = variant.pk

        try:
            variant_id = int(variant_id)
        except (TypeError, ValueError):
            raise WarehouseTransferError(
                {
                    "items": (
                        f"El producto número {position} "
                        "no tiene una variante válida."
                    )
                }
            )

        raw_quantity = raw_item.get(
            "quantity",
            raw_item.get("requested_quantity"),
        )

        try:
            quantity = int(raw_quantity)
        except (TypeError, ValueError):
            raise WarehouseTransferError(
                {
                    "items": (
                        f"La cantidad del producto número {position} " "no es válida."
                    )
                }
            )

        if quantity <= 0:
            raise WarehouseTransferError(
                {
                    "items": (
                        f"La cantidad del producto número {position} "
                        "debe ser mayor a cero."
                    )
                }
            )

        notes = _normalize_text(
            raw_item.get("notes"),
            field_name="notes",
        )

        quantities_by_variant[variant_id] += quantity

        if notes:
            previous_notes = notes_by_variant.get(variant_id, "")
            notes_by_variant[variant_id] = f"{previous_notes}\n{notes}".strip()

    variant_ids = list(quantities_by_variant.keys())

    variants = {
        variant.pk: variant
        for variant in ProductVariant.objects.select_related("product").filter(
            company=company,
            pk__in=variant_ids,
            is_active=True,
        )
    }

    if len(variants) != len(variant_ids):
        raise PermissionDenied(
            "Uno o más productos no existen, están inactivos "
            "o no pertenecen a tu empresa."
        )

    for variant_id in variant_ids:
        variant = variants[variant_id]

        if getattr(variant, "is_bundle", False):
            raise WarehouseTransferError(
                {
                    "items": (
                        f"El SKU {variant.sku} es un combo virtual y "
                        "no puede traspasarse directamente."
                    )
                }
            )

        normalized.append(
            {
                "variant": variant,
                "quantity": quantities_by_variant[variant_id],
                "notes": notes_by_variant.get(variant_id, ""),
            }
        )

    return normalized


def _touch_transfer(transfer):
    transfer.version += 1


# ============================================================
# CREAR TRASPASO
# ============================================================


@transaction.atomic
def create_transfer(
    *,
    user,
    origin_warehouse,
    destination_warehouse,
    reason,
    items,
    priority=WarehouseTransfer.PRIORITY_NORMAL,
    notes="",
    ip_address=None,
    request_id=None,
):
    """
    Crea un traspaso en borrador.

    No reserva ni modifica inventario.
    """
    company = _user_company(user)
    request_id = _normalize_request_id(request_id)

    _assert_warehouse_belongs_to_company(
        origin_warehouse,
        company,
        "origin_warehouse",
    )
    _assert_warehouse_belongs_to_company(
        destination_warehouse,
        company,
        "destination_warehouse",
    )

    if origin_warehouse.pk == destination_warehouse.pk:
        raise WarehouseTransferError(
            {
                "destination_warehouse": (
                    "La bodega de destino debe ser diferente " "a la bodega de origen."
                )
            }
        )

    _require_warehouse_access(
        user=user,
        warehouse=origin_warehouse,
        required_level=ACCESS_OPERATE,
    )
    _require_warehouse_access(
        user=user,
        warehouse=destination_warehouse,
        required_level=ACCESS_VIEW,
    )

    valid_priorities = {value for value, _label in WarehouseTransfer.PRIORITY_CHOICES}

    if priority not in valid_priorities:
        raise WarehouseTransferError(
            {"priority": "La prioridad seleccionada no es válida."}
        )

    reason = _normalize_text(
        reason,
        field_name="reason",
        max_length=250,
        required=True,
    )
    notes = _normalize_text(
        notes,
        field_name="notes",
    )
    normalized_items = _normalize_items(
        items=items,
        company=company,
    )

    # Serializa la generación del folio por empresa.
    locked_company = company.__class__.objects.select_for_update().get(pk=company.pk)

    folio = _generate_locked_folio(locked_company)

    try:
        transfer = WarehouseTransfer(
            company=locked_company,
            folio=folio,
            origin_warehouse=origin_warehouse,
            destination_warehouse=destination_warehouse,
            status=WarehouseTransfer.STATUS_DRAFT,
            priority=priority,
            reason=reason,
            notes=notes,
            requested_by=user,
            created_ip=_normalize_ip(ip_address),
        )
        transfer.full_clean()
        transfer.save()
    except IntegrityError as exc:
        raise WarehouseTransferError(
            "No fue posible generar un folio único para el traspaso."
        ) from exc

    transfer_items = []

    for item_data in normalized_items:
        item = WarehouseTransferItem(
            company=locked_company,
            transfer=transfer,
            variant=item_data["variant"],
            requested_quantity=item_data["quantity"],
            notes=item_data["notes"],
        )
        item.full_clean()
        item.save()
        transfer_items.append(item)

    _create_event(
        transfer=transfer,
        action=WarehouseTransferEvent.ACTION_CREATED,
        user=user,
        description=(f"Traspaso {transfer.folio} creado en borrador."),
        previous_status="",
        new_status=WarehouseTransfer.STATUS_DRAFT,
        ip_address=ip_address,
        request_id=request_id,
        metadata={
            "origin_warehouse_id": origin_warehouse.pk,
            "destination_warehouse_id": destination_warehouse.pk,
            "item_count": len(transfer_items),
        },
    )

    return transfer


# ============================================================
# SOLICITAR TRASPASO
# ============================================================


@transaction.atomic
def request_transfer(
    *,
    transfer_id,
    user,
    ip_address=None,
    request_id=None,
):
    """
    Cambia el traspaso de DRAFT a REQUESTED.

    Todavía no modifica inventario.
    """
    company = _user_company(user)
    request_id = _normalize_request_id(request_id)

    transfer = _lock_transfer(
        transfer_id=transfer_id,
        company=company,
    )

    _require_transfer_company(transfer, company)
    _require_warehouse_access(
        user=user,
        warehouse=transfer.origin_warehouse,
        required_level=ACCESS_OPERATE,
    )

    if _event_exists(
        transfer,
        WarehouseTransferEvent.ACTION_REQUESTED,
        request_id,
    ):
        return transfer

    if transfer.status == WarehouseTransfer.STATUS_REQUESTED:
        return transfer

    if transfer.status != WarehouseTransfer.STATUS_DRAFT:
        raise WarehouseTransferStateError(
            "Solamente un traspaso en borrador puede solicitarse."
        )

    items = list(
        WarehouseTransferItem.objects.select_for_update()
        .select_related("variant")
        .filter(
            company=company,
            transfer=transfer,
        )
        .order_by("pk")
    )

    if not items:
        raise WarehouseTransferError("El traspaso no contiene productos.")

    for item in items:
        if item.variant.company_id != company.id:
            raise PermissionDenied("El traspaso contiene un producto de otra empresa.")

        if not item.variant.is_active:
            raise WarehouseTransferError(f"El SKU {item.variant.sku} está inactivo.")

        if item.requested_quantity <= 0:
            raise WarehouseTransferError(
                f"El SKU {item.variant.sku} tiene una cantidad inválida."
            )

    previous_status = transfer.status
    transfer.status = WarehouseTransfer.STATUS_REQUESTED
    transfer.requested_at = timezone.now()
    _touch_transfer(transfer)

    transfer.full_clean()
    transfer.save(
        update_fields=[
            "status",
            "requested_at",
            "version",
            "updated_at",
        ]
    )

    _create_event(
        transfer=transfer,
        action=WarehouseTransferEvent.ACTION_REQUESTED,
        user=user,
        description=f"Traspaso {transfer.folio} solicitado.",
        previous_status=previous_status,
        new_status=transfer.status,
        ip_address=ip_address,
        request_id=request_id,
        metadata={
            "item_count": len(items),
        },
    )

    return transfer


# ============================================================
# RESERVAR STOCK
# ============================================================


@transaction.atomic
def reserve_transfer(
    *,
    transfer_id,
    user,
    ip_address=None,
    request_id=None,
):
    """
    Reserva todo el inventario requerido en la bodega de origen.

    La operación es todo o nada:
    - Si falta un solo SKU, no se reserva ninguno.
    - Si falla el Kardex, se revierte todo.
    """
    company = _user_company(user)
    request_id = _normalize_request_id(request_id)

    transfer = _lock_transfer(
        transfer_id=transfer_id,
        company=company,
    )

    _require_transfer_company(transfer, company)
    _require_warehouse_access(
        user=user,
        warehouse=transfer.origin_warehouse,
        required_level=ACCESS_MANAGE,
    )

    if _event_exists(
        transfer,
        WarehouseTransferEvent.ACTION_RESERVED,
        request_id,
    ):
        return transfer

    if transfer.status == WarehouseTransfer.STATUS_RESERVED:
        return transfer

    if transfer.status != WarehouseTransfer.STATUS_REQUESTED:
        raise WarehouseTransferStateError(
            "Solamente un traspaso solicitado puede reservar inventario."
        )

    items = list(
        WarehouseTransferItem.objects.select_for_update()
        .select_related("variant")
        .filter(
            company=company,
            transfer=transfer,
        )
        .order_by("variant_id", "pk")
    )

    if not items:
        raise WarehouseTransferError("El traspaso no contiene productos para reservar.")

    variant_ids = [item.variant_id for item in items]

    stocks = {
        stock.variant_id: stock
        for stock in (
            Stock.objects.select_for_update()
            .select_related(
                "company",
                "warehouse",
                "variant",
                "variant__product",
            )
            .filter(
                company=company,
                warehouse=transfer.origin_warehouse,
                variant_id__in=variant_ids,
            )
            .order_by("variant_id", "pk")
        )
    }

    shortages = []

    # Primero valida todos los SKUs. Todavía no modifica nada.
    for item in items:
        if item.company_id != company.id:
            raise PermissionDenied("El traspaso contiene un detalle de otra empresa.")

        if item.variant.company_id != company.id:
            raise PermissionDenied("El traspaso contiene un producto de otra empresa.")

        if item.reserved_quantity != 0:
            raise WarehouseTransferError(
                f"El SKU {item.variant.sku} ya contiene una reserva previa."
            )

        stock = stocks.get(item.variant_id)
        available = stock.available_stock if stock else 0

        if available < item.requested_quantity:
            shortages.append(
                {
                    "sku": item.variant.sku,
                    "requested": item.requested_quantity,
                    "available": available,
                    "missing": item.requested_quantity - available,
                }
            )

    if shortages:
        details = "; ".join(
            (
                f"{row['sku']}: solicitado {row['requested']}, "
                f"disponible {row['available']}, "
                f"faltan {row['missing']}"
            )
            for row in shortages
        )

        raise WarehouseTransferStockError(
            f"Stock insuficiente en la bodega de origen. {details}"
        )

    # LT-1E: Stock ya está bloqueado. Para SKU controlado se reserva
    # ahora el detalle exacto por lote antes de modificar el agregado.
    for item in items:
        if is_lot_controlled(item.variant):
            reserve_transfer_item_lots(
                company=company,
                transfer=transfer,
                item=item,
                user=user,
            )

    movement_ids = []

    for item in items:
        stock = stocks[item.variant_id]
        stock_before = stock.physical_stock

        stock.reserved_stock += item.requested_quantity
        stock.full_clean()
        stock.save(
            update_fields=[
                "reserved_stock",
                "updated_at",
            ]
        )

        item.reserved_quantity = item.requested_quantity
        item.full_clean()
        item.save(
            update_fields=[
                "reserved_quantity",
                "updated_at",
            ]
        )

        movement = _create_stock_movement(
            transfer=transfer,
            stock=stock,
            movement_type="reservation",
            quantity=item.requested_quantity,
            user=user,
            stock_before=stock_before,
            stock_after=stock.physical_stock,
            notes=(
                f"Reserva para traspaso {transfer.folio}: "
                f"{transfer.origin_warehouse.name} → "
                f"{transfer.destination_warehouse.name}."
            ),
        )
        movement_ids.append(movement.pk)

        check_stock_critical(
            stock=stock,
            source="warehouse_transfer_reservation",
            reference=f"Traspaso {transfer.folio}",
        )

    previous_status = transfer.status
    transfer.status = WarehouseTransfer.STATUS_RESERVED
    transfer.authorized_by = user
    transfer.reserved_at = timezone.now()
    _touch_transfer(transfer)

    transfer.full_clean()
    transfer.save(
        update_fields=[
            "status",
            "authorized_by",
            "reserved_at",
            "version",
            "updated_at",
        ]
    )

    _create_event(
        transfer=transfer,
        action=WarehouseTransferEvent.ACTION_RESERVED,
        user=user,
        description=(f"Inventario reservado para el traspaso {transfer.folio}."),
        previous_status=previous_status,
        new_status=transfer.status,
        ip_address=ip_address,
        request_id=request_id,
        metadata={
            "movement_ids": movement_ids,
            "item_count": len(items),
        },
    )

    return transfer


# ============================================================
# CANCELAR TRASPASO
# ============================================================


@transaction.atomic
def cancel_transfer(
    *,
    transfer_id,
    user,
    cancellation_reason,
    ip_address=None,
    request_id=None,
):
    """
    Cancela un traspaso en DRAFT, REQUESTED o RESERVED.

    Cuando está RESERVED libera todas las reservas de origen.
    """
    company = _user_company(user)
    request_id = _normalize_request_id(request_id)

    transfer = _lock_transfer(
        transfer_id=transfer_id,
        company=company,
    )

    _require_transfer_company(transfer, company)
    _require_warehouse_access(
        user=user,
        warehouse=transfer.origin_warehouse,
        required_level=ACCESS_MANAGE,
    )

    cancellation_reason = _normalize_text(
        cancellation_reason,
        field_name="cancellation_reason",
        max_length=500,
        required=True,
    )

    if _event_exists(
        transfer,
        WarehouseTransferEvent.ACTION_CANCELLED,
        request_id,
    ):
        return transfer

    if transfer.status == WarehouseTransfer.STATUS_CANCELLED:
        return transfer

    allowed_statuses = {
        WarehouseTransfer.STATUS_DRAFT,
        WarehouseTransfer.STATUS_REQUESTED,
        WarehouseTransfer.STATUS_RESERVED,
    }

    if transfer.status not in allowed_statuses:
        raise WarehouseTransferStateError(
            "Este traspaso ya no puede cancelarse. "
            "Después del despacho debe utilizarse un traspaso de reversa."
        )

    previous_status = transfer.status
    movement_ids = []

    items = list(
        WarehouseTransferItem.objects.select_for_update()
        .select_related("variant")
        .filter(
            company=company,
            transfer=transfer,
        )
        .order_by("variant_id", "pk")
    )

    if previous_status == WarehouseTransfer.STATUS_RESERVED:
        variant_ids = [item.variant_id for item in items if item.reserved_quantity > 0]

        stocks = {
            stock.variant_id: stock
            for stock in (
                Stock.objects.select_for_update()
                .select_related(
                    "company",
                    "warehouse",
                    "variant",
                    "variant__product",
                )
                .filter(
                    company=company,
                    warehouse=transfer.origin_warehouse,
                    variant_id__in=variant_ids,
                )
                .order_by("variant_id", "pk")
            )
        }

        for item in items:
            quantity = item.reserved_quantity

            if quantity <= 0:
                continue

            stock = stocks.get(item.variant_id)

            if stock is None:
                raise WarehouseTransferStockError(
                    f"No existe el stock reservado del SKU "
                    f"{item.variant.sku} en la bodega de origen."
                )

            if stock.reserved_stock < quantity:
                raise WarehouseTransferStockError(
                    f"La reserva global del SKU {item.variant.sku} "
                    "es menor que la reserva registrada por el traspaso."
                )

            if is_lot_controlled(item.variant):
                release_transfer_item_lots(
                    company=company,
                    transfer=transfer,
                    item=item,
                    user=user,
                )

            stock_before = stock.physical_stock

            stock.reserved_stock -= quantity
            stock.full_clean()
            stock.save(
                update_fields=[
                    "reserved_stock",
                    "updated_at",
                ]
            )

            item.reserved_quantity = 0
            item.full_clean()
            item.save(
                update_fields=[
                    "reserved_quantity",
                    "updated_at",
                ]
            )

            movement = _create_stock_movement(
                transfer=transfer,
                stock=stock,
                movement_type="release",
                quantity=quantity,
                user=user,
                stock_before=stock_before,
                stock_after=stock.physical_stock,
                notes=(
                    f"Liberación por cancelación del traspaso "
                    f"{transfer.folio}. Motivo: {cancellation_reason}"
                ),
            )
            movement_ids.append(movement.pk)

            check_stock_critical(
                stock=stock,
                source="warehouse_transfer_cancelled",
                reference=f"Traspaso {transfer.folio}",
            )

        _create_event(
            transfer=transfer,
            action=(WarehouseTransferEvent.ACTION_RESERVATION_RELEASED),
            user=user,
            description=(
                f"Reservas liberadas por cancelación del " f"traspaso {transfer.folio}."
            ),
            previous_status=previous_status,
            new_status=previous_status,
            ip_address=ip_address,
            request_id=None,
            metadata={
                "movement_ids": movement_ids,
            },
        )

    transfer.status = WarehouseTransfer.STATUS_CANCELLED
    transfer.cancelled_at = timezone.now()

    cancellation_note = f"Cancelación: {cancellation_reason}"

    if transfer.notes:
        transfer.notes = f"{transfer.notes}\n\n{cancellation_note}"
    else:
        transfer.notes = cancellation_note

    _touch_transfer(transfer)

    transfer.full_clean()
    transfer.save(
        update_fields=[
            "status",
            "cancelled_at",
            "notes",
            "version",
            "updated_at",
        ]
    )

    _create_event(
        transfer=transfer,
        action=WarehouseTransferEvent.ACTION_CANCELLED,
        user=user,
        description=(
            f"Traspaso {transfer.folio} cancelado. " f"Motivo: {cancellation_reason}"
        ),
        previous_status=previous_status,
        new_status=transfer.status,
        ip_address=ip_address,
        request_id=request_id,
        metadata={
            "released_movement_ids": movement_ids,
        },
    )

    return transfer


# ============================================================
# HELPERS DE DESPACHO Y RECEPCIÓN
# ============================================================


def _choice_value(model, field_name, *candidates):
    """
    Obtiene un valor real de choices sin depender de etiquetas visibles.

    Esto permite mantener el servicio compatible con los nombres de Kardex y
    auditoría definidos en los modelos del proyecto.
    """
    field = model._meta.get_field(field_name)
    valid_values = {value for value, _label in field.flatchoices}

    for candidate in candidates:
        if candidate in valid_values:
            return candidate

    raise WarehouseTransferError(
        f"El modelo {model.__name__} no contiene ninguno de estos valores "
        f"para {field_name}: {', '.join(candidates)}."
    )


def _event_action(*candidates):
    return _choice_value(WarehouseTransferEvent, "action", *candidates)


def _movement_type(*candidates):
    return _choice_value(StockMovement, "movement_type", *candidates)


def _locked_transfer_items(transfer, company):
    items = list(
        WarehouseTransferItem.objects.select_for_update()
        .select_related("variant", "variant__product")
        .filter(company=company, transfer=transfer)
        .order_by("variant_id", "pk")
    )

    if not items:
        raise WarehouseTransferError("El traspaso no contiene productos.")

    for item in items:
        if item.company_id != company.id or item.variant.company_id != company.id:
            raise PermissionDenied(
                "El traspaso contiene un detalle o producto de otra empresa."
            )

    return items


def _normalize_receipt_rows(rows, items_by_variant):
    if not rows:
        raise WarehouseTransferError(
            {"items": "Debes indicar al menos un producto recibido."}
        )

    normalized = defaultdict(
        lambda: {
            "received": 0,
            "damaged": 0,
            "rejected": 0,
            "missing": 0,
            "notes": [],
        }
    )

    for position, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise WarehouseTransferError(
                {"items": f"La recepción número {position} tiene formato inválido."}
            )

        raw_variant_id = row.get("variant_id")
        if row.get("variant") is not None:
            raw_variant_id = row["variant"].pk

        try:
            variant_id = int(raw_variant_id)
        except (TypeError, ValueError):
            raise WarehouseTransferError(
                {"items": f"La recepción número {position} no tiene variante válida."}
            )

        if variant_id not in items_by_variant:
            raise WarehouseTransferError(
                {"items": "Se intentó recibir un producto ajeno al traspaso."}
            )

        values = {}
        for input_name, short_name in (
            ("received_quantity", "received"),
            ("damaged_quantity", "damaged"),
            ("rejected_quantity", "rejected"),
            ("missing_quantity", "missing"),
        ):
            try:
                value = int(row.get(input_name, 0) or 0)
            except (TypeError, ValueError):
                raise WarehouseTransferError(
                    {
                        "items": (
                            f"La cantidad {input_name} de la recepción "
                            f"número {position} no es válida."
                        )
                    }
                )

            if value < 0:
                raise WarehouseTransferError(
                    {"items": "Las cantidades recibidas no pueden ser negativas."}
                )
            values[short_name] = value

        if sum(values.values()) <= 0:
            raise WarehouseTransferError(
                {
                    "items": (
                        f"La recepción número {position} debe registrar "
                        "alguna cantidad."
                    )
                }
            )

        target = normalized[variant_id]
        for key, value in values.items():
            target[key] += value

        note = _normalize_text(row.get("notes"), field_name="notes")
        if note:
            target["notes"].append(note)

    return normalized


def _item_quantity(item, field_name):
    if not hasattr(item, field_name):
        raise WarehouseTransferError(
            f"El modelo WarehouseTransferItem no contiene {field_name}."
        )
    return int(getattr(item, field_name) or 0)


def _save_item_quantities(item, values, extra_notes=""):
    update_fields = []

    for field_name, value in values.items():
        if not hasattr(item, field_name):
            if value:
                raise WarehouseTransferError(
                    f"El modelo WarehouseTransferItem no contiene {field_name}."
                )
            continue
        setattr(item, field_name, int(value))
        update_fields.append(field_name)

    if extra_notes:
        item.notes = (
            f"{item.notes}\n{extra_notes}".strip() if item.notes else extra_notes
        )
        update_fields.append("notes")

    item.full_clean()
    if hasattr(item, "updated_at"):
        update_fields.append("updated_at")
    item.save(update_fields=list(dict.fromkeys(update_fields)))


# ============================================================
# INICIAR DESPACHO
# ============================================================


@transaction.atomic
def start_dispatch_transfer(
    *,
    transfer_id,
    user,
    ip_address=None,
    request_id=None,
):
    """
    Cambia RESERVED a DISPATCHING sin descontar inventario todavía.
    """
    company = _user_company(user)
    request_id = _normalize_request_id(request_id)
    transfer = _lock_transfer(transfer_id, company)

    _require_transfer_company(transfer, company)
    _require_warehouse_access(
        user=user,
        warehouse=transfer.origin_warehouse,
        required_level=ACCESS_MANAGE,
    )

    action = _event_action("dispatch_started", "dispatching")
    if _event_exists(transfer, action, request_id):
        return transfer
    if transfer.status == WarehouseTransfer.STATUS_DISPATCHING:
        return transfer
    if transfer.status != WarehouseTransfer.STATUS_RESERVED:
        raise WarehouseTransferStateError(
            "Solamente un traspaso reservado puede iniciar despacho."
        )

    _locked_transfer_items(transfer, company)
    previous_status = transfer.status
    transfer.status = WarehouseTransfer.STATUS_DISPATCHING
    _touch_transfer(transfer)
    transfer.full_clean()
    transfer.save(update_fields=["status", "version", "updated_at"])

    _create_event(
        transfer=transfer,
        action=action,
        user=user,
        description=f"Comenzó la preparación del traspaso {transfer.folio}.",
        previous_status=previous_status,
        new_status=transfer.status,
        ip_address=ip_address,
        request_id=request_id,
    )
    return transfer


# ============================================================
# CONFIRMAR DESPACHO
# ============================================================


@transaction.atomic
def dispatch_transfer(
    *,
    transfer_id,
    user,
    ip_address=None,
    request_id=None,
):
    """
    Confirma la salida completa.

    Descuenta physical_stock y reserved_stock del origen y deja el traspaso
    en IN_TRANSIT. La operación completa hace rollback si falla un solo SKU.
    """
    company = _user_company(user)
    request_id = _normalize_request_id(request_id)
    transfer = _lock_transfer(transfer_id, company)

    _require_transfer_company(transfer, company)
    _require_warehouse_access(
        user=user,
        warehouse=transfer.origin_warehouse,
        required_level=ACCESS_MANAGE,
    )

    action = _event_action("dispatched", "in_transit")
    if _event_exists(transfer, action, request_id):
        return transfer
    if transfer.status == WarehouseTransfer.STATUS_IN_TRANSIT:
        return transfer
    if transfer.status != WarehouseTransfer.STATUS_DISPATCHING:
        raise WarehouseTransferStateError(
            "Solamente un traspaso en preparación puede confirmarse como enviado."
        )

    items = _locked_transfer_items(transfer, company)
    variant_ids = [item.variant_id for item in items]
    stocks = {
        stock.variant_id: stock
        for stock in Stock.objects.select_for_update()
        .select_related("warehouse", "variant")
        .filter(
            company=company,
            warehouse=transfer.origin_warehouse,
            variant_id__in=variant_ids,
        )
        .order_by("variant_id", "pk")
    }

    for item in items:
        quantity = _item_quantity(item, "reserved_quantity")
        stock = stocks.get(item.variant_id)

        if quantity <= 0:
            raise WarehouseTransferStockError(
                f"El SKU {item.variant.sku} no tiene reserva para despachar."
            )
        if stock is None:
            raise WarehouseTransferStockError(
                f"No existe inventario del SKU {item.variant.sku} en origen."
            )
        if stock.reserved_stock < quantity or stock.physical_stock < quantity:
            raise WarehouseTransferStockError(
                f"El inventario del SKU {item.variant.sku} es inconsistente "
                "con la reserva del traspaso."
            )

    movement_ids = []
    movement_type = _movement_type("transfer_out", "transfer_dispatch", "out")

    for item in items:
        quantity = _item_quantity(item, "reserved_quantity")
        stock = stocks[item.variant_id]

        if is_lot_controlled(item.variant):
            dispatch_transfer_item_lots(
                company=company,
                transfer=transfer,
                item=item,
                user=user,
            )

        stock_before = stock.physical_stock

        stock.physical_stock -= quantity
        stock.reserved_stock -= quantity
        stock.full_clean()
        stock.save(update_fields=["physical_stock", "reserved_stock", "updated_at"])

        _save_item_quantities(
            item,
            {
                "dispatched_quantity": quantity,
            },
        )

        movement = _create_stock_movement(
            transfer=transfer,
            stock=stock,
            movement_type=movement_type,
            quantity=quantity,
            user=user,
            stock_before=stock_before,
            stock_after=stock.physical_stock,
            notes=(
                f"Salida por traspaso {transfer.folio}: "
                f"{transfer.origin_warehouse.name} → "
                f"{transfer.destination_warehouse.name}."
            ),
        )
        movement_ids.append(movement.pk)
        check_stock_critical(
            stock=stock,
            source="warehouse_transfer_dispatch",
            reference=f"Traspaso {transfer.folio}",
        )

    previous_status = transfer.status
    transfer.status = WarehouseTransfer.STATUS_IN_TRANSIT
    transfer.dispatched_by = user
    transfer.dispatched_at = timezone.now()
    _touch_transfer(transfer)
    transfer.full_clean()
    transfer.save(
        update_fields=[
            "status",
            "dispatched_by",
            "dispatched_at",
            "version",
            "updated_at",
        ]
    )

    _create_event(
        transfer=transfer,
        action=action,
        user=user,
        description=f"Traspaso {transfer.folio} despachado y en tránsito.",
        previous_status=previous_status,
        new_status=transfer.status,
        ip_address=ip_address,
        request_id=request_id,
        metadata={"movement_ids": movement_ids},
    )
    return transfer


# ============================================================
# INICIAR RECEPCIÓN
# ============================================================


@transaction.atomic
def start_receiving_transfer(
    *,
    transfer_id,
    user,
    ip_address=None,
    request_id=None,
):
    """
    Cambia IN_TRANSIT a RECEIVING sin modificar inventario.
    """
    company = _user_company(user)
    request_id = _normalize_request_id(request_id)
    transfer = _lock_transfer(transfer_id, company)

    _require_transfer_company(transfer, company)
    _require_warehouse_access(
        user=user,
        warehouse=transfer.destination_warehouse,
        required_level=ACCESS_MANAGE,
    )

    action = _event_action("receiving_started", "receiving")
    if _event_exists(transfer, action, request_id):
        return transfer
    if transfer.status == WarehouseTransfer.STATUS_RECEIVING:
        return transfer
    if transfer.status != WarehouseTransfer.STATUS_IN_TRANSIT:
        raise WarehouseTransferStateError(
            "Solamente un traspaso en tránsito puede iniciar recepción."
        )

    _locked_transfer_items(transfer, company)
    previous_status = transfer.status
    transfer.status = WarehouseTransfer.STATUS_RECEIVING
    transfer.receiving_started_at = timezone.now()
    _touch_transfer(transfer)
    transfer.full_clean()
    transfer.save(
        update_fields=[
            "status",
            "receiving_started_at",
            "version",
            "updated_at",
        ]
    )

    _create_event(
        transfer=transfer,
        action=action,
        user=user,
        description=f"Comenzó la recepción del traspaso {transfer.folio}.",
        previous_status=previous_status,
        new_status=transfer.status,
        ip_address=ip_address,
        request_id=request_id,
    )
    return transfer


# ============================================================
# REGISTRAR RECEPCIÓN
# ============================================================


@transaction.atomic
def receive_transfer(
    *,
    transfer_id,
    user,
    items,
    ip_address=None,
    request_id=None,
):
    """
    LT-1E: recepción mixta NORMAL + lotes.

    NORMAL conserva el flujo histórico por SKU.
    LOT / LOT_EXPIRY exige contabilización por TransferLotAllocation para
    conservar qué lote llegó conforme, dañado, rechazado o faltante.
    """
    company = _user_company(user)
    request_id = _normalize_request_id(request_id)
    transfer = _lock_transfer(transfer_id, company)

    _require_transfer_company(transfer, company)
    _require_warehouse_access(
        user=user,
        warehouse=transfer.destination_warehouse,
        required_level=ACCESS_MANAGE,
    )

    action = _event_action("received", "receipt_recorded", "item_received")
    if _event_exists(transfer, action, request_id):
        return transfer
    if transfer.status != WarehouseTransfer.STATUS_RECEIVING:
        raise WarehouseTransferStateError(
            "Solamente un traspaso en recepción puede registrar cantidades."
        )

    transfer_items = _locked_transfer_items(transfer, company)
    items_by_variant = {item.variant_id: item for item in transfer_items}
    items_by_id = {item.id: item for item in transfer_items}

    if not items:
        raise WarehouseTransferError(
            {"items": "Debes indicar al menos una cantidad recibida."}
        )

    controlled_rows = []
    normal_rows = []

    for row in items:
        if not isinstance(row, dict):
            raise WarehouseTransferError(
                {"items": "La recepción contiene un formato inválido."}
            )

        allocation_id = row.get("transfer_lot_allocation_id")
        if allocation_id not in (None, ""):
            controlled_rows.append(row)
        else:
            normal_rows.append(row)

    # Ningún SKU controlado puede entrar por la ruta agregada.
    for row in normal_rows:
        raw_variant_id = row.get("variant_id")
        try:
            variant_id = int(raw_variant_id)
        except (TypeError, ValueError):
            raise WarehouseTransferError(
                {"items": "Se recibió una variante inválida."}
            )
        item = items_by_variant.get(variant_id)
        if item is None:
            raise WarehouseTransferError(
                {"items": "Se intentó recibir un producto ajeno al traspaso."}
            )
        if is_lot_controlled(item.variant):
            raise WarehouseTransferError(
                {
                    "items": (
                        f"El SKU {item.variant.sku} está controlado por lote "
                        "y debe recibirse por asignación de lote."
                    )
                }
            )

    movement_ids = []
    receipt_summary = []

    # Ruta NORMAL histórica.
    if normal_rows:
        rows = _normalize_receipt_rows(normal_rows, items_by_variant)

        normal_variant_ids = [
            variant_id
            for variant_id in rows.keys()
            if not is_lot_controlled(items_by_variant[variant_id].variant)
        ]

        destination_stocks = {
            stock.variant_id: stock
            for stock in Stock.objects.select_for_update()
            .select_related("warehouse", "variant")
            .filter(
                company=company,
                warehouse=transfer.destination_warehouse,
                variant_id__in=normal_variant_ids,
            )
            .order_by("variant_id", "pk")
        }

        movement_type = _movement_type(
            "transfer_in",
            "transfer_receipt",
            "in",
        )

        for variant_id, row in rows.items():
            item = items_by_variant[variant_id]
            if is_lot_controlled(item.variant):
                continue

            dispatched = _item_quantity(item, "dispatched_quantity")
            current_received = _item_quantity(item, "received_quantity")
            current_damaged = _item_quantity(item, "damaged_quantity")
            current_rejected = _item_quantity(item, "rejected_quantity")
            current_missing = _item_quantity(item, "missing_quantity")

            new_values = {
                "received_quantity": current_received + row["received"],
                "damaged_quantity": current_damaged + row["damaged"],
                "rejected_quantity": current_rejected + row["rejected"],
                "missing_quantity": current_missing + row["missing"],
            }

            if sum(new_values.values()) > dispatched:
                raise WarehouseTransferError(
                    f"Las cantidades acumuladas del SKU {item.variant.sku} "
                    f"superan lo despachado ({dispatched})."
                )

            stock = destination_stocks.get(variant_id)
            if stock is None:
                try:
                    stock = Stock.objects.create(
                        company=company,
                        warehouse=transfer.destination_warehouse,
                        variant=item.variant,
                        physical_stock=0,
                        reserved_stock=0,
                        committed_stock=0,
                    )
                except IntegrityError:
                    stock = Stock.objects.select_for_update().get(
                        company=company,
                        warehouse=transfer.destination_warehouse,
                        variant=item.variant,
                    )
                destination_stocks[variant_id] = stock

            if row["received"] > 0:
                stock_before = stock.physical_stock
                stock.physical_stock += row["received"]
                stock.full_clean()
                stock.save(
                    update_fields=["physical_stock", "updated_at"]
                )

                movement = _create_stock_movement(
                    transfer=transfer,
                    stock=stock,
                    movement_type=movement_type,
                    quantity=row["received"],
                    user=user,
                    stock_before=stock_before,
                    stock_after=stock.physical_stock,
                    notes=(
                        f"Entrada por recepción del traspaso "
                        f"{transfer.folio} desde "
                        f"{transfer.origin_warehouse.name}."
                    ),
                )
                movement_ids.append(movement.pk)

                check_stock_critical(
                    stock=stock,
                    source="warehouse_transfer_receipt",
                    reference=f"Traspaso {transfer.folio}",
                )

            _save_item_quantities(
                item,
                new_values,
                extra_notes="\n".join(row["notes"]),
            )

            receipt_summary.append(
                {
                    "variant_id": variant_id,
                    "received": row["received"],
                    "damaged": row["damaged"],
                    "rejected": row["rejected"],
                    "missing": row["missing"],
                    "lot_controlled": False,
                }
            )

    # Ruta controlada por lote.
    for row in controlled_rows:
        try:
            allocation_id = int(row.get("transfer_lot_allocation_id"))
        except (TypeError, ValueError):
            raise WarehouseTransferError(
                {"items": "La asignación de lote no es válida."}
            )

        # Lectura de contexto solamente. El helper de recepción toma los
        # locks en orden canónico Stock -> ProductLot -> allocation -> ubicación.
        allocation = (
            TransferLotAllocation.objects.select_related(
                "transfer_item",
                "transfer_item__variant",
                "lot",
            )
            .filter(
                pk=allocation_id,
                company=company,
                transfer_item__transfer=transfer,
            )
            .first()
        )
        if allocation is None:
            raise PermissionDenied(
                "La asignación de lote no pertenece a este traspaso."
            )

        item = items_by_id.get(allocation.transfer_item_id)
        if item is None:
            raise PermissionDenied(
                "La asignación de lote apunta a otro detalle."
            )
        if not is_lot_controlled(item.variant):
            raise WarehouseTransferError(
                "Una asignación de lote apunta a un SKU NORMAL."
            )

        values = {}
        for input_name, short_name in (
            ("received_quantity", "received"),
            ("damaged_quantity", "damaged"),
            ("rejected_quantity", "rejected"),
            ("missing_quantity", "missing"),
        ):
            try:
                value = int(row.get(input_name, 0) or 0)
            except (TypeError, ValueError):
                raise WarehouseTransferError(
                    {"items": f"La cantidad {input_name} no es válida."}
                )
            if value < 0:
                raise WarehouseTransferError(
                    {"items": "Las cantidades no pueden ser negativas."}
                )
            values[short_name] = value

        if sum(values.values()) <= 0:
            raise WarehouseTransferError(
                {"items": "Debes contabilizar alguna cantidad del lote."}
            )

        destination_location = None
        destination_location_id = row.get("destination_location_id")
        if values["received"] > 0:
            try:
                destination_location_id = int(destination_location_id)
            except (TypeError, ValueError):
                raise WarehouseTransferError(
                    {
                        "items": (
                            "Selecciona una ubicación destino para "
                            f"el lote {allocation.lot.lot_number}."
                        )
                    }
                )

            # Solo resuelve contexto. El lock de ubicación se toma dentro
            # de receive_transfer_lot_allocation después de Stock y ProductLot.
            destination_location = (
                WarehouseLocation.objects.filter(
                    pk=destination_location_id,
                    company=company,
                    warehouse=transfer.destination_warehouse,
                )
                .first()
            )
            if destination_location is None:
                raise PermissionDenied(
                    "La ubicación destino no pertenece a la bodega."
                )

        updated_allocation = receive_transfer_lot_allocation(
            company=company,
            transfer=transfer,
            allocation=allocation,
            received=values["received"],
            damaged=values["damaged"],
            rejected=values["rejected"],
            missing=values["missing"],
            destination_location=destination_location,
            notes=_normalize_text(row.get("notes"), field_name="notes"),
            user=user,
        )

        current_values = {
            "received_quantity": sum(
                row.received_quantity
                for row in item.lot_allocations.all()
            ),
            "damaged_quantity": sum(
                row.damaged_quantity
                for row in item.lot_allocations.all()
            ),
            "rejected_quantity": sum(
                row.rejected_quantity
                for row in item.lot_allocations.all()
            ),
            "missing_quantity": sum(
                row.missing_quantity
                for row in item.lot_allocations.all()
            ),
        }
        _save_item_quantities(
            item,
            current_values,
            extra_notes=_normalize_text(
                row.get("notes"),
                field_name="notes",
            ),
        )

        receipt_summary.append(
            {
                "variant_id": item.variant_id,
                "transfer_lot_allocation_id": updated_allocation.id,
                "lot_number": updated_allocation.lot.lot_number,
                "received": values["received"],
                "damaged": values["damaged"],
                "rejected": values["rejected"],
                "missing": values["missing"],
                "lot_controlled": True,
            }
        )

    transfer.received_by = user
    _touch_transfer(transfer)
    transfer.full_clean()
    transfer.save(update_fields=["received_by", "version", "updated_at"])

    _create_event(
        transfer=transfer,
        action=action,
        user=user,
        description=f"Recepción registrada para el traspaso {transfer.folio}.",
        previous_status=transfer.status,
        new_status=transfer.status,
        ip_address=ip_address,
        request_id=request_id,
        metadata={
            "movement_ids": movement_ids,
            "items": receipt_summary,
        },
    )

    return transfer


# ============================================================
# COMPLETAR TRASPASO
# ============================================================


@transaction.atomic
def complete_transfer(
    *,
    transfer_id,
    user,
    ip_address=None,
    request_id=None,
):
    """
    Cierra la recepción cuando cada unidad despachada fue contabilizada.
    """
    company = _user_company(user)
    request_id = _normalize_request_id(request_id)
    transfer = _lock_transfer(transfer_id, company)

    _require_transfer_company(transfer, company)
    _require_warehouse_access(
        user=user,
        warehouse=transfer.destination_warehouse,
        required_level=ACCESS_MANAGE,
    )

    completed_statuses = {
        WarehouseTransfer.STATUS_COMPLETED,
        WarehouseTransfer.STATUS_COMPLETED_WITH_DIFFERENCES,
    }
    if transfer.status in completed_statuses:
        return transfer
    if transfer.status != WarehouseTransfer.STATUS_RECEIVING:
        raise WarehouseTransferStateError(
            "Solamente un traspaso en recepción puede completarse."
        )

    items = _locked_transfer_items(transfer, company)
    differences = []

    for item in items:
        dispatched = _item_quantity(item, "dispatched_quantity")
        received = _item_quantity(item, "received_quantity")
        damaged = _item_quantity(item, "damaged_quantity")
        rejected = _item_quantity(item, "rejected_quantity")
        missing = _item_quantity(item, "missing_quantity")
        accounted = received + damaged + rejected + missing

        if accounted != dispatched:
            raise WarehouseTransferError(
                f"El SKU {item.variant.sku} tiene {dispatched} unidades "
                f"despachadas y {accounted} contabilizadas. "
                "Registra la recepción o la diferencia pendiente."
            )

        if damaged or rejected or missing or received != dispatched:
            differences.append(
                {
                    "variant_id": item.variant_id,
                    "sku": item.variant.sku,
                    "dispatched": dispatched,
                    "received": received,
                    "damaged": damaged,
                    "rejected": rejected,
                    "missing": missing,
                }
            )

    previous_status = transfer.status
    transfer.status = (
        WarehouseTransfer.STATUS_COMPLETED_WITH_DIFFERENCES
        if differences
        else WarehouseTransfer.STATUS_COMPLETED
    )
    transfer.received_by = user
    transfer.completed_at = timezone.now()
    _touch_transfer(transfer)
    transfer.full_clean()
    transfer.save(
        update_fields=[
            "status",
            "received_by",
            "completed_at",
            "version",
            "updated_at",
        ]
    )

    action = _event_action(
        "completed_with_differences" if differences else "completed",
        "completed",
    )
    if not _event_exists(transfer, action, request_id):
        _create_event(
            transfer=transfer,
            action=action,
            user=user,
            description=(
                f"Traspaso {transfer.folio} completado"
                + (" con diferencias." if differences else " sin diferencias.")
            ),
            previous_status=previous_status,
            new_status=transfer.status,
            ip_address=ip_address,
            request_id=request_id,
            metadata={"differences": differences},
        )

    return transfer


# ============================================================
# CREAR TRASPASO DE REVERSA
# ============================================================


@transaction.atomic
def create_reverse_transfer(
    *,
    original_transfer_id,
    user,
    reason,
    notes="",
    ip_address=None,
    request_id=None,
):
    """
    Crea en borrador un traspaso inverso de uno ya completado.

    La reversa no modifica inventario al crearse. Su origen es la bodega que
    recibió el traspaso original y sus cantidades corresponden únicamente a
    las unidades recibidas conformes. Después debe recorrer el flujo normal:
    solicitar, reservar, despachar, recibir y completar.
    """
    company = _user_company(user)
    request_id = _normalize_request_id(request_id)
    original = _lock_transfer(
        transfer_id=original_transfer_id,
        company=company,
    )

    _require_transfer_company(original, company)
    _require_warehouse_access(
        user=user,
        warehouse=original.destination_warehouse,
        required_level=ACCESS_OPERATE,
    )
    _require_warehouse_access(
        user=user,
        warehouse=original.origin_warehouse,
        required_level=ACCESS_VIEW,
    )

    # Un reintento con el mismo request_id devuelve exactamente la reversa
    # creada por la primera petición.
    if request_id is not None:
        idempotent_event = (
            WarehouseTransferEvent.objects.select_for_update()
            .filter(
                company=company,
                transfer=original,
                action=WarehouseTransferEvent.ACTION_REVERSED,
                request_id=request_id,
            )
            .order_by("pk")
            .first()
        )
        if idempotent_event is not None:
            reversal_id = idempotent_event.metadata.get("reversal_transfer_id")
            reversal = WarehouseTransfer.objects.filter(
                pk=reversal_id,
                company=company,
                reversal_of=original,
            ).first()
            if reversal is None:
                raise WarehouseTransferError(
                    "La petición ya fue procesada, pero su reversa no está disponible."
                )
            return reversal

    allowed_statuses = {
        WarehouseTransfer.STATUS_COMPLETED,
        WarehouseTransfer.STATUS_COMPLETED_WITH_DIFFERENCES,
    }
    if original.status not in allowed_statuses:
        raise WarehouseTransferStateError(
            "Solamente un traspaso completado puede generar una reversa."
        )

    existing_reversal = (
        WarehouseTransfer.objects.select_for_update()
        .filter(
            company=company,
            reversal_of=original,
        )
        .exclude(status=WarehouseTransfer.STATUS_CANCELLED)
        .order_by("pk")
        .first()
    )
    if existing_reversal is not None:
        raise WarehouseTransferStateError(
            f"El traspaso {original.folio} ya tiene la reversa activa "
            f"{existing_reversal.folio}."
        )

    reason = _normalize_text(
        reason,
        field_name="reason",
        max_length=250,
        required=True,
    )
    notes = _normalize_text(
        notes,
        field_name="notes",
    )

    original_items = list(
        WarehouseTransferItem.objects.select_for_update()
        .select_related("variant", "variant__product")
        .filter(
            company=company,
            transfer=original,
            received_quantity__gt=0,
        )
        .order_by("variant_id", "pk")
    )
    if not original_items:
        raise WarehouseTransferError(
            "El traspaso original no tiene unidades recibidas que puedan revertirse."
        )

    for item in original_items:
        if item.variant.company_id != company.id:
            raise PermissionDenied(
                "El traspaso original contiene un producto de otra empresa."
            )
        if not item.variant.is_active:
            raise WarehouseTransferError(
                f"El SKU {item.variant.sku} está inactivo y no puede revertirse."
            )
        if getattr(item.variant, "is_bundle", False):
            raise WarehouseTransferError(
                f"El SKU {item.variant.sku} es un combo virtual y "
                "no puede revertirse directamente."
            )

    locked_company = company.__class__.objects.select_for_update().get(pk=company.pk)
    folio = _generate_locked_folio(locked_company)

    reversal = WarehouseTransfer(
        company=locked_company,
        folio=folio,
        origin_warehouse=original.destination_warehouse,
        destination_warehouse=original.origin_warehouse,
        status=WarehouseTransfer.STATUS_DRAFT,
        priority=original.priority,
        reason=reason,
        notes=notes,
        requested_by=user,
        reversal_of=original,
        created_ip=_normalize_ip(ip_address),
    )

    try:
        # El savepoint permite consultar la reversa ganadora si una petición
        # concurrente activa primero la restricción única.
        with transaction.atomic():
            reversal.full_clean()
            reversal.save()
    except IntegrityError as exc:
        # La restricción condicional también protege frente a dos solicitudes
        # concurrentes, incluso si select_for_update no bloquea filas en SQLite.
        concurrent_reversal = (
            WarehouseTransfer.objects.filter(
                company=company,
                reversal_of=original,
            )
            .exclude(status=WarehouseTransfer.STATUS_CANCELLED)
            .order_by("pk")
            .first()
        )
        if concurrent_reversal is not None:
            raise WarehouseTransferStateError(
                f"El traspaso {original.folio} ya tiene la reversa activa "
                f"{concurrent_reversal.folio}."
            ) from exc
        raise WarehouseTransferError(
            "No fue posible crear de forma única el traspaso de reversa."
        ) from exc

    reversal_items = []
    for original_item in original_items:
        reversal_item = WarehouseTransferItem(
            company=locked_company,
            transfer=reversal,
            variant=original_item.variant,
            requested_quantity=original_item.received_quantity,
            notes=(
                f"Reversa de {original.folio}. "
                f"Cantidad recibida originalmente: "
                f"{original_item.received_quantity}."
            ),
        )
        reversal_item.full_clean()
        reversal_item.save()
        reversal_items.append(reversal_item)

    _create_event(
        transfer=reversal,
        action=WarehouseTransferEvent.ACTION_CREATED,
        user=user,
        description=(
            f"Reversa {reversal.folio} creada en borrador "
            f"desde el traspaso {original.folio}."
        ),
        previous_status="",
        new_status=WarehouseTransfer.STATUS_DRAFT,
        ip_address=ip_address,
        # El request_id se reserva para el evento del original, que es el
        # ancla idempotente de la operación completa.
        request_id=None,
        metadata={
            "original_transfer_id": original.pk,
            "item_count": len(reversal_items),
            "origin_warehouse_id": reversal.origin_warehouse_id,
            "destination_warehouse_id": reversal.destination_warehouse_id,
        },
    )

    _create_event(
        transfer=original,
        action=WarehouseTransferEvent.ACTION_REVERSED,
        user=user,
        description=(
            f"Se creó la reversa {reversal.folio} "
            f"para el traspaso {original.folio}."
        ),
        previous_status=original.status,
        new_status=original.status,
        ip_address=ip_address,
        request_id=request_id,
        metadata={
            "reversal_transfer_id": reversal.pk,
            "reversal_folio": reversal.folio,
        },
    )

    return reversal
