from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from shipping_hub.models import (
    LogisticsIncident,
    ShipmentBatch,
    ShipmentBatchItem,
)


def _warehouse_for_package(package):
    shipping_order = package.carrier_shipment.shipping_order
    packing = getattr(shipping_order, "packing_order", None)
    picking = getattr(packing, "picking_order", None) if packing else None
    return getattr(picking, "warehouse", None)


@dataclass(frozen=True)
class MissingReportResult:
    reported_item_ids: list[int]
    incident_ids: list[int]


@transaction.atomic
def report_missing_packages(
    *,
    company_id: int,
    batch_id: int,
    item_ids: list[int],
    actor,
) -> MissingReportResult:
    """
    Marca paquetes del lote como "no encontrados en el área de recolección".

    Cada paquete faltante genera su propio LogisticsIncident individual
    (tipo lost, severidad alta) para que quede trazable y asignable por
    separado — no se cancela el pedido ni se libera stock aquí; eso solo
    ocurre si, tras la investigación, se confirma la pérdida definitiva
    (fuera del alcance de este servicio).
    """
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
            "No se encontraron paquetes válidos para reportar como faltantes."
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
            incident_type=LogisticsIncident.TYPE_LOST,
            severity=LogisticsIncident.SEVERITY_HIGH,
            status=LogisticsIncident.STATUS_OPEN,
            title=f"Paquete no encontrado en recolección - pedido {folio}",
            description=(
                f"Reportado por {getattr(actor, 'get_username', lambda: actor)()} "
                f"al escanear el lote #{batch.pk} en el punto "
                f"'{batch.collection_point.name}'. El sistema esperaba este "
                "paquete en el área de recolección y no fue encontrado."
            ),
            opened_by=actor,
        )

        item.status = ShipmentBatchItem.STATUS_MISSING
        item.incident = incident
        item.save(update_fields=["status", "incident", "updated_at"])

        reported_ids.append(item.pk)
        incident_ids.append(incident.pk)
        if not batch.items.filter(status=ShipmentBatchItem.STATUS_PENDING).exists():
            batch.status = ShipmentBatch.STATUS_COMPLETED
            batch.completed_at = timezone.now()
            batch.save(update_fields=["status", "completed_at", "updated_at"])

    from notifications.services import sync_shipping_incident_notification
    for incident_id in incident_ids:
        sync_shipping_incident_notification(
            LogisticsIncident.objects.get(pk=incident_id)
        )

    return MissingReportResult(reported_item_ids=reported_ids, incident_ids=incident_ids)
