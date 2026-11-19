from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from ..models import (
    CarrierShipment, DeliveryRetry, LogisticsIncident, LogisticsIncidentEvent,
    ShipmentReturn, ShippingAuditEvent, ShippingReconciliation,
)
from shipping_hub.services.lifecycle_service import (
    SOURCE_USER_ACTION,
    transition_shipment,
)


def shipment_warehouse(shipment):
    return shipment.shipping_order.packing_order.picking_order.warehouse


def _validate_scope(shipment, warehouse):
    if shipment.company_id != warehouse.company_id or shipment_warehouse(shipment).pk != warehouse.pk:
        raise ValidationError("El envío no pertenece a esta empresa y bodega.")


@transaction.atomic
def create_incident(*, shipment, warehouse, user, cleaned_data):
    _validate_scope(shipment, warehouse)
    incident = LogisticsIncident(company=shipment.company, carrier_shipment=shipment, warehouse=warehouse, opened_by=user, **cleaned_data)
    incident.full_clean(); incident.save()
    LogisticsIncidentEvent.objects.create(company=shipment.company, incident=incident, event_type="created", to_status=incident.status, notes=incident.description, actor=user)
    if shipment.status != CarrierShipment.STATUS_DELIVERED:
        transition_shipment(
            entity_id=shipment.pk,
            company_id=shipment.company_id,
            to_state=CarrierShipment.STATUS_EXCEPTION,
            source=SOURCE_USER_ACTION,
            reason="Incidencia logística registrada.",
            actor=user,
            metadata={
                "operation": "create_incident",
                "incident_id": incident.pk,
                "incident_type": incident.incident_type,
            },
        )
    ShippingAuditEvent.objects.create(company=shipment.company, shipping_order=shipment.shipping_order, carrier_shipment=shipment, actor=user, action="logistics_incident_created", detail={"incident_id": incident.pk, "type": incident.incident_type, "severity": incident.severity})
    return incident


@transaction.atomic
def transition_incident(*, incident, user, status, notes):
    locked = LogisticsIncident.objects.select_for_update().get(pk=incident.pk, company=incident.company)
    allowed = {
        "open": {"investigating", "escalated", "resolved", "cancelled"},
        "investigating": {"escalated", "resolved", "cancelled"},
        "escalated": {"investigating", "resolved", "cancelled"},
        "resolved": set(), "cancelled": set(),
    }
    if status not in allowed.get(locked.status, set()):
        raise ValidationError("La transición de estado no está permitida.")
    previous = locked.status; locked.status = status
    if status == LogisticsIncident.STATUS_RESOLVED:
        locked.resolution = notes.strip(); locked.resolved_at = timezone.now()
    locked.full_clean(); locked.save()
    LogisticsIncidentEvent.objects.create(company=locked.company, incident=locked, event_type="status_changed", from_status=previous, to_status=status, notes=notes, actor=user)
    return locked


@transaction.atomic
def create_return(*, incident, user, cleaned_data):
    packages = list(cleaned_data.pop("packages", []))
    shipment = incident.carrier_shipment; warehouse = incident.warehouse
    package_ids = [item.pk for item in packages]
    if any(item.company_id != shipment.company_id or item.carrier_shipment_id != shipment.pk for item in packages):
        raise ValidationError("Uno de los bultos no pertenece al envío.")
    item = ShipmentReturn(company=shipment.company, carrier_shipment=shipment, warehouse=warehouse, incident=incident, requested_by=user, package_ids=package_ids, **cleaned_data)
    item.full_clean(); item.save()
    LogisticsIncidentEvent.objects.create(company=shipment.company, incident=incident, event_type="return_requested", notes=f"Devolución #{item.pk}", actor=user)
    return item


@transaction.atomic
def create_retry(*, incident, user, cleaned_data):
    shipment = incident.carrier_shipment
    number = (DeliveryRetry.objects.select_for_update().filter(carrier_shipment=shipment).aggregate(value=Max("attempt_number"))["value"] or 0) + 1
    retry = DeliveryRetry(company=shipment.company, carrier_shipment=shipment, warehouse=incident.warehouse, incident=incident, attempt_number=number, created_by=user, **cleaned_data)
    retry.full_clean(); retry.save()
    LogisticsIncidentEvent.objects.create(company=shipment.company, incident=incident, event_type="delivery_retry_scheduled", notes=f"Intento #{number}", actor=user)
    return retry


@transaction.atomic
def reconcile_shipment(*, shipment, warehouse, user, provider_status, provider_cost, currency):
    _validate_scope(shipment, warehouse)
    expected_cost = Decimal(shipment.total_cost or 0); differences = {}
    if provider_status and provider_status != shipment.status: differences["status"] = {"wms360": shipment.status, "provider": provider_status}
    if provider_cost is not None and Decimal(provider_cost) != expected_cost: differences["cost"] = {"wms360": str(expected_cost), "provider": str(provider_cost)}
    status = ShippingReconciliation.STATUS_MISMATCH if differences else ShippingReconciliation.STATUS_MATCHED
    record = ShippingReconciliation(company=shipment.company, carrier_shipment=shipment, warehouse=warehouse, checked_by=user, expected_status=shipment.status, provider_status=provider_status, expected_cost=expected_cost, provider_cost=provider_cost, currency=currency or shipment.currency, differences=differences, status=status)
    record.full_clean(); record.save()
    ShippingAuditEvent.objects.create(company=shipment.company, shipping_order=shipment.shipping_order, carrier_shipment=shipment, actor=user, action="shipping_reconciled", detail={"reconciliation_id": record.pk, "status": status, "differences": differences})
    return record
