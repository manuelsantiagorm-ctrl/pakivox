from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from shipping_hub.models import (
    LogisticsIncident,
    LogisticsIncidentEvent,
    ShipmentBatch,
    ShipmentBatchItem,
)


def _warehouse_for_package(package):
    shipping_order = package.carrier_shipment.shipping_order
    packing = getattr(shipping_order, "packing_order", None)
    picking = getattr(packing, "picking_order", None) if packing else None
    return getattr(picking, "warehouse", None)


@dataclass(frozen=True)
class RejectionReportResult:
    reported_item_ids: list[int]
    incident_ids: list[int]


@transaction.atomic
def report_rejected_packages(
    *,
    company_id: int,
    batch_id: int,
    item_ids: list[int],
    reason: str,
    actor,
) -> RejectionReportResult:
    """
    Marca paquetes como rechazados por la paquetería en el mostrador.

    Genera un LogisticsIncident tipo "held" (retenido/rechazado) por
    paquete, distinto del tipo "lost" usado para faltantes en
    recolección. La resolución (reempacar, regenerar guía, cancelar)
    ocurre en un paso posterior sobre ese incidente.
    """
    if not reason or not reason.strip():
        raise ValidationError("Indica el motivo del rechazo de la paquetería.")

    batch = ShipmentBatch.objects.select_for_update().get(
        pk=batch_id, company_id=company_id
    )

    items = list(
        ShipmentBatchItem.objects.select_for_update()
        .filter(company_id=company_id, batch_id=batch.pk, pk__in=item_ids)
        .select_related("package", "package__carrier_shipment")
    )

    if not items:
        raise ValidationError(
            "No se encontraron paquetes válidos para reportar como rechazados."
        )

    reported_ids: list[int] = []
    incident_ids: list[int] = []

    for item in items:
        if item.status != ShipmentBatchItem.STATUS_PENDING:
            raise ValidationError(
                f"El paquete {item.package_id} no está pendiente "
                f"(estado actual: {item.get_status_display()})."
            )

        package = item.package
        warehouse = _warehouse_for_package(package)
        if warehouse is None:
            raise ValidationError(
                f"No se pudo determinar la bodega del paquete {package.pk}."
            )

        folio = getattr(
            getattr(package.carrier_shipment.shipping_order, "order", None),
            "folio",
            f"paquete {package.pk}",
        )

        incident = LogisticsIncident.objects.create(
            company_id=company_id,
            carrier_shipment=package.carrier_shipment,
            warehouse=warehouse,
            package=package,
            incident_type=LogisticsIncident.TYPE_HELD,
            severity=LogisticsIncident.SEVERITY_MEDIUM,
            status=LogisticsIncident.STATUS_OPEN,
            title=f"Rechazado por {item.carrier_name_snapshot or 'paquetería'} - pedido {folio}",
            description=(
                f"Reportado por {getattr(actor, 'get_username', lambda: actor)()} "
                f"en mostrador de {item.carrier_name_snapshot or 'paquetería'}. "
                f"Motivo: {reason.strip()}"
            ),
            opened_by=actor,
        )

        LogisticsIncidentEvent.objects.create(
            company_id=company_id,
            incident=incident,
            event_type="rejected_at_carrier_counter",
            notes=reason.strip(),
            actor=actor,
        )

        item.status = ShipmentBatchItem.STATUS_REJECTED
        item.incident = incident
        item.save(update_fields=["status", "incident", "updated_at"])

        reported_ids.append(item.pk)
        incident_ids.append(incident.pk)

    if not batch.items.filter(status=ShipmentBatchItem.STATUS_PENDING).exists():
        from django.utils import timezone
        batch.status = ShipmentBatch.STATUS_COMPLETED
        batch.completed_at = timezone.now()
        batch.save(update_fields=["status", "completed_at", "updated_at"])

    from notifications.services import sync_shipping_incident_notification
    for incident_id in incident_ids:
        sync_shipping_incident_notification(
            LogisticsIncident.objects.get(pk=incident_id)
        )

    return RejectionReportResult(reported_item_ids=reported_ids, incident_ids=incident_ids)