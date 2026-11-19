from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction

from shipping_hub.models import (
    ShipmentBatch,
    ShipmentBatchItem,
    ShipmentCollectionPoint,
    ShipmentPackage,
)

BATCHABLE_PACKAGE_STATUSES = {
    ShipmentPackage.STATUS_LABEL_READY,
    ShipmentPackage.STATUS_PRINTED,
}


@dataclass(frozen=True)
class BatchCreationResult:
    batch_id: int
    item_count: int


@transaction.atomic
def create_shipment_batch(
    *,
    company_id: int,
    collection_point_id: int,
    package_ids: list[int],
    assigned_to,
    created_by,
) -> BatchCreationResult:
    if not package_ids:
        raise ValidationError("Selecciona al menos un paquete para el lote.")

    collection_point = ShipmentCollectionPoint.objects.select_for_update().get(
        pk=collection_point_id,
        company_id=company_id,
        is_active=True,
    )

    packages = list(
        ShipmentPackage.objects.select_for_update()
        .filter(company_id=company_id, pk__in=package_ids)
        .select_related("carrier_shipment")
    )

    found_ids = {p.pk for p in packages}
    missing = set(package_ids) - found_ids
    if missing:
        raise ValidationError(
            f"Algunos paquetes no existen o no pertenecen a la empresa: {sorted(missing)}."
        )

    invalid_status = [
        p.pk for p in packages if p.status not in BATCHABLE_PACKAGE_STATUSES
    ]
    if invalid_status:
        raise ValidationError(
            f"Los siguientes paquetes no tienen guía lista para envío: {invalid_status}."
        )

    already_batched = list(
        ShipmentBatchItem.objects.filter(
            company_id=company_id,
            package_id__in=package_ids,
            batch__status=ShipmentBatch.STATUS_OPEN,
        ).values_list("package_id", flat=True)
    )
    if already_batched:
        raise ValidationError(
            f"Los siguientes paquetes ya están en otro lote abierto: {already_batched}."
        )

    batch = ShipmentBatch.objects.create(
        company_id=company_id,
        collection_point=collection_point,
        assigned_to=assigned_to,
        created_by=created_by,
    )

    items = []
    for package in packages:
        carrier_name = (
            package.carrier_shipment.carrier_name
            or package.carrier_shipment.get_provider_display()
        )
        item = ShipmentBatchItem(
            company_id=company_id,
            batch=batch,
            package=package,
            carrier_name_snapshot=carrier_name,
        )
        item.full_clean()
        items.append(item)

    ShipmentBatchItem.objects.bulk_create(items)

    return BatchCreationResult(batch_id=batch.pk, item_count=len(items))
