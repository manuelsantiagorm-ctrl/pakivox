from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from shipping_hub.models import (
    CarrierShipment,
    ShipmentPackage,
    ShippingAuditEvent,
)
from shipping_hub.services.lifecycle_service import (
    SOURCE_TRACKING_ENGINE,
    transition_package,
)


@dataclass(frozen=True)
class DeliveryClosureResult:
    shipment_id: int
    shipping_order_id: int
    order_id: int | None
    packages_delivered: int
    idempotent: bool


def _has_field(instance, field_name: str) -> bool:
    return any(
        field.name == field_name
        for field in instance._meta.concrete_fields
    )


def _allowed_statuses(instance) -> set[str]:
    field = instance._meta.get_field("status")
    return {
        str(value)
        for value, _label in (field.choices or ())
    }


def _save_delivered_status(
    instance,
    *,
    delivered_at: datetime,
) -> bool:
    current = str(getattr(instance, "status", "") or "")
    if current == "delivered":
        return False

    if "delivered" not in _allowed_statuses(instance):
        raise ValidationError(
            f"{instance._meta.label} no admite el estado 'delivered'."
        )

    if current == "cancelled":
        raise ValidationError(
            f"{instance._meta.label} está cancelado y no puede entregarse."
        )

    instance.status = "delivered"
    update_fields = ["status"]

    for field_name in (
        "delivered_at",
        "delivery_date",
        "completed_at",
    ):
        if _has_field(instance, field_name):
            if getattr(instance, field_name, None) is None:
                setattr(instance, field_name, delivered_at)
                update_fields.append(field_name)
            break

    if _has_field(instance, "updated_at"):
        update_fields.append("updated_at")

    instance.save(update_fields=update_fields)
    return True


@transaction.atomic
def close_operational_delivery(
    *,
    company_id: int,
    shipment_id: int,
    occurred_at=None,
    provider_event_id: str = "",
    tracking_event_id: int | None = None,
) -> DeliveryClosureResult:
    delivered_at = occurred_at or timezone.now()
    if timezone.is_naive(delivered_at):
        delivered_at = timezone.make_aware(delivered_at)

    shipment = (
        CarrierShipment.objects.select_for_update()
        .get(
            pk=shipment_id,
            company_id=company_id,
        )
    )

    if shipment.status != CarrierShipment.STATUS_DELIVERED:
        raise ValidationError(
            "El envío todavía no tiene una entrega confirmada "
            "por el Tracking Engine."
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
            .get(
                pk=order.pk,
                company_id=company_id,
            )
        )

    already_closed = (
        str(getattr(shipping_order, "status", "") or "") == "delivered"
        and (
            order is None
            or str(getattr(order, "status", "") or "") == "delivered"
        )
    )

    packages = list(
        ShipmentPackage.objects.select_for_update()
        .filter(
            company_id=company_id,
            carrier_shipment_id=shipment.pk,
        )
        .order_by("package_number", "pk")
    )

    delivered_packages = 0
    for package in packages:
        if package.status == ShipmentPackage.STATUS_DELIVERED:
            continue

        if package.status in {
            ShipmentPackage.STATUS_CANCELLED,
            ShipmentPackage.STATUS_FAILED,
        }:
            raise ValidationError(
                f"El bulto {package.pk} está cancelado o fallido."
            )

        transition_package(
            entity_id=package.pk,
            company_id=company_id,
            to_state=ShipmentPackage.STATUS_DELIVERED,
            source=SOURCE_TRACKING_ENGINE,
            reason=(
                "La paquetería confirmó la entrega del envío maestro."
            ),
            metadata={
                "operation": "automatic_delivery_closure",
                "shipment_id": shipment.pk,
                "tracking": shipment.master_tracking_number,
                "provider_event_id": provider_event_id,
                "tracking_event_id": tracking_event_id,
                "occurred_at": delivered_at.isoformat(),
            },
        )
        delivered_packages += 1

    shipping_changed = _save_delivered_status(
        shipping_order,
        delivered_at=delivered_at,
    )
    order_changed = False
    if order is not None:
        order_changed = _save_delivered_status(
            order,
            delivered_at=delivered_at,
        )

    if not already_closed or delivered_packages:
        ShippingAuditEvent.objects.create(
            company_id=company_id,
            shipping_order=shipping_order,
            carrier_shipment=shipment,
            action="delivery_closed_automatically",
            detail={
                "message": (
                    "Pedido cerrado automáticamente por confirmación "
                    "de entrega del proveedor."
                ),
                "provider": shipment.provider,
                "tracking": shipment.master_tracking_number,
                "provider_event_id": provider_event_id,
                "tracking_event_id": tracking_event_id,
                "occurred_at": delivered_at.isoformat(),
                "packages_delivered": delivered_packages,
                "shipping_order_changed": shipping_changed,
                "order_changed": order_changed,
            },
        )

    return DeliveryClosureResult(
        shipment_id=shipment.pk,
        shipping_order_id=shipping_order.pk,
        order_id=getattr(order, "pk", None),
        packages_delivered=delivered_packages,
        idempotent=(
            already_closed
            and delivered_packages == 0
            and not shipping_changed
            and not order_changed
        ),
    )
