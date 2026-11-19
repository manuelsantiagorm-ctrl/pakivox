from __future__ import annotations

from dataclasses import dataclass
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from shipping_hub.models import (
    CarrierShipment,
    ShipmentPackage,
    ShippingAuditEvent,
)
from warehouse.models import OrderFulfillment

from shipping_hub.services.lifecycle_service import (
    SOURCE_USER_ACTION,
    transition_package,
    transition_shipment,
)


READY_STATUSES = {
    CarrierShipment.STATUS_LABEL_READY,
    CarrierShipment.STATUS_PRINTED,
    CarrierShipment.STATUS_READY,
}

IN_TRANSIT_STATUSES = {
    CarrierShipment.STATUS_SHIPPED,
    CarrierShipment.STATUS_IN_TRANSIT,
    CarrierShipment.STATUS_OUT_FOR_DELIVERY,
}


@dataclass(frozen=True)
class DispatchResult:
    shipment_id: int
    shipping_order_id: int
    order_id: int | None
    tracking: str
    idempotent: bool


@dataclass(frozen=True)
class ManualDeliveryResult:
    shipment_id: int
    shipping_order_id: int
    order_id: int | None
    tracking: str
    packages_delivered: int
    idempotent: bool


def _status_value(instance) -> str:
    return str(getattr(instance, "status", "") or "").strip()


def _has_field(instance, field_name: str) -> bool:
    return any(
        field.name == field_name
        for field in instance._meta.concrete_fields
    )


def _save_status_if_supported(
    instance,
    desired: str,
    *,
    delivered_at=None,
) -> bool:
    """
    Sincroniza modelos operativos relacionados bajo lock.

    Valida que el estado exista en choices y, al cerrar una entrega,
    completa la fecha correspondiente cuando el modelo la soporta.
    """
    field = instance._meta.get_field("status")
    allowed = {str(value) for value, _label in (field.choices or [])}
    if desired not in allowed:
        raise ValidationError(
            f"{instance._meta.label} no admite el estado {desired!r}."
        )

    changed = False
    update_fields: list[str] = []

    if _status_value(instance) != desired:
        instance.status = desired
        update_fields.append("status")
        changed = True

    if desired == "delivered" and delivered_at is not None:
        for field_name in (
            "delivered_at",
            "delivery_date",
            "completed_at",
        ):
            if not _has_field(instance, field_name):
                continue
            if getattr(instance, field_name, None) is None:
                setattr(instance, field_name, delivered_at)
                update_fields.append(field_name)
                changed = True
            break

    if not changed:
        return False

    if _has_field(instance, "updated_at"):
        update_fields.append("updated_at")

    instance.save(update_fields=list(dict.fromkeys(update_fields)))
    return True


def _lock_fulfillments(*, company_id: int, order_id: int):
    return list(
        OrderFulfillment.objects.select_for_update()
        .filter(company_id=company_id, order_id=order_id)
        .order_by("pk")
    )


def _validate_shipping_relations(*, shipment, shipping_order, order, fulfillment):
    if shipping_order.company_id != shipment.company_id:
        raise ValidationError("La guía y la orden de envío pertenecen a empresas diferentes.")
    if order is None or order.company_id != shipment.company_id:
        raise ValidationError("El pedido asociado no pertenece a la empresa del envío.")
    if fulfillment is None:
        raise ValidationError("La orden de envío no tiene cumplimiento asociado.")
    if fulfillment.company_id != shipment.company_id or fulfillment.order_id != order.pk:
        raise ValidationError("El cumplimiento no pertenece a la empresa o pedido del envío.")
    packing = getattr(shipping_order, "packing_order", None)
    picking = getattr(packing, "picking_order", None) if packing else None
    warehouse_id = getattr(picking, "warehouse_id", None)
    if warehouse_id and fulfillment.warehouse_id != warehouse_id:
        raise ValidationError("La bodega del cumplimiento no coincide con la bodega operativa del envío.")


def _save_fulfillment_state(fulfillment, desired: str, *, occurred_at):
    terminal = {OrderFulfillment.STATUS_DELIVERED, OrderFulfillment.STATUS_CANCELLED}
    current = _status_value(fulfillment)
    if current in terminal and current != desired:
        raise ValidationError("Un cumplimiento en estado terminal no puede retroceder.")
    update_fields = []
    if current != desired:
        fulfillment.status = desired
        update_fields.append("status")
    timestamp_field = "delivered_at" if desired == OrderFulfillment.STATUS_DELIVERED else "shipped_at"
    if getattr(fulfillment, timestamp_field, None) is None:
        setattr(fulfillment, timestamp_field, occurred_at)
        update_fields.append(timestamp_field)
    if update_fields:
        update_fields.append("updated_at")
        fulfillment.save(update_fields=list(dict.fromkeys(update_fields)))
    return bool(update_fields)


def _sync_order_from_fulfillments(order, fulfillments, *, occurred_at):
    active = [f for f in fulfillments if f.status != OrderFulfillment.STATUS_CANCELLED]
    if not active:
        raise ValidationError("El pedido no tiene cumplimientos activos.")
    statuses = {f.status for f in active}
    if statuses == {OrderFulfillment.STATUS_DELIVERED}:
        return _save_status_if_supported(order, "delivered", delivered_at=occurred_at)
    if statuses.issubset({OrderFulfillment.STATUS_SHIPPED, OrderFulfillment.STATUS_DELIVERED}):
        return _save_status_if_supported(order, "shipped")
    return False


def _sync_shipping_order_dispatch(shipping_order, *, actor, occurred_at, shipment):
    changed = _save_status_if_supported(shipping_order, "shipped")
    fields = []
    if getattr(shipping_order, "shipped_at", None) is None:
        shipping_order.shipped_at = occurred_at
        fields.append("shipped_at")
    if getattr(shipping_order, "shipped_by_id", None) is None and actor is not None:
        shipping_order.shipped_by = actor
        fields.append("shipped_by")
    if not getattr(shipping_order, "tracking_number", None) and shipment.master_tracking_number:
        shipping_order.tracking_number = shipment.master_tracking_number
        fields.append("tracking_number")
    if not getattr(shipping_order, "carrier", None) and shipment.carrier_name:
        shipping_order.carrier = shipment.carrier_name
        fields.append("carrier")
    if fields:
        fields.append("updated_at")
        shipping_order.save(update_fields=list(dict.fromkeys(fields)))
    return changed or bool(fields)


def _validate_ready_for_dispatch(
    shipment: CarrierShipment,
    *,
    shipping_order=None,
    order=None,
) -> None:
    if shipment.status not in READY_STATUSES:
        raise ValidationError(
            "La guía todavía no está lista para marcar el pedido como enviado."
        )
    if not shipment.master_tracking_number:
        raise ValidationError("El envío no tiene número de rastreo.")
    if not shipment.has_label:
        raise ValidationError("El envío no tiene etiqueta disponible.")

    if shipping_order is None:
        shipping_order = shipment.shipping_order

    packing_order = getattr(shipping_order, "packing_order", None)
    if packing_order is None:
        raise ValidationError("El envío no tiene packing asociado.")

    packing_status = _status_value(packing_order)
    completed_values = {"completed", "packed", "closed"}
    if packing_status not in completed_values:
        raise ValidationError(
            "El packing debe estar completado antes de despachar."
        )

    if order is None:
        order = getattr(shipping_order, "order", None)
    if order is None:
        raise ValidationError("El envío no tiene pedido asociado.")

    order_status = _status_value(order)
    if order_status == "cancelled":
        raise ValidationError("El pedido está cancelado.")
    if order_status == "delivered":
        raise ValidationError("El pedido ya fue entregado.")

    balance_due = getattr(order, "balance_due", None)
    if balance_due is not None and balance_due > 0:
        raise ValidationError("El pedido todavía tiene saldo pendiente.")


def filter_dispatchable_shipments(queryset):
    from django.db.models import Q

    completed_packings = ("completed", "packed", "closed")

    return (
        queryset.filter(
            status__in=READY_STATUSES,
            shipping_order__packing_order__status__in=completed_packings,
        )
        .exclude(
            shipping_order__order__status__in=("cancelled", "delivered")
        )
        .filter(
            Q(shipping_order__order__balance_due__isnull=True)
            | Q(shipping_order__order__balance_due__lte=0)
        )
    )


@transaction.atomic
def mark_shipment_dispatched(
    *,
    company_id: int,
    shipment_id: int,
    actor,
) -> DispatchResult:
    """
    Marca un envío como despachado de forma transaccional e idempotente.

    El lock se aplica únicamente a la fila base para evitar OUTER JOIN +
    FOR UPDATE en PostgreSQL.
    """
    shipment = (
        CarrierShipment.objects.select_for_update()
        .get(pk=shipment_id, company_id=company_id)
    )

    if shipment.status in IN_TRANSIT_STATUSES:
        shipping_order = shipment.shipping_order
        order = getattr(shipping_order, "order", None)
        return DispatchResult(
            shipment_id=shipment.pk,
            shipping_order_id=shipping_order.pk,
            order_id=getattr(order, "pk", None),
            tracking=shipment.master_tracking_number,
            idempotent=True,
        )

    shipping_order_model = shipment._meta.get_field(
        "shipping_order"
    ).remote_field.model
    shipping_order = (
        shipping_order_model.objects.select_for_update()
        .get(
            pk=shipment.shipping_order_id,
            company_id=company_id,
        )
    )

    order = getattr(shipping_order, "order", None)
    if order is not None:
        order = (
            order.__class__.objects.select_for_update()
            .get(pk=order.pk, company_id=company_id)
        )

    fulfillment = None
    fulfillments = []
    if order is not None:
        fulfillments = _lock_fulfillments(
            company_id=company_id,
            order_id=order.pk,
        )
        fulfillment = next(
            (
                item
                for item in fulfillments
                if item.pk == shipping_order.fulfillment_id
            ),
            None,
        )

    _validate_shipping_relations(
        shipment=shipment,
        shipping_order=shipping_order,
        order=order,
        fulfillment=fulfillment,
    )
    _validate_ready_for_dispatch(
        shipment,
        shipping_order=shipping_order,
        order=order,
    )
    dispatched_at = timezone.now()

    transition_shipment(
        entity_id=shipment.pk,
        company_id=company_id,
        to_state=CarrierShipment.STATUS_SHIPPED,
        source=SOURCE_USER_ACTION,
        reason="El empleado confirmó la salida física del pedido.",
        actor=actor,
        metadata={
            "operation": "dispatch_from_center",
            "shipping_order_id": shipping_order.pk,
            "order_id": getattr(order, "pk", None),
            "tracking": shipment.master_tracking_number,
            "warehouse_id": getattr(
                getattr(
                    getattr(shipping_order, "packing_order", None),
                    "picking_order",
                    None,
                ),
                "warehouse_id",
                None,
            ),
            "dispatched_at": dispatched_at.isoformat(),
        },
    )

    package_ids = list(
        ShipmentPackage.objects.filter(
            company_id=company_id,
            carrier_shipment_id=shipment.pk,
        ).values_list("pk", flat=True)
    )
    for package_id in package_ids:
        package = ShipmentPackage.objects.get(
            pk=package_id,
            company_id=company_id,
        )
        if package.status == ShipmentPackage.STATUS_SHIPPED:
            continue
        transition_package(
            entity_id=package_id,
            company_id=company_id,
            to_state=ShipmentPackage.STATUS_SHIPPED,
            source=SOURCE_USER_ACTION,
            reason="El bulto salió físicamente de la bodega.",
            actor=actor,
            metadata={
                "operation": "dispatch_from_center",
                "shipment_id": shipment.pk,
                "tracking": shipment.master_tracking_number,
            },
        )

        _sync_shipping_order_dispatch(
            shipping_order,
            actor=actor,
            occurred_at=dispatched_at,
            shipment=shipment,
        )

    _save_fulfillment_state(
        fulfillment,
        OrderFulfillment.STATUS_SHIPPED,
        occurred_at=dispatched_at,
    )

    _sync_order_from_fulfillments(
        order,
        fulfillments,
        occurred_at=dispatched_at,
    )

    # =========================================================
    # WHATSAPP AUTOMÁTICO - PEDIDO ENVIADO
    # =========================================================
    # Solamente notificamos cuando el pedido GLOBAL ya quedó
    # completamente enviado. En pedidos multibodega, despachar
    # una sola bodega no debe avisar todavía al cliente.
    #
    # Se ejecuta después del COMMIT para evitar enviar WhatsApp
    # si la transacción logística posteriormente hace rollback.
    # El servicio de Messaging ya aporta idempotencia, por lo que
    # un reintento no debe duplicar una notificación exitosa.
    # =========================================================
    if order is not None and _status_value(order) == "shipped":
        shipping_order_id_for_notification = shipping_order.pk
        company_id_for_notification = company_id

        def _notify_order_shipped_after_commit():
            from messaging.tasks import notificar_pedido_enviado_whatsapp_task

            notificar_pedido_enviado_whatsapp_task.delay(
                shipping_order_id_for_notification,
                company_id_for_notification,
            )

        transaction.on_commit(
            _notify_order_shipped_after_commit,
            robust=True,
        )

    ShippingAuditEvent.objects.create(
        company_id=company_id,
        shipping_order_id=shipping_order.pk,
        carrier_shipment_id=shipment.pk,
        actor=actor,
        action="shipment_dispatched_from_center",
        detail={
            "tracking": shipment.master_tracking_number,
            "order_id": order.pk,
            "fulfillment_id": fulfillment.pk,
            "warehouse_id": fulfillment.warehouse_id,
            "dispatched_at": dispatched_at.isoformat(),
        },
    )

    return DispatchResult(
        shipment_id=shipment.pk,
        shipping_order_id=shipping_order.pk,
        order_id=getattr(order, "pk", None),
        tracking=shipment.master_tracking_number,
        idempotent=False,
    )


def _validate_manual_delivery_source(
    shipment: CarrierShipment,
    packages: list[ShipmentPackage],
) -> None:
    if shipment.status != CarrierShipment.STATUS_DELIVERED:
        if shipment.status not in IN_TRANSIT_STATUSES:
            raise ValidationError(
                "Solo un envío marcado previamente como enviado "
                "puede cerrarse manualmente como entregado."
            )
        if not shipment.master_tracking_number:
            raise ValidationError(
                "El envío no tiene número de rastreo."
            )

    if not packages:
        raise ValidationError(
            "El envío no tiene bultos registrados y no puede cerrarse."
        )

    blocked = [
        package.pk
        for package in packages
        if package.status in {
            ShipmentPackage.STATUS_CANCELLED,
            ShipmentPackage.STATUS_FAILED,
        }
    ]
    if blocked:
        raise ValidationError(
            "No se puede cerrar la entrega porque existen bultos "
            f"cancelados o fallidos: {blocked}."
        )


@transaction.atomic
def mark_shipment_delivered_manually(
    *,
    company_id: int,
    shipment_id: int,
    actor,
    observation: str = "",
) -> ManualDeliveryResult:
    """
    Cierra manualmente la entrega bajo una sola transacción.

    La combinación select_for_update + estado terminal vuelve segura la
    operación ante doble clic o solicitudes concurrentes: solamente la
    primera solicitud genera el cierre y la auditoría operativa.
    """
    if actor is None or not getattr(actor, "pk", None):
        raise ValidationError(
            "Se requiere un usuario autenticado para cerrar la entrega."
        )

    observation = str(observation or "").strip()
    if len(observation) > 500:
        raise ValidationError(
            "La observación no puede superar 500 caracteres."
        )

    operation_id = uuid.uuid4()
    delivered_at = timezone.now()

    shipment = (
        CarrierShipment.objects.select_for_update()
        .get(pk=shipment_id, company_id=company_id)
    )

    shipping_order_model = shipment._meta.get_field(
        "shipping_order"
    ).remote_field.model
    shipping_order = (
        shipping_order_model.objects.select_for_update()
        .get(
            pk=shipment.shipping_order_id,
            company_id=company_id,
        )
    )

    order = getattr(shipping_order, "order", None)
    if order is not None:
        order = (
            order.__class__.objects.select_for_update()
            .get(pk=order.pk, company_id=company_id)
        )
    fulfillment = None
    fulfillments = []
    if order is not None:
        fulfillments = _lock_fulfillments(company_id=company_id, order_id=order.pk)
        fulfillment = next((f for f in fulfillments if f.pk == shipping_order.fulfillment_id), None)
    _validate_shipping_relations(
        shipment=shipment, shipping_order=shipping_order, order=order, fulfillment=fulfillment
    )

    packages = list(
        ShipmentPackage.objects.select_for_update()
        .filter(
            company_id=company_id,
            carrier_shipment_id=shipment.pk,
        )
        .order_by("package_number", "pk")
    )

    already_delivered = (
        shipment.status == CarrierShipment.STATUS_DELIVERED
        and _status_value(shipping_order) == "delivered"
        and fulfillment.status == OrderFulfillment.STATUS_DELIVERED
        and all(
            package.status == ShipmentPackage.STATUS_DELIVERED
            for package in packages
        )
    )

    if already_delivered:
        return ManualDeliveryResult(
            shipment_id=shipment.pk,
            shipping_order_id=shipping_order.pk,
            order_id=getattr(order, "pk", None),
            tracking=shipment.master_tracking_number,
            packages_delivered=0,
            idempotent=True,
        )

    _validate_manual_delivery_source(shipment, packages)

    transition_shipment(
        entity_id=shipment.pk,
        company_id=company_id,
        to_state=CarrierShipment.STATUS_DELIVERED,
        source=SOURCE_USER_ACTION,
        reason="El empleado confirmó manualmente la entrega.",
        actor=actor,
        correlation_id=operation_id,
        metadata={
            "operation": "manual_delivery_closure",
            "shipping_order_id": shipping_order.pk,
            "order_id": getattr(order, "pk", None),
            "tracking": shipment.master_tracking_number,
            "observation": observation,
            "delivered_at": delivered_at.isoformat(),
        },
    )

    delivered_count = 0
    for package in packages:
        if package.status == ShipmentPackage.STATUS_DELIVERED:
            continue
        transition_package(
            entity_id=package.pk,
            company_id=company_id,
            to_state=ShipmentPackage.STATUS_DELIVERED,
            source=SOURCE_USER_ACTION,
            reason="El empleado confirmó manualmente la entrega.",
            actor=actor,
            correlation_id=operation_id,
            metadata={
                "operation": "manual_delivery_closure",
                "shipment_id": shipment.pk,
                "tracking": shipment.master_tracking_number,
                "observation": observation,
                "delivered_at": delivered_at.isoformat(),
            },
        )
        delivered_count += 1

    shipping_changed = _save_status_if_supported(
        shipping_order,
        "delivered",
        delivered_at=delivered_at,
    )
    fulfillment_changed = _save_fulfillment_state(
        fulfillment, OrderFulfillment.STATUS_DELIVERED, occurred_at=delivered_at
    )
    order_changed = _sync_order_from_fulfillments(
        order, fulfillments, occurred_at=delivered_at
    )

    warehouse_id = getattr(
        getattr(
            getattr(shipping_order, "packing_order", None),
            "picking_order",
            None,
        ),
        "warehouse_id",
        None,
    )

    ShippingAuditEvent.objects.create(
        company_id=company_id,
        shipping_order_id=shipping_order.pk,
        carrier_shipment_id=shipment.pk,
        actor=actor,
        action="delivery_closed_manually",
        detail={
            "message": (
                "Entrega cerrada manualmente desde "
                "el Centro de Despacho."
            ),
            "operation_id": str(operation_id),
            "tracking": shipment.master_tracking_number,
            "observation": observation,
            "packages_delivered": delivered_count,
            "delivered_at": delivered_at.isoformat(),
            "shipping_order_id": shipping_order.pk,
            "shipping_order_changed": shipping_changed,
            "order_id": getattr(order, "pk", None),
            "order_changed": order_changed,
            "fulfillment_id": fulfillment.pk,
            "fulfillment_changed": fulfillment_changed,
            "warehouse_id": warehouse_id,
            "actor_id": actor.pk,
        },
    )

    return ManualDeliveryResult(
        shipment_id=shipment.pk,
        shipping_order_id=shipping_order.pk,
        order_id=getattr(order, "pk", None),
        tracking=shipment.master_tracking_number,
        packages_delivered=delivered_count,
        idempotent=False,
    )
