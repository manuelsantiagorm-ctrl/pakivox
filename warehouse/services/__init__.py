from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from warehouse.models import PickingOrder

from warehouse.models import (
    OrderFulfillment,
    OrderItemAllocation,
    PackingOrder,
    PickingItem,
    PickingOrder,
    ShippingOrder,
    WarehouseAuditLog,
)


from warehouse.services.stock_reservation import (
    consume_reserved_stock_for_shipping,
    convert_order_item_quantity_to_internal,
    release_reserved_stock_for_cancelled_picking,
    report_damaged_stock_during_picking,
    reserve_stock_for_picking,
)
def _model_has_field(model_or_instance, field_name):
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

def _create_audit_log(
    company,
    order=None,
    picking_order=None,
    picking_item=None,
    user=None,
    action="",
    description="",
    metadata=None,
):
    data = {}

    if model_has_field(WarehouseAuditLog, "company"):
        data["company"] = company

    if model_has_field(WarehouseAuditLog, "order"):
        data["order"] = order

    if model_has_field(WarehouseAuditLog, "picking_order"):
        data["picking_order"] = picking_order

    if model_has_field(WarehouseAuditLog, "picking_item"):
        data["picking_item"] = picking_item

    if model_has_field(WarehouseAuditLog, "user"):
        data["user"] = user

    if model_has_field(WarehouseAuditLog, "action"):
        data["action"] = action

    if model_has_field(WarehouseAuditLog, "description"):
        data["description"] = description

    if metadata is not None and model_has_field(WarehouseAuditLog, "metadata"):
        data["metadata"] = metadata

    return WarehouseAuditLog.objects.create(**data)


def _save_existing_fields(instance, field_names):
    valid_fields = [
        field_name
        for field_name in field_names
        if _model_has_field(instance, field_name)
    ]

    if valid_fields:
        instance.save(
            update_fields=list(dict.fromkeys(valid_fields))
        )
    else:
        instance.save()


def _set_if_has_field(instance, field_name, value, update_fields):
    if _model_has_field(instance, field_name):
        setattr(instance, field_name, value)
        update_fields.append(field_name)


def _cancel_status_for(model_class):
    return getattr(model_class, "STATUS_CANCELLED", "cancelled")


def _shipped_status_for(model_class):
    return getattr(model_class, "STATUS_SHIPPED", "shipped")


def _delivered_status_for(model_class):
    return getattr(model_class, "STATUS_DELIVERED", "delivered")


def _fulfillment_status(name, fallback):
    return getattr(OrderFulfillment, name, fallback)


def _authenticated_user(user):
    if not user:
        return None

    if not getattr(user, "is_authenticated", True):
        return None

    return user


def _mark_flow_object_cancelled(instance, user=None, reason=""):
    """
    Cancela de forma segura PackingOrder o ShippingOrder.
    Usa campos solo si existen en el modelo.
    """

    if not instance:
        return None

    cancelled_status = _cancel_status_for(instance.__class__)

    if getattr(instance, "status", None) == cancelled_status:
        return instance

    update_fields = []

    if _model_has_field(instance, "status"):
        instance.status = cancelled_status
        update_fields.append("status")

    _set_if_has_field(instance, "cancelled_at", timezone.now(), update_fields)
    _set_if_has_field(
        instance,
        "cancel_reason",
        reason or "Cancelado por flujo warehouse.",
        update_fields,
    )

    authenticated_user = _authenticated_user(user)

    if authenticated_user:
        _set_if_has_field(
            instance,
            "cancelled_by",
            authenticated_user,
            update_fields,
        )

    if _model_has_field(instance, "updated_at"):
        update_fields.append("updated_at")

    if update_fields:
        instance.save(update_fields=list(dict.fromkeys(update_fields)))
    else:
        instance.save()

    return instance


def _get_order_locked(order):
    """
    Bloquea el pedido en BD para evitar operaciones concurrentes.
    """

    order_model = order.__class__

    return (
        order_model.objects.select_for_update(of=("self",))
        .select_related("company")
        .get(pk=order.pk)
    )


def _validate_fulfillment_context(fulfillment, order, warehouse=None):
    if not fulfillment:
        return None

    if fulfillment.order_id != order.id:
        raise ValidationError("El cumplimiento no pertenece al pedido indicado.")

    if fulfillment.company_id != order.company_id:
        raise ValidationError("El cumplimiento pertenece a otra empresa.")

    if fulfillment.warehouse.company_id != order.company_id:
        raise ValidationError("La bodega del cumplimiento pertenece a otra empresa.")

    if warehouse and fulfillment.warehouse_id != warehouse.id:
        raise ValidationError(
            "La bodega seleccionada no coincide con la bodega " "del cumplimiento."
        )

    return fulfillment


def _set_fulfillment_status(fulfillment, status):
    if not fulfillment or not status:
        return fulfillment

    if fulfillment.status == status:
        return fulfillment

    fulfillment.status = status
    update_fields = ["status", "updated_at"]
    now = timezone.now()

    if status == _fulfillment_status("STATUS_RESERVED", "reserved"):
        if not fulfillment.reserved_at:
            fulfillment.reserved_at = now
            update_fields.append("reserved_at")

    if status == _fulfillment_status("STATUS_SHIPPED", "shipped"):
        if not fulfillment.shipped_at:
            fulfillment.shipped_at = now
            update_fields.append("shipped_at")

    if status == _fulfillment_status("STATUS_DELIVERED", "delivered"):
        if not fulfillment.delivered_at:
            fulfillment.delivered_at = now
            update_fields.append("delivered_at")

    if status == _fulfillment_status("STATUS_CANCELLED", "cancelled"):
        if not fulfillment.cancelled_at:
            fulfillment.cancelled_at = now
            update_fields.append("cancelled_at")

    fulfillment.save(update_fields=list(dict.fromkeys(update_fields)))

    return fulfillment


def _update_order_status(order, status):
    if not status or not hasattr(order, "status"):
        return order

    current_status = getattr(order, "status", None)

    if current_status == "delivered":
        return order

    if current_status == "shipped" and status not in [
        "shipped",
        "delivered",
    ]:
        return order

    if current_status == status:
        return order

    order.status = status
    update_fields = ["status"]

    if hasattr(order, "updated_at"):
        update_fields.append("updated_at")

    order.save(update_fields=update_fields)

    return order


def _sync_order_status_from_fulfillments(order, fallback="paid"):
    """
    Sincroniza el estado global del pedido según todos sus cumplimientos
    activos, respetando estrictamente la empresa del pedido.
    """

    statuses = list(
        OrderFulfillment.objects.filter(
            company_id=order.company_id,
            order_id=order.id,
        ).values_list("status", flat=True)
    )

    if not statuses:
        return _update_order_status(order, fallback)

    cancelled = _fulfillment_status(
        "STATUS_CANCELLED",
        "cancelled",
    )
    packed = _fulfillment_status(
        "STATUS_PACKED",
        "packed",
    )
    shipped = _fulfillment_status(
        "STATUS_SHIPPED",
        "shipped",
    )
    delivered = _fulfillment_status(
        "STATUS_DELIVERED",
        "delivered",
    )

    active_statuses = [status for status in statuses if status != cancelled]

    if not active_statuses:
        target_status = fallback

    elif all(status == delivered for status in active_statuses):
        target_status = "delivered"

    elif all(status in {shipped, delivered} for status in active_statuses):
        target_status = "shipped"

    elif all(status in {packed, shipped, delivered} for status in active_statuses):
        target_status = "packed"

    else:
        target_status = "picking"

    return _update_order_status(
        order,
        target_status,
    )


def _ensure_fulfillment_allocations(fulfillment):
    """
    En modo de una sola bodega crea las asignaciones faltantes usando todas
    las líneas del pedido. En modo multibodega exige asignaciones explícitas.
    """

    order = fulfillment.order
    company = fulfillment.company
    fulfillment_mode = getattr(
        order,
        "fulfillment_mode",
        "single_warehouse",
    )

    existing_allocations = {
        allocation.order_item_id: allocation
        for allocation in fulfillment.allocations.select_related(
            "order_item",
            "variant",
        ).all()
    }

    if fulfillment_mode == "multi_warehouse":
        if not existing_allocations:
            raise ValidationError(
                "El cumplimiento multibodega no tiene líneas asignadas."
            )

        return fulfillment

    order_items = list(
        order.items.select_related(
            "variant",
            "variant__product",
        ).order_by("id")
    )

    if not order_items:
        raise ValidationError("El pedido no tiene productos para generar cumplimiento.")

    for order_item in order_items:
        variant = order_item.variant

        if order_item.order.company_id != company.id:
            raise ValidationError(
                f"La línea #{order_item.id} pertenece a otra empresa."
            )

        if variant.company_id != company.id:
            raise ValidationError(f"El SKU {variant.sku} pertenece a otra empresa.")

        internal_quantity = convert_order_item_quantity_to_internal(
            variant=variant,
            quantity=order_item.quantity,
        )

        existing = existing_allocations.get(order_item.id)

        if existing:
            if existing.variant_id != variant.id:
                raise ValidationError(
                    f"La asignación de la línea #{order_item.id} "
                    "apunta a otra variante."
                )

            continue

        allocation = OrderItemAllocation(
            company=company,
            fulfillment=fulfillment,
            order_item=order_item,
            variant=variant,
            commercial_quantity=order_item.quantity,
            internal_quantity=internal_quantity,
        )
        allocation.full_clean()
        allocation.save()

    return fulfillment


def _resolve_fulfillment_for_picking(
    *,
    order,
    warehouse=None,
    fulfillment=None,
    user=None,
):
    """
    Resuelve el cumplimiento del picking.

    - Si se recibe fulfillment, lo valida y usa.
    - En single_warehouse puede crear el cumplimiento -01 automáticamente.
    - En multi_warehouse exige un cumplimiento previamente creado/asignado.
    - Mantiene compatibilidad legacy cuando no hay warehouse ni fulfillment.
    """

    if fulfillment:
        fulfillment = (
            OrderFulfillment.objects.select_for_update(of=("self",))
            .select_related("company", "order", "warehouse")
            .get(pk=fulfillment.pk)
        )

        _validate_fulfillment_context(
            fulfillment=fulfillment,
            order=order,
            warehouse=warehouse,
        )
        _ensure_fulfillment_allocations(fulfillment)
        return fulfillment

    fulfillment_mode = getattr(
        order,
        "fulfillment_mode",
        "single_warehouse",
    )

    fulfillments_query = (
        OrderFulfillment.objects.select_for_update(of=("self",))
        .select_related("company", "order", "warehouse")
        .filter(
            company_id=order.company_id,
            order_id=order.id,
        )
    )

    if warehouse:
        if warehouse.company_id != order.company_id:
            raise ValidationError("La bodega seleccionada pertenece a otra empresa.")

        existing = fulfillments_query.filter(
            warehouse_id=warehouse.id,
        ).first()

        if existing:
            _ensure_fulfillment_allocations(existing)
            return existing

        if fulfillment_mode == "multi_warehouse":
            raise ValidationError(
                "El pedido multibodega requiere un cumplimiento "
                "previamente creado para esta bodega."
            )

        max_sequence = (
            fulfillments_query.aggregate(max_sequence=Max("sequence"))["max_sequence"]
            or 0
        )

        fulfillment = OrderFulfillment(
            company=order.company,
            order=order,
            warehouse=warehouse,
            sequence=max_sequence + 1,
            status=_fulfillment_status("STATUS_PENDING", "pending"),
            created_by=_authenticated_user(user),
            notes=(
                "Cumplimiento creado automáticamente para flujo " "de una sola bodega."
            ),
        )
        fulfillment.full_clean()
        fulfillment.save()
        _ensure_fulfillment_allocations(fulfillment)
        return fulfillment

    active = list(
        fulfillments_query.exclude(
            status=_fulfillment_status("STATUS_CANCELLED", "cancelled")
        )[:2]
    )

    if len(active) == 1:
        _ensure_fulfillment_allocations(active[0])
        return active[0]

    if len(active) > 1:
        raise ValidationError(
            "El pedido tiene varios cumplimientos. Debes indicar "
            "qué cumplimiento y bodega generarán el picking."
        )

    if fulfillment_mode == "multi_warehouse":
        raise ValidationError(
            "El pedido multibodega todavía no tiene cumplimientos asignados."
        )

    return None


def _get_flow_objects_locked(
    order,
    fulfillment=None,
    picking_order=None,
):
    """
    Obtiene y bloquea exclusivamente el flujo solicitado.

    En multibodega nunca toma el flujo de otra bodega por usar solamente
    order_id. Para registros legacy conserva el fallback por pedido.
    """

    if picking_order:
        picking_order = (
            PickingOrder.objects.select_for_update(of=("self",))
            .select_related("company", "order", "warehouse", "fulfillment")
            .get(
                pk=picking_order.pk,
                order_id=order.id,
                company_id=order.company_id,
            )
        )
        fulfillment = fulfillment or picking_order.fulfillment

    elif fulfillment:
        fulfillment = (
            OrderFulfillment.objects.select_for_update(of=("self",))
            .select_related("company", "order", "warehouse")
            .get(
                pk=fulfillment.pk,
                order_id=order.id,
                company_id=order.company_id,
            )
        )

        picking_order = (
            PickingOrder.objects.select_for_update()
            .filter(
                company_id=order.company_id,
                order_id=order.id,
                fulfillment_id=fulfillment.id,
            )
            .order_by("-id")
            .first()
        )

    else:
        picking_order = (
            PickingOrder.objects.select_for_update()
            .filter(
                company_id=order.company_id,
                order_id=order.id,
            )
            .order_by("-id")
            .first()
        )
        fulfillment = (
            picking_order.fulfillment
            if picking_order and picking_order.fulfillment_id
            else None
        )

    if picking_order:
        packing_order = (
            PackingOrder.objects.select_for_update()
            .filter(
                company_id=order.company_id,
                order_id=order.id,
                picking_order_id=picking_order.id,
            )
            .order_by("-id")
            .first()
        )
    elif fulfillment:
        packing_order = (
            PackingOrder.objects.select_for_update()
            .filter(
                company_id=order.company_id,
                order_id=order.id,
                fulfillment_id=fulfillment.id,
            )
            .order_by("-id")
            .first()
        )
    else:
        packing_order = None

    if packing_order:
        shipping_order = (
            ShippingOrder.objects.select_for_update()
            .filter(
                company_id=order.company_id,
                order_id=order.id,
                packing_order_id=packing_order.id,
            )
            .order_by("-id")
            .first()
        )
    elif fulfillment:
        shipping_order = (
            ShippingOrder.objects.select_for_update()
            .filter(
                company_id=order.company_id,
                order_id=order.id,
                fulfillment_id=fulfillment.id,
            )
            .order_by("-id")
            .first()
        )
    else:
        shipping_order = None

    return fulfillment, picking_order, packing_order, shipping_order


def _validate_flow_can_be_cancelled(
    order,
    shipping_order=None,
    fulfillment=None,
):
    """
    No permite liberar mercancía del flujo solicitado cuando ese envío ya
    salió o fue entregado. El estado global del pedido solo bloquea legacy.
    """

    if shipping_order:
        shipping_status = getattr(shipping_order, "status", "")

        blocked_shipping_statuses = {
            _shipped_status_for(ShippingOrder),
            _delivered_status_for(ShippingOrder),
            "shipped",
            "delivered",
        }

        if shipping_status in blocked_shipping_statuses:
            raise ValidationError(
                "No se puede liberar mercancía porque este envío "
                "ya fue enviado o entregado."
            )

    if not fulfillment:
        order_status = getattr(order, "status", "")

        if order_status in ["shipped", "delivered"]:
            raise ValidationError(
                "No se puede cancelar/liberar este flujo porque "
                "el pedido ya fue enviado o entregado."
            )


@transaction.atomic
def cancelar_flujo_warehouse(
    order,
    user=None,
    reason="",
    return_order_to="paid",
    fulfillment=None,
    picking_order=None,
):
    """
    Cancela solamente el cumplimiento/picking indicado y libera únicamente
    su reserva. Conserva compatibilidad con el flujo legacy.
    """

    order = _get_order_locked(order)

    (
        fulfillment,
        picking_order,
        packing_order,
        shipping_order,
    ) = _get_flow_objects_locked(
        order=order,
        fulfillment=fulfillment,
        picking_order=picking_order,
    )

    _validate_flow_can_be_cancelled(
        order=order,
        shipping_order=shipping_order,
        fulfillment=fulfillment,
    )

    reason = reason or "Flujo warehouse cancelado y mercancía liberada."

    old_order_status = getattr(order, "status", None)
    old_fulfillment_status = (
        getattr(fulfillment, "status", None) if fulfillment else None
    )
    old_picking_status = (
        getattr(picking_order, "status", None) if picking_order else None
    )
    old_packing_status = (
        getattr(packing_order, "status", None) if packing_order else None
    )
    old_shipping_status = (
        getattr(shipping_order, "status", None) if shipping_order else None
    )

    if shipping_order:
        _mark_flow_object_cancelled(
            instance=shipping_order,
            user=user,
            reason=reason,
        )

    if packing_order:
        _mark_flow_object_cancelled(
            instance=packing_order,
            user=user,
            reason=reason,
        )

    if picking_order and picking_order.status != PickingOrder.STATUS_CANCELLED:
        release_reserved_stock_for_cancelled_picking(
            picking_order=picking_order,
            user=user,
            reason=reason,
        )

    if fulfillment:
        _set_fulfillment_status(
            fulfillment,
            _fulfillment_status("STATUS_CANCELLED", "cancelled"),
        )
        _sync_order_status_from_fulfillments(
            order,
            fallback=return_order_to,
        )
    else:
        _update_order_status(order, return_order_to)

    WarehouseAuditLog.objects.create(
        company=order.company,
        order=order,
        picking_order=picking_order,
        user=_authenticated_user(user),
        action="warehouse_flow_cancelled",
        description=f"Flujo warehouse cancelado para el pedido {order.folio}.",
        metadata={
            "order_id": order.id,
            "order_folio": order.folio,
            "fulfillment_id": fulfillment.id if fulfillment else None,
            "old_order_status": old_order_status,
            "new_order_status": getattr(order, "status", return_order_to),
            "old_fulfillment_status": old_fulfillment_status,
            "picking_order_id": picking_order.id if picking_order else None,
            "packing_order_id": packing_order.id if packing_order else None,
            "shipping_order_id": shipping_order.id if shipping_order else None,
            "old_picking_status": old_picking_status,
            "old_packing_status": old_packing_status,
            "old_shipping_status": old_shipping_status,
            "reason": reason,
        },
    )

    return {
        "order": order,
        "fulfillment": fulfillment,
        "picking_order": picking_order,
        "packing_order": packing_order,
        "shipping_order": shipping_order,
    }


@transaction.atomic
def generate_picking_order(
    order,
    user=None,
    warehouse=None,
    fulfillment=None,
    assigned_to=None,
):
    """
    Crea, devuelve o regenera el picking de un cumplimiento.

    Reglas:
    - Existe como máximo un picking por cumplimiento.
    - Un picking activo no se duplica.
    - Un picking cancelado se reutiliza.
    - La reserva real ocurre en reserve_stock_for_picking().
    """

    order_model = order.__class__

    order = (
        order_model.objects.select_for_update(of=("self",))
        .select_related("company")
        .get(
            pk=order.pk,
            company_id=order.company_id,
        )
    )

    if fulfillment is None:
        raise ValidationError("Debes indicar el cumplimiento para generar el picking.")

    fulfillment = (
        OrderFulfillment.objects.select_for_update(of=("self",))
        .select_related(
            "company",
            "order",
            "warehouse",
        )
        .get(
            pk=fulfillment.pk,
            company_id=order.company_id,
            order_id=order.id,
        )
    )

    if fulfillment.warehouse_id is None:
        raise ValidationError("El cumplimiento no tiene una bodega asignada.")

    if warehouse is None:
        warehouse = fulfillment.warehouse

    if warehouse.id != fulfillment.warehouse_id:
        raise ValidationError(
            "La bodega indicada no coincide con la bodega del cumplimiento."
        )

    if warehouse.company_id != order.company_id:
        raise ValidationError("La bodega del cumplimiento pertenece a otra empresa.")

    cancelled_status = getattr(
        OrderFulfillment,
        "STATUS_CANCELLED",
        "cancelled",
    )

    if fulfillment.status == cancelled_status:
        raise ValidationError(
            "No puedes generar picking para un cumplimiento cancelado."
        )

    picking_order = (
        PickingOrder.objects.select_for_update()
        .filter(
            company_id=order.company_id,
            order_id=order.id,
            fulfillment_id=fulfillment.id,
        )
        .order_by("-id")
        .first()
    )

    regenerated = False

    if picking_order:
        if picking_order.status != PickingOrder.STATUS_CANCELLED:
            return picking_order

        picking_order.items.all().delete()

        picking_order.status = PickingOrder.STATUS_PENDING
        picking_order.warehouse = warehouse
        picking_order.fulfillment = fulfillment
        picking_order.assigned_to = assigned_to or user

        if _model_has_field(PickingOrder, "started_at"):
            picking_order.started_at = None

        if _model_has_field(PickingOrder, "completed_at"):
            picking_order.completed_at = None

        if _model_has_field(PickingOrder, "cancelled_at"):
            picking_order.cancelled_at = None

        if _model_has_field(PickingOrder, "cancelled_by"):
            picking_order.cancelled_by = None

        if _model_has_field(PickingOrder, "cancel_reason"):
            picking_order.cancel_reason = ""

        _save_existing_fields(
            picking_order,
            [
                "status",
                "warehouse",
                "fulfillment",
                "assigned_to",
                "started_at",
                "completed_at",
                "cancelled_at",
                "cancelled_by",
                "cancel_reason",
                "updated_at",
            ],
        )

        regenerated = True

    else:
        picking_order = PickingOrder.objects.create(
            company=order.company,
            order=order,
            fulfillment=fulfillment,
            warehouse=warehouse,
            assigned_to=assigned_to or user,
            status=PickingOrder.STATUS_PENDING,
        )

    reserve_stock_for_picking(
        picking_order=picking_order,
        user=user,
    )

    _create_audit_log(
        company=order.company,
        order=order,
        picking_order=picking_order,
        user=user,
        action="picking_created",
        description=(
            f"Picking {'regenerado' if regenerated else 'creado'} "
            f"para el pedido {order.folio}, "
            f"cumplimiento {fulfillment.code}."
        ),
        metadata={
            "picking_order_id": picking_order.id,
            "order_id": order.id,
            "fulfillment_id": fulfillment.id,
            "warehouse_id": warehouse.id,
            "regenerated": regenerated,
        },
    )

    order.status = "picking"
    _save_existing_fields(
        order,
        [
            "status",
            "updated_at",
        ],
    )

    return picking_order


@transaction.atomic
def reserve_paid_order(order, user=None):
    """Planifica y reserva completamente un pedido pagado, de forma idempotente."""
    from warehouse.services.fulfillment_planning import plan_order_fulfillments

    order = (
        order.__class__.objects.select_for_update(of=("self",))
        .select_related("company")
        .get(pk=order.pk, company_id=order.company_id)
    )
    if order.balance_due > 0 or order.status not in ("paid", "picking"):
        raise ValidationError(
            "El pedido debe estar totalmente pagado antes de reservar inventario."
        )

    plan_order_fulfillments(order=order, user=user)
    fulfillments = list(
        OrderFulfillment.objects.select_for_update(of=("self",))
        .select_related("warehouse")
        .filter(company_id=order.company_id, order_id=order.id)
        .exclude(status=OrderFulfillment.STATUS_CANCELLED)
        .order_by("sequence", "id")
    )
    if not fulfillments:
        raise ValidationError("No se pudo crear el plan de surtimiento.")

    pickings = []
    for fulfillment in fulfillments:
        pickings.append(
            generate_picking_order(
                order=order,
                warehouse=fulfillment.warehouse,
                fulfillment=fulfillment,
                assigned_to=user,
                user=user,
            )
        )
    return pickings


@transaction.atomic
def cancel_picking_order(picking_order, user=None, reason=""):
    """
    Cancela únicamente el flujo del picking recibido.
    """

    result = cancelar_flujo_warehouse(
        order=picking_order.order,
        fulfillment=(
            picking_order.fulfillment if picking_order.fulfillment_id else None
        ),
        picking_order=picking_order,
        user=user,
        reason=reason or "Picking cancelado manualmente.",
        return_order_to="paid",
    )

    return result["picking_order"]


@transaction.atomic
def confirm_picking_item(
    picking_item,
    user=None,
    scanned_code="",
):
    """
    Confirma/recolecta un producto del picking.

    Reglas:
    - Un código inválido no inicia el picking.
    - El primer código válido inicia automáticamente
      un picking pendiente.
    - Cuando ya no quedan productos pendientes,
      el picking cambia a completado.
    """

    if picking_item.status == PickingItem.STATUS_PICKED:
        return picking_item

    if picking_item.status == getattr(
        PickingItem,
        "STATUS_DAMAGED_RESOLVED",
        "damaged_resolved",
    ):
        raise ValidationError(
            "Este renglón conserva el historial de un lote dañado y ya fue "
            "sustituido por un PickingItem de reemplazo. No puede confirmarse."
        )

    picking_order = picking_item.picking_order

    scanned_code = (
        scanned_code
        or ""
    ).strip()

    valid_codes = [
        picking_item.variant.sku,
        getattr(
            picking_item.variant,
            "barcode",
            None,
        ),
    ]

    if picking_item.location_id:
        valid_codes.extend(
            [
                picking_item.location.qr_code,
                picking_item.location.bin_code,
            ]
        )

    valid_codes = [
        code
        for code in valid_codes
        if code
    ]

    if (
        scanned_code
        and scanned_code not in valid_codes
    ):
        picking_item.status = PickingItem.STATUS_ERROR
        picking_item.has_issue = True
        picking_item.issue_note = (
            f"Código escaneado inválido: "
            f"{scanned_code}"
        )
        picking_item.scanned_code = scanned_code

        picking_item.save(
            update_fields=[
                "status",
                "has_issue",
                "issue_note",
                "scanned_code",
            ]
        )

        WarehouseAuditLog.objects.create(
            company=picking_order.company,
            order=picking_order.order,
            picking_order=picking_order,
            picking_item=picking_item,
            user=_authenticated_user(user),
            action="picking_scan_invalid",
            description=(
                f"Escaneo inválido para SKU "
                f"{picking_item.variant.sku}."
            ),
            metadata={
                "picking_item_id": picking_item.id,
                "fulfillment_id": (
                    picking_order.fulfillment_id
                ),
                "variant_id": picking_item.variant_id,
                "sku": picking_item.variant.sku,
                "scanned_code": scanned_code,
                "valid_codes": valid_codes,
            },
        )

        raise ValidationError(
            "El código escaneado no coincide "
            "con el producto o ubicación."
        )

    if (
        picking_order.status
        == PickingOrder.STATUS_PENDING
    ):
        picking_order.start()

        if picking_order.fulfillment_id:
            _set_fulfillment_status(
                picking_order.fulfillment,
                _fulfillment_status(
                    "STATUS_PICKING",
                    "picking",
                ),
            )

        WarehouseAuditLog.objects.create(
            company=picking_order.company,
            order=picking_order.order,
            picking_order=picking_order,
            user=_authenticated_user(user),
            action="picking_started",
            description=(
                f"Picking iniciado para pedido "
                f"{picking_order.order.folio}."
            ),
            metadata={
                "picking_order_id": picking_order.id,
                "fulfillment_id": (
                    picking_order.fulfillment_id
                ),
                "order_id": picking_order.order_id,
                "warehouse_id": picking_order.warehouse_id,
            },
        )

    elif (
        picking_order.status
        != PickingOrder.STATUS_IN_PROGRESS
    ):
        raise ValidationError(
            "Este picking no está disponible "
            "para confirmar productos."
        )

    picking_item.mark_picked(
        user=user,
        scanned_code=scanned_code,
    )

    if picking_order.fulfillment_id:
        _set_fulfillment_status(
            picking_order.fulfillment,
            _fulfillment_status(
                "STATUS_PICKING",
                "picking",
            ),
        )

    WarehouseAuditLog.objects.create(
        company=picking_order.company,
        order=picking_order.order,
        picking_order=picking_order,
        picking_item=picking_item,
        user=_authenticated_user(user),
        action="picking_item_picked",
        description=(
            f"Producto {picking_item.variant.sku} "
            "recolectado correctamente."
        ),
        metadata={
            "picking_item_id": picking_item.id,
            "fulfillment_id": (
                picking_order.fulfillment_id
            ),
            "variant_id": picking_item.variant_id,
            "sku": picking_item.variant.sku,
            "required_quantity": (
                picking_item.required_quantity
            ),
            "picked_quantity": (
                picking_item.picked_quantity
            ),
            "scanned_code": scanned_code,
        },
    )

    resolved_statuses = [
        PickingItem.STATUS_PICKED,
        getattr(
            PickingItem,
            "STATUS_DAMAGED_RESOLVED",
            "damaged_resolved",
        ),
    ]
    pending_items = (
        picking_order.items.exclude(
            status__in=resolved_statuses
        )
        .exists()
    )

    if not pending_items:
        picking_order.complete()

        WarehouseAuditLog.objects.create(
            company=picking_order.company,
            order=picking_order.order,
            picking_order=picking_order,
            user=_authenticated_user(user),
            action="picking_completed",
            description=(
                f"Picking completado para pedido "
                f"{picking_order.order.folio}."
            ),
            metadata={
                "picking_order_id": picking_order.id,
                "fulfillment_id": (
                    picking_order.fulfillment_id
                ),
                "order_id": picking_order.order_id,
                "warehouse_id": picking_order.warehouse_id,
            },
        )

    if picking_order.fulfillment_id:
        _sync_order_status_from_fulfillments(
            picking_order.order
        )

    return picking_item


@transaction.atomic
def create_packing_order(picking_order, user=None):
    """
    Crea un packing exclusivamente para el picking recibido.
    """

    if picking_order.status != PickingOrder.STATUS_COMPLETED:
        raise ValidationError("El picking debe estar completado para crear packing.")

    if picking_order.order.company_id != picking_order.company_id:
        raise ValidationError("El pedido del picking pertenece a otra empresa.")

    fulfillment = picking_order.fulfillment if picking_order.fulfillment_id else None

    if fulfillment:
        _validate_fulfillment_context(
            fulfillment=fulfillment,
            order=picking_order.order,
            warehouse=picking_order.warehouse,
        )

    packing_order = (
        PackingOrder.objects.select_for_update()
        .filter(
            company_id=picking_order.company_id,
            order_id=picking_order.order_id,
            picking_order_id=picking_order.id,
        )
        .order_by("-id")
        .first()
    )

    created = False

    if not packing_order:
        packing_order = PackingOrder(
            company=picking_order.company,
            order=picking_order.order,
            fulfillment=fulfillment,
            picking_order=picking_order,
            status=PackingOrder.STATUS_PENDING,
        )
        packing_order.full_clean()
        packing_order.save()
        created = True

    elif fulfillment and packing_order.fulfillment_id != fulfillment.id:
        raise ValidationError("El packing existente pertenece a otro cumplimiento.")

    if created:
        WarehouseAuditLog.objects.create(
            company=picking_order.company,
            order=picking_order.order,
            picking_order=picking_order,
            user=_authenticated_user(user),
            action="packing_created",
            description=(f"Packing creado para pedido {picking_order.order.folio}."),
            metadata={
                "picking_order_id": picking_order.id,
                "packing_order_id": packing_order.id,
                "fulfillment_id": picking_order.fulfillment_id,
            },
        )

    return packing_order


@transaction.atomic
def complete_packing_order(packing_order, user=None):
    """
    Marca packing como empacado y actualiza solamente su cumplimiento.
    """

    packing_order.mark_packed(user=user)

    if packing_order.fulfillment_id:
        _set_fulfillment_status(
            packing_order.fulfillment,
            _fulfillment_status("STATUS_PACKED", "packed"),
        )
        _sync_order_status_from_fulfillments(packing_order.order)

    WarehouseAuditLog.objects.create(
        company=packing_order.company,
        order=packing_order.order,
        picking_order=packing_order.picking_order,
        user=_authenticated_user(user),
        action="packing_completed",
        description=(f"Packing completado para pedido {packing_order.order.folio}."),
        metadata={
            "packing_order_id": packing_order.id,
            "fulfillment_id": packing_order.fulfillment_id,
            "order_id": packing_order.order_id,
        },
    )

    return packing_order


@transaction.atomic
def create_shipping_order(packing_order, user=None):
    """
    Crea un shipping exclusivamente para el packing recibido.
    """

    if packing_order.status != PackingOrder.STATUS_PACKED:
        raise ValidationError("El packing debe estar empacado para crear envío.")

    if not packing_order.picking_order_id:
        raise ValidationError("El packing no tiene picking relacionado.")

    fulfillment = packing_order.fulfillment if packing_order.fulfillment_id else None

    if fulfillment:
        _validate_fulfillment_context(
            fulfillment=fulfillment,
            order=packing_order.order,
            warehouse=packing_order.picking_order.warehouse,
        )

        if packing_order.picking_order.fulfillment_id != fulfillment.id:
            raise ValidationError(
                "El picking y el packing pertenecen a cumplimientos distintos."
            )

    shipping_order = (
        ShippingOrder.objects.select_for_update()
        .filter(
            company_id=packing_order.company_id,
            order_id=packing_order.order_id,
            packing_order_id=packing_order.id,
        )
        .order_by("-id")
        .first()
    )

    created = False

    if not shipping_order:
        shipping_order = ShippingOrder(
            company=packing_order.company,
            order=packing_order.order,
            fulfillment=fulfillment,
            packing_order=packing_order,
            status=ShippingOrder.STATUS_READY,
        )
        shipping_order.full_clean()
        shipping_order.save()
        created = True

    elif fulfillment and shipping_order.fulfillment_id != fulfillment.id:
        raise ValidationError("El envío existente pertenece a otro cumplimiento.")

    if created:
        WarehouseAuditLog.objects.create(
            company=packing_order.company,
            order=packing_order.order,
            picking_order=packing_order.picking_order,
            user=_authenticated_user(user),
            action="shipping_created",
            description=(f"Envío creado para pedido {packing_order.order.folio}."),
            metadata={
                "packing_order_id": packing_order.id,
                "shipping_order_id": shipping_order.id,
                "fulfillment_id": packing_order.fulfillment_id,
            },
        )

    return shipping_order


@transaction.atomic
def mark_order_as_shipped(shipping_order, user=None):
    """
    Marca un envío como enviado y consume únicamente la reserva de su picking.
    El pedido global se marca enviado solo cuando todos sus cumplimientos
    activos están enviados o entregados.
    """

    if shipping_order.status in [
        _shipped_status_for(ShippingOrder),
        _delivered_status_for(ShippingOrder),
        "shipped",
        "delivered",
    ]:
        return shipping_order

    if not shipping_order.packing_order_id:
        raise ValidationError("El envío no tiene packing relacionado.")

    packing_order = shipping_order.packing_order
    picking_order = packing_order.picking_order

    if not picking_order:
        raise ValidationError("El packing no tiene picking relacionado.")

    fulfillment = shipping_order.fulfillment if shipping_order.fulfillment_id else None

    if fulfillment:
        if packing_order.fulfillment_id != fulfillment.id:
            raise ValidationError(
                "El shipping y el packing pertenecen a cumplimientos distintos."
            )

        if picking_order.fulfillment_id != fulfillment.id:
            raise ValidationError(
                "El shipping y el picking pertenecen a cumplimientos distintos."
            )

    consume_reserved_stock_for_shipping(
        picking_order=picking_order,
        user=user,
    )

    shipping_order.mark_shipped(user=user)
    order = shipping_order.order

    if fulfillment:
        _set_fulfillment_status(
            fulfillment,
            _fulfillment_status("STATUS_SHIPPED", "shipped"),
        )
        _sync_order_status_from_fulfillments(order)
    else:
        _update_order_status(order, "shipped")

    WarehouseAuditLog.objects.create(
        company=shipping_order.company,
        order=order,
        picking_order=picking_order,
        user=_authenticated_user(user),
        action="order_shipped",
        description=f"Envío del pedido {order.folio} marcado como enviado.",
        metadata={
            "shipping_order_id": shipping_order.id,
            "picking_order_id": picking_order.id,
            "fulfillment_id": shipping_order.fulfillment_id,
            "order_id": shipping_order.order_id,
            "global_order_status": getattr(order, "status", None),
        },
    )

    return shipping_order


@transaction.atomic
def mark_order_as_delivered(shipping_order, user=None):
    """
    Marca un envío como entregado. El pedido global se marca entregado solo
    cuando todos sus cumplimientos activos fueron entregados.
    """

    if shipping_order.status in [
        _delivered_status_for(ShippingOrder),
        "delivered",
    ]:
        return shipping_order

    shipping_order.mark_delivered()
    order = shipping_order.order
    fulfillment = shipping_order.fulfillment if shipping_order.fulfillment_id else None

    if fulfillment:
        _set_fulfillment_status(
            fulfillment,
            _fulfillment_status("STATUS_DELIVERED", "delivered"),
        )
        _sync_order_status_from_fulfillments(order)
    else:
        _update_order_status(order, "delivered")

    picking_order = (
        shipping_order.packing_order.picking_order
        if shipping_order.packing_order_id
        and shipping_order.packing_order.picking_order_id
        else None
    )

    WarehouseAuditLog.objects.create(
        company=shipping_order.company,
        order=order,
        picking_order=picking_order,
        user=_authenticated_user(user),
        action="order_delivered",
        description=f"Envío del pedido {order.folio} marcado como entregado.",
        metadata={
            "shipping_order_id": shipping_order.id,
            "fulfillment_id": shipping_order.fulfillment_id,
            "order_id": shipping_order.order_id,
            "delivered_at": timezone.now().isoformat(),
            "global_order_status": getattr(order, "status", None),
        },
    )

    return shipping_order


@transaction.atomic
def liberar_reservas_vencidas(hours=24, user=None, dry_run=False):
    """
    Cancela pickings pendientes/viejos y libera solamente la reserva de cada
    picking vencido, sin tocar cumplimientos de otras bodegas.
    """

    threshold = timezone.now() - timedelta(hours=hours)

    pending_statuses = [
        getattr(PickingOrder, "STATUS_PENDING", "pending"),
        getattr(PickingOrder, "STATUS_IN_PROGRESS", "in_progress"),
        "pending",
        "in_progress",
    ]

    pickings = (
        PickingOrder.objects.select_related(
            "order",
            "company",
            "warehouse",
            "fulfillment",
        )
        .filter(
            status__in=list(dict.fromkeys(pending_statuses)),
            updated_at__lte=threshold,
        )
        .order_by("updated_at", "id")
    )

    results = []

    for picking_order in pickings:
        order = picking_order.order

        item = {
            "picking_order_id": picking_order.id,
            "fulfillment_id": picking_order.fulfillment_id,
            "warehouse_id": picking_order.warehouse_id,
            "order_id": order.id,
            "order_folio": order.folio,
            "status": picking_order.status,
            "updated_at": (
                picking_order.updated_at.isoformat()
                if picking_order.updated_at
                else None
            ),
        }

        if dry_run:
            item["cancelled"] = False
            item["dry_run"] = True
            results.append(item)
            continue

        cancel_picking_order(
            picking_order=picking_order,
            user=user,
            reason=(
                f"Reserva vencida: picking sin avance por más " f"de {hours} horas."
            ),
        )

        item["cancelled"] = True
        item["dry_run"] = False
        results.append(item)

    return results
