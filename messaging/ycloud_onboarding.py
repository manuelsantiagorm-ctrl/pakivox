from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import WhatsAppNumber
from .ycloud_api import normalize_ycloud_phone, retrieve_ycloud_phone_number


def _remote_phone(payload):
    return payload.get("phoneNumber") or payload.get("displayPhoneNumber") or ""


@transaction.atomic
def verify_and_activate_ycloud_channel(*, channel_id, waba_id):
    """Operación exclusiva del Owner general; valida en YCloud antes de activar."""
    channel = (
        WhatsAppNumber.objects.select_for_update(of=("self",))
        .select_related("company", "warehouse", "company__subscription__plan")
        .get(pk=channel_id)
    )
    if channel.provider != WhatsAppNumber.PROVIDER_YCLOUD:
        raise ValidationError("El canal seleccionado no pertenece a YCloud.")
    if not channel.company.is_active or not channel.warehouse_id or not channel.warehouse.is_active:
        raise ValidationError("La empresa y la bodega deben estar activas.")
    subscription = getattr(channel.company, "subscription", None)
    if not subscription or not subscription.has_whatsapp_access:
        raise ValidationError("La empresa no tiene WhatsApp Premium activo y verificado.")

    payload = retrieve_ycloud_phone_number(
        waba_id=waba_id, phone_number=channel.display_number,
    )
    expected = normalize_ycloud_phone(channel.display_number)
    returned = normalize_ycloud_phone(_remote_phone(payload))
    if returned != expected:
        raise ValidationError("YCloud devolvió un número distinto al solicitado.")
    remote_waba = str(payload.get("wabaId") or waba_id).strip()
    if remote_waba != str(waba_id).strip():
        raise ValidationError("YCloud devolvió un WABA distinto al solicitado.")
    status = str(payload.get("status") or "").upper()
    if status != "CONNECTED":
        raise ValidationError(f"El número todavía no está CONNECTED en YCloud (estado: {status or 'UNKNOWN'}).")
    phone_id = str(payload.get("phoneNumberId") or payload.get("id") or "").strip()
    if not phone_id or len(phone_id) > 120:
        raise ValidationError("YCloud no devolvió un Phone Number ID válido.")

    channel.phone_number_id = phone_id
    channel.ycloud_waba_id = remote_waba
    channel.ycloud_status = status
    channel.ycloud_last_synced_at = timezone.now()
    channel.ycloud_enabled = True
    channel.twilio_enabled = False
    channel.meta_enabled = False
    channel.activation_status = WhatsAppNumber.STATUS_ACTIVE
    channel.save()
    return channel
