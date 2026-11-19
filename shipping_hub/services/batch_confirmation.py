# shipping_hub/services/batch_confirmation.py

from __future__ import annotations

from dataclasses import dataclass, field
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from shipping_hub.models import (
    CarrierShipment,
    ShipmentBatch,
    ShipmentBatchItem,
    ShipmentPackage,
)
from shipping_hub.services.dispatch_flow import mark_shipment_dispatched


@dataclass(frozen=True)
class BatchConfirmationResult:
    batch_id: int
    confirmed_item_ids: list[int]
    dispatched_shipment_ids: list[int] = field(default_factory=list)
    already_dispatched_shipment_ids: list[int] = field(default_factory=list)
    pending_shipment_ids: list[int] = field(default_factory=list)


def _shipment_fully_confirmed(*, company_id: int, carrier_shipment_id: int) -> bool:
    """
    Un pedido/guía está listo para despacharse solo cuando TODOS sus
    bultos (paquetes) tienen su ShipmentBatchItem en estado "entregado
    a paquetería" — sin importar en qué lote se confirmó cada uno.
    """
    package_ids = list(
        ShipmentPackage.objects.filter(
            company_id=company_id,
            carrier_shipment_id=carrier_shipment_id,
        ).values_list("pk", flat=True)
    )
    if not package_ids:
        return False

    confirmed_count = (
        ShipmentBatchItem.objects.filter(
            company_id=company_id,
            package_id__in=package_ids,
            status=ShipmentBatchItem.STATUS_DELIVERED,
        )
        .values("package_id")
        .distinct()
        .count()
    )

    return confirmed_count == len(package_ids)


@transaction.atomic
def confirm_batch_items_delivered(
    *,
    company_id: int,
    batch_id: int,
    item_ids: list[int],
    actor,
) -> BatchConfirmationResult:
    """
    Confirma la entrega física de un grupo de paquetes de un lote a UNA
    paquetería (ej. "Confirmar entregados en Estafeta").

    Idempotente: si un item ya estaba confirmado, se ignora sin duplicar
    nada. El mensaje de WhatsApp al cliente solo se dispara cuando TODAS
    las cajas del pedido correspondiente ya están confirmadas.
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
            "No se encontraron paquetes válidos para confirmar en este lote."
        )

    confirmed_ids: list[int] = []
    affected_shipment_ids: set[int] = set()
    now = timezone.now()

    for item in items:
        if item.status == ShipmentBatchItem.STATUS_DELIVERED:
            # Ya estaba confirmado antes (doble clic / reintento) — no se toca de nuevo.
            affected_shipment_ids.add(item.package.carrier_shipment_id)
            continue

        if item.status != ShipmentBatchItem.STATUS_PENDING:
            # Está rechazado o marcado como faltante: no se puede confirmar así.
            raise ValidationError(
                f"El paquete {item.package_id} no está pendiente de confirmación "
                f"(estado actual: {item.get_status_display()})."
            )

        item.status = ShipmentBatchItem.STATUS_DELIVERED
        item.delivered_at = now
        item.delivered_by = actor
        item.save(
            update_fields=["status", "delivered_at", "delivered_by", "updated_at"]
        )

        confirmed_ids.append(item.pk)
        affected_shipment_ids.add(item.package.carrier_shipment_id)

    if batch.first_scanned_at is None:
        batch.first_scanned_at = now
        batch.save(update_fields=["first_scanned_at", "updated_at"])

    dispatched: list[int] = []
    already_dispatched: list[int] = []
    pending: list[int] = []

    for shipment_id in affected_shipment_ids:
        if not _shipment_fully_confirmed(
            company_id=company_id, carrier_shipment_id=shipment_id
        ):
            pending.append(shipment_id)
            continue

        result = mark_shipment_dispatched(
            company_id=company_id,
            shipment_id=shipment_id,
            actor=actor,
        )
        if result.idempotent:
            already_dispatched.append(shipment_id)
        else:
            dispatched.append(shipment_id)

    # Si ya no quedan items pendientes en el lote, se marca como completado.
    if not batch.items.filter(status=ShipmentBatchItem.STATUS_PENDING).exists():
        batch.status = ShipmentBatch.STATUS_COMPLETED
        batch.completed_at = now
        batch.save(update_fields=["status", "completed_at", "updated_at"])

    return BatchConfirmationResult(
        batch_id=batch.pk,
        confirmed_item_ids=confirmed_ids,
        dispatched_shipment_ids=dispatched,
        already_dispatched_shipment_ids=already_dispatched,
        pending_shipment_ids=pending,
    )
