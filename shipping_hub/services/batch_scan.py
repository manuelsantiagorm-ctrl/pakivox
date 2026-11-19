from __future__ import annotations

from django.core.exceptions import ValidationError

from shipping_hub.models import ShipmentBatch, ShipmentCollectionPoint


def resolve_batch_from_qr(*, company_id: int, qr_token: str, user):
    """
    Dado el token del QR fijo de una estación, regresa el lote abierto
    más reciente asociado a esa estación, validando que el usuario sea
    quien tiene asignado ese lote.
    """
    try:
        point = ShipmentCollectionPoint.objects.get(
            company_id=company_id,
            qr_token=qr_token,
            is_active=True,
        )
    except ShipmentCollectionPoint.DoesNotExist:
        raise ValidationError("Punto de recolección no encontrado o inactivo.")

    batch = (
        ShipmentBatch.objects.filter(
            company_id=company_id,
            collection_point=point,
            status=ShipmentBatch.STATUS_OPEN,
        )
        .order_by("-created_at")
        .select_related("assigned_to")
        .prefetch_related("items__package__carrier_shipment")
        .first()
    )

    if batch is None:
        raise ValidationError(f"No hay lotes pendientes en '{point.name}'.")

    if batch.assigned_to_id != user.pk:
        raise ValidationError(
            f"Este lote está asignado a {batch.assigned_to.get_full_name() or batch.assigned_to.username}, no a ti."
        )

    return point, batch
