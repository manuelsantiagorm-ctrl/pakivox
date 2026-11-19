import hashlib
import hmac
import json
import time

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    Conversation,
    Message,
    MessagingCustomer,
    WebhookEvent,
    WhatsAppNumber,
)
from .ycloud_api import normalize_ycloud_phone


class YCloudWebhookError(Exception):
    """Evento YCloud inválido o que no puede asociarse de forma segura."""


def verify_ycloud_signature(raw_body, signature_header, now=None):
    secret = str(getattr(settings, "YCLOUD_WEBHOOK_SECRET", "") or "").strip()
    if not secret:
        raise YCloudWebhookError("Webhook no configurado.")

    values = {}
    for item in str(signature_header or "").split(","):
        if "=" not in item:
            continue
        key, value = (part.strip() for part in item.split("=", 1))
        if key in values:
            raise YCloudWebhookError("Firma inválida.")
        values[key] = value

    timestamp = values.get("t", "")
    received = values.get("s", "")
    if not timestamp.isdigit() or len(received) != 64:
        raise YCloudWebhookError("Firma inválida.")

    try:
        tolerance = max(
            0,
            int(getattr(settings, "YCLOUD_WEBHOOK_TOLERANCE_SECONDS", 300)),
        )
    except (TypeError, ValueError) as error:
        raise YCloudWebhookError("Tolerancia de webhook inválida.") from error

    current_time = int(time.time() if now is None else now)
    if abs(current_time - int(timestamp)) > tolerance:
        raise YCloudWebhookError("Firma expirada.")

    signed_payload = timestamp.encode("ascii") + b"." + bytes(raw_body)
    expected = hmac.new(
        secret.encode("utf-8"),
        signed_payload,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(received.lower(), expected):
        raise YCloudWebhookError("Firma inválida.")
    return True


def parse_ycloud_payload(raw_body):
    try:
        payload = json.loads(bytes(raw_body).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
        raise YCloudWebhookError("JSON inválido.") from error

    allowed_types = {
        "whatsapp.inbound_message.received",
        "whatsapp.message.updated",
    }
    if (
        not isinstance(payload, dict)
        or not str(payload.get("id") or "").strip()
        or payload.get("apiVersion") != "v2"
        or payload.get("type") not in allowed_types
    ):
        raise YCloudWebhookError("Evento inválido.")
    return payload


def _resolve_channel(number):
    normalized = normalize_ycloud_phone(number)
    if not normalized:
        raise YCloudWebhookError("Número receptor ausente.")
    matches = list(
        WhatsAppNumber.objects.filter(
            provider=WhatsAppNumber.PROVIDER_YCLOUD,
            ycloud_enabled=True,
            is_active=True,
            activation_status=WhatsAppNumber.STATUS_ACTIVE,
            company__is_active=True,
            warehouse__is_active=True,
            display_number_normalized=normalized,
        )
        .select_related("company", "warehouse")
        .order_by("pk")
    )
    if len(matches) != 1:
        raise YCloudWebhookError("Canal YCloud inexistente o ambiguo.")
    channel = matches[0]
    return channel


def _event_type(payload_type, event_id):
    value = f"ycloud_{payload_type}_{event_id}"
    if len(value) > WebhookEvent._meta.get_field("event_type").max_length:
        digest = hashlib.sha256(str(event_id).encode("utf-8")).hexdigest()[:20]
        value = f"ycloud_{payload_type}_{digest}"
    return value


def process_ycloud_event(payload):
    event_kind = payload["type"]
    event_id = str(payload["id"]).strip()
    inbound = event_kind == "whatsapp.inbound_message.received"
    data_key = "whatsappInboundMessage" if inbound else "whatsappMessage"
    data = payload.get(data_key)
    if not isinstance(data, dict):
        raise YCloudWebhookError("Mensaje ausente.")

    channel = _resolve_channel(data.get("to" if inbound else "from"))
    ycloud_id = str(data.get("id") or "").strip()
    wamid = str(data.get("wamid") or "").strip()
    external_id = str(data.get("externalId") or "").strip()
    provider_id = wamid or ycloud_id or external_id
    if not provider_id:
        raise YCloudWebhookError("ID ausente.")

    event_lookup = {
        "company": channel.company,
        "whatsapp_number": channel,
        "event_type": _event_type(event_kind, event_id),
        "provider_message_id": provider_id,
    }
    try:
        event, created = WebhookEvent.objects.get_or_create(
            **event_lookup,
            defaults={"payload": {"id": event_id, "type": event_kind}},
        )
    except IntegrityError:
        event = WebhookEvent.objects.get(**event_lookup)
        created = False
    if not created and event.is_processed:
        return event

    try:
        with transaction.atomic():
            if inbound:
                sender = normalize_ycloud_phone(data.get("from"))
                if not sender:
                    raise YCloudWebhookError("Remitente ausente.")

                profile = data.get("customerProfile") or {}
                customer, _ = MessagingCustomer.objects.get_or_create(
                    company=channel.company,
                    phone=sender,
                    defaults={
                        "name": str(profile.get("name") or sender)[:255],
                        "source": "whatsapp_ycloud",
                    },
                )
                conversation, _ = Conversation.objects.get_or_create(
                    company=channel.company,
                    whatsapp_number=channel,
                    customer=customer,
                    defaults={
                        "status": Conversation.STATUS_OPEN,
                        "last_message_at": timezone.now(),
                    },
                )
                if conversation.status == Conversation.STATUS_CLOSED:
                    conversation.status = Conversation.STATUS_OPEN
                    conversation.save(update_fields=["status", "updated_at"])

                message_type = str(data.get("type") or Message.TYPE_UNKNOWN)[:30]
                text = data.get("text") or {}
                Message.objects.get_or_create(
                    company=channel.company,
                    whatsapp_number=channel,
                    provider_message_id=provider_id,
                    defaults={
                        "conversation": conversation,
                        "ycloud_message_id": ycloud_id or None,
                        "whatsapp_message_id": wamid or None,
                        "provider_external_id": external_id or None,
                        "direction": Message.DIRECTION_INBOUND,
                        "sender_type": Message.SENDER_CUSTOMER,
                        "message_type": message_type,
                        "body": str(text.get("body") or f"[{message_type}]"),
                        "raw_payload": data,
                        "status": Message.STATUS_RECEIVED,
                        "provider_timestamp": timezone.now(),
                    },
                )
            else:
                status = str(data.get("status") or "").lower()
                allowed_statuses = {
                    Message.STATUS_SENT,
                    Message.STATUS_DELIVERED,
                    Message.STATUS_READ,
                    Message.STATUS_FAILED,
                }
                if status not in allowed_statuses:
                    raise YCloudWebhookError("Estado no soportado.")

                identifier_query = Q()
                if ycloud_id:
                    identifier_query |= Q(provider_message_id=ycloud_id)
                    identifier_query |= Q(ycloud_message_id=ycloud_id)
                if wamid:
                    identifier_query |= Q(provider_message_id=wamid)
                    identifier_query |= Q(whatsapp_message_id=wamid)
                if external_id:
                    identifier_query |= Q(provider_external_id=external_id)

                message = Message.objects.filter(
                    company=channel.company,
                    whatsapp_number=channel,
                    direction=Message.DIRECTION_OUTBOUND,
                ).filter(identifier_query).first()
                if message is None:
                    raise YCloudWebhookError("Mensaje no encontrado en el tenant.")

                rank = {
                    Message.STATUS_SENT: 1,
                    Message.STATUS_DELIVERED: 2,
                    Message.STATUS_READ: 3,
                }
                should_update = status == Message.STATUS_FAILED or (
                    rank.get(status, 0) > rank.get(message.status, 0)
                )
                if should_update:
                    message.status = status
                    message.raw_payload = data
                    message.save(
                        update_fields=["status", "raw_payload", "updated_at"]
                    )

            event.mark_processed()
        return event
    except Exception as error:
        event.mark_failed(str(error))
        raise
