from __future__ import annotations

import hashlib

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from shipping_hub.models import (
    CarrierShipment,
    IncidentExpenseReceipt,
    LogisticsIncident,
    LogisticsIncidentEvent,
    ShipmentPackage,
)
from warehouse.services import cancelar_flujo_warehouse

ALLOWED_RECEIPT_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/pdf",
}
MAX_RECEIPT_SIZE_BYTES = 8 * 1024 * 1024  # 8 MB


def _hash_uploaded_file(uploaded_file) -> str:
    hasher = hashlib.sha256()
    for chunk in uploaded_file.chunks():
        hasher.update(chunk)
    uploaded_file.seek(0)
    return hasher.hexdigest()


@transaction.atomic
def resolve_rejection_by_repack(
    *, company_id: int, incident_id: int, notes: str, actor
):
    """
    Caso 1: la paquetería aceptó el paquete tras reempacar/reforzar en el
    momento. No cambia guía, no toca stock. El paquete vuelve a estar
    disponible para agruparse en un lote nuevo automáticamente, porque
    su lote anterior ya quedó "completed" y deja de excluirlo del filtro
    de "paquetes listos para agrupar".
    """
    incident = LogisticsIncident.objects.select_for_update().get(
        pk=incident_id, company_id=company_id
    )

    if incident.incident_type != LogisticsIncident.TYPE_HELD:
        raise ValidationError(
            "Esta incidencia no corresponde a un rechazo de paquetería."
        )

    if incident.status == LogisticsIncident.STATUS_RESOLVED:
        return incident  # idempotente

    incident.status = LogisticsIncident.STATUS_RESOLVED
    incident.resolution = (
        notes.strip()
        or "Reempacado y aceptado por la paquetería en el mismo mostrador."
    )
    incident.resolved_at = timezone.now()
    incident.save(update_fields=["status", "resolution", "resolved_at", "updated_at"])

    LogisticsIncidentEvent.objects.create(
        company_id=company_id,
        incident=incident,
        event_type="rejection_resolved_repack",
        from_status=LogisticsIncident.STATUS_OPEN,
        to_status=LogisticsIncident.STATUS_RESOLVED,
        notes=incident.resolution,
        actor=actor,
    )

    from notifications.services import sync_shipping_incident_notification

    sync_shipping_incident_notification(incident)

    return incident


@transaction.atomic
def resolve_rejection_by_new_label(
    *,
    company_id: int,
    incident_id: int,
    new_carrier_name: str,
    new_tracking_number: str,
    notes: str,
    actor,
):
    """
    Caso 2 (y base del caso 3): se regenera la guía porque cambia
    paquetería o medidas. No toca stock -- el paquete sigue físicamente
    en tránsito/con el recolector, nunca regresó a bodega.

    Actualiza directo el CarrierShipment/ShipmentPackage existentes
    (en vez de crear una guía nueva por separado) y deja el rastro
    completo -- incluyendo la guía anterior -- en LogisticsIncidentEvent.
    """
    if not new_carrier_name.strip() or not new_tracking_number.strip():
        raise ValidationError("Indica la nueva paquetería y el nuevo número de guía.")

    incident = LogisticsIncident.objects.select_for_update().get(
        pk=incident_id, company_id=company_id
    )

    if incident.incident_type != LogisticsIncident.TYPE_HELD:
        raise ValidationError(
            "Esta incidencia no corresponde a un rechazo de paquetería."
        )

    if incident.status == LogisticsIncident.STATUS_RESOLVED:
        return incident  # idempotente

    if incident.package_id is None:
        raise ValidationError("Esta incidencia no está ligada a un paquete específico.")

    package = ShipmentPackage.objects.select_for_update().get(
        pk=incident.package_id, company_id=company_id
    )
    shipment = CarrierShipment.objects.select_for_update().get(
        pk=package.carrier_shipment_id, company_id=company_id
    )

    old_carrier = shipment.carrier_name
    old_tracking = package.tracking_number or shipment.master_tracking_number

    shipment.carrier_name = new_carrier_name.strip()
    shipment.master_tracking_number = new_tracking_number.strip()
    shipment.save(
        update_fields=["carrier_name", "master_tracking_number", "updated_at"]
    )

    package.tracking_number = new_tracking_number.strip()
    package.save(update_fields=["tracking_number", "updated_at"])

    resolution_text = (
        f"Guía regenerada. Anterior: {old_carrier or '—'} / {old_tracking or '—'}. "
        f"Nueva: {new_carrier_name.strip()} / {new_tracking_number.strip()}. "
        + notes.strip()
    )

    incident.status = LogisticsIncident.STATUS_RESOLVED
    incident.resolution = resolution_text
    incident.resolved_at = timezone.now()
    incident.save(update_fields=["status", "resolution", "resolved_at", "updated_at"])

    LogisticsIncidentEvent.objects.create(
        company_id=company_id,
        incident=incident,
        event_type="rejection_resolved_new_label",
        from_status=LogisticsIncident.STATUS_OPEN,
        to_status=LogisticsIncident.STATUS_RESOLVED,
        notes=resolution_text,
        actor=actor,
    )

    from notifications.services import sync_shipping_incident_notification

    sync_shipping_incident_notification(incident)

    return incident


@transaction.atomic
def resolve_rejection_by_field_expense(
    *,
    company_id: int,
    incident_id: int,
    new_carrier_name: str,
    new_tracking_number: str,
    amount,
    currency: str,
    receipt_file,
    actor,
):
    """
    Caso 3: la paquetería genera la guía nueva ahí mismo y Héctor paga en
    el momento (transferencia/efectivo resuelto fuera del sistema).

    Requiere foto/PDF obligatorio del comprobante. El gasto queda
    "pendiente de conciliar" hasta que finanzas lo revise -- este
    servicio NO valida el pago en sí, solo deja evidencia trazable.
    """
    if receipt_file is None:
        raise ValidationError("Adjunta una foto o PDF del comprobante de pago.")

    if receipt_file.content_type not in ALLOWED_RECEIPT_CONTENT_TYPES:
        raise ValidationError(
            "El comprobante debe ser una imagen (JPG/PNG/WEBP) o un PDF."
        )

    if receipt_file.size > MAX_RECEIPT_SIZE_BYTES:
        raise ValidationError("El comprobante no puede pesar más de 8 MB.")

    if amount is None or amount <= 0:
        raise ValidationError("Indica el monto pagado, mayor a cero.")

    # Reusa la actualización de guía del Caso 2 -- misma validación,
    # mismo rastro en el incidente, sin duplicar lógica.
    incident = resolve_rejection_by_new_label(
        company_id=company_id,
        incident_id=incident_id,
        new_carrier_name=new_carrier_name,
        new_tracking_number=new_tracking_number,
        notes="Guía y pago gestionados en sitio por el recolector.",
        actor=actor,
    )

    file_hash = _hash_uploaded_file(receipt_file)

    duplicate = IncidentExpenseReceipt.objects.filter(
        company_id=company_id,
        incident=incident,
        sha256=file_hash,
    ).first()
    if duplicate is not None:
        return incident, duplicate  # idempotente: mismo archivo ya registrado

    receipt = IncidentExpenseReceipt.objects.create(
        company_id=company_id,
        incident=incident,
        amount=amount,
        currency=currency or "MXN",
        carrier_name=new_carrier_name.strip(),
        new_tracking_number=new_tracking_number.strip(),
        receipt_file=receipt_file,
        original_name=receipt_file.name,
        content_type=receipt_file.content_type,
        size_bytes=receipt_file.size,
        sha256=file_hash,
        status=IncidentExpenseReceipt.STATUS_PENDING,
        reported_by=actor,
    )

    LogisticsIncidentEvent.objects.create(
        company_id=company_id,
        incident=incident,
        event_type="field_expense_reported",
        notes=(
            f"Gasto de ${amount} {currency or 'MXN'} reportado por "
            f"{getattr(actor, 'get_username', lambda: actor)()}. "
            "Pendiente de conciliar por finanzas."
        ),
        actor=actor,
    )

    from notifications.services import (
        notify_field_expense_pending,
        sync_shipping_incident_notification,
    )

    sync_shipping_incident_notification(incident)
    notify_field_expense_pending(receipt)

    return incident, receipt


@transaction.atomic
def resolve_rejection_by_cancellation(
    *, company_id: int, incident_id: int, notes: str, actor
):
    """
    Caso 4: ya no se puede enviar de ninguna forma. El pedido regresa a
    bodega y se libera el stock -- el único caso de los 4 que sí toca
    inventario, porque el producto físicamente deja de estar en tránsito.

    Reusa cancelar_flujo_warehouse (ya probado en producción) para la
    liberación de stock, en vez de duplicar esa lógica aquí.
    """
    incident = LogisticsIncident.objects.select_for_update().get(
        pk=incident_id, company_id=company_id
    )

    if incident.incident_type != LogisticsIncident.TYPE_HELD:
        raise ValidationError(
            "Esta incidencia no corresponde a un rechazo de paquetería."
        )

    if incident.status == LogisticsIncident.STATUS_RESOLVED:
        return incident  # idempotente

    order = (
        incident.carrier_shipment.shipping_order.order
        if incident.carrier_shipment_id
        else None
    )
    if order is None:
        raise ValidationError("La incidencia no tiene un pedido asociado.")

    reason = notes.strip() or (
        "Pedido cancelado tras rechazo definitivo de la paquetería "
        f"(incidencia #{incident.pk})."
    )

    cancelar_flujo_warehouse(order=order, user=actor, reason=reason)

    incident.status = LogisticsIncident.STATUS_RESOLVED
    incident.resolution = f"Cancelado y regresado a bodega. {reason}"
    incident.resolved_at = timezone.now()
    incident.save(update_fields=["status", "resolution", "resolved_at", "updated_at"])

    LogisticsIncidentEvent.objects.create(
        company_id=company_id,
        incident=incident,
        event_type="rejection_resolved_cancellation",
        from_status=LogisticsIncident.STATUS_OPEN,
        to_status=LogisticsIncident.STATUS_RESOLVED,
        notes=incident.resolution,
        actor=actor,
    )

    from notifications.services import sync_shipping_incident_notification

    sync_shipping_incident_notification(incident)

    return incident
