"""
Seguridad y parsing del webhook oficial de Meta WhatsApp.

REGLA CRÍTICA:

Un POST nunca debe confiar en el JSON recibido hasta validar
primero X-Hub-Signature-256 utilizando el META_APP_SECRET
central de WMS360.

El aislamiento por company se realizará posteriormente usando
exclusivamente metadata.phone_number_id.
"""

import hashlib
import hmac
import json

from django.conf import settings


class MetaWebhookError(Exception):
    """
    Error general procesando un webhook Meta.
    """


class MetaWebhookConfigurationError(MetaWebhookError):
    """
    Configuración central de Meta incompleta.
    """


class MetaWebhookVerificationError(MetaWebhookError):
    """
    Falló la verificación GET del callback.
    """


class MetaWebhookSignatureError(MetaWebhookError):
    """
    La firma HMAC del POST es inválida.
    """


class MetaWebhookPayloadError(MetaWebhookError):
    """
    El JSON recibido no cumple el contrato mínimo esperado.
    """


# ============================================================
# GET — VERIFICACIÓN DEL CALLBACK
# ============================================================


def verify_meta_webhook_challenge(
    *,
    mode,
    verify_token,
    challenge,
):
    """
    Verifica el GET inicial utilizado por Meta para confirmar
    que WMS360 controla el callback configurado.

    Devuelve hub.challenge únicamente cuando:
    - hub.mode == subscribe
    - el verify token coincide en tiempo constante
    - challenge existe
    """

    expected_token = str(
        getattr(
            settings,
            "META_WEBHOOK_VERIFY_TOKEN",
            "",
        )
        or ""
    ).strip()

    if not expected_token:
        raise MetaWebhookConfigurationError(
            "META_WEBHOOK_VERIFY_TOKEN no está configurado."
        )

    mode = str(
        mode or ""
    ).strip()

    received_token = str(
        verify_token or ""
    ).strip()

    challenge = str(
        challenge or ""
    ).strip()

    if mode != "subscribe":
        raise MetaWebhookVerificationError(
            "Modo de verificación Meta inválido."
        )

    if not received_token:
        raise MetaWebhookVerificationError(
            "No se recibió el token de verificación."
        )

    if not hmac.compare_digest(
        received_token,
        expected_token,
    ):
        raise MetaWebhookVerificationError(
            "Token de verificación Meta inválido."
        )

    if not challenge:
        raise MetaWebhookVerificationError(
            "Meta no envió hub.challenge."
        )

    return challenge


# ============================================================
# POST — X-HUB-SIGNATURE-256
# ============================================================


def verify_meta_webhook_signature(
    *,
    raw_body,
    signature_header,
):
    """
    Verifica la firma del cuerpo RAW del POST.

    Formato esperado:

        X-Hub-Signature-256: sha256=<digest>

    IMPORTANTE:
    raw_body debe ser exactamente request.body.
    """

    app_secret = str(
        getattr(
            settings,
            "META_APP_SECRET",
            "",
        )
        or ""
    ).strip()

    if not app_secret:
        raise MetaWebhookConfigurationError(
            "META_APP_SECRET no está configurado."
        )

    if not isinstance(
        raw_body,
        (bytes, bytearray),
    ):
        raise MetaWebhookSignatureError(
            "El cuerpo RAW del webhook no es válido."
        )

    signature_header = str(
        signature_header or ""
    ).strip()

    prefix = "sha256="

    if not signature_header.startswith(prefix):
        raise MetaWebhookSignatureError(
            "Falta una firma Meta válida."
        )

    received_digest = signature_header[
        len(prefix):
    ].strip().lower()

    if (
        not received_digest
        or len(received_digest) != 64
        or any(
            character not in "0123456789abcdef"
            for character in received_digest
        )
    ):
        raise MetaWebhookSignatureError(
            "La firma Meta tiene formato inválido."
        )

    expected_digest = hmac.new(
        app_secret.encode("utf-8"),
        bytes(raw_body),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(
        received_digest,
        expected_digest,
    ):
        raise MetaWebhookSignatureError(
            "Firma Meta inválida."
        )

    return True


# ============================================================
# JSON
# ============================================================


def parse_meta_webhook_payload(
    raw_body,
):
    """
    Convierte request.body en dict únicamente después de que
    la vista haya validado la firma.

    Esta función no realiza verificación criptográfica.
    """

    if not isinstance(
        raw_body,
        (bytes, bytearray),
    ):
        raise MetaWebhookPayloadError(
            "El cuerpo del webhook no es válido."
        )

    try:
        payload = json.loads(
            bytes(raw_body).decode("utf-8")
        )

    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as error:
        raise MetaWebhookPayloadError(
            "Meta envió un JSON inválido."
        ) from error

    if not isinstance(
        payload,
        dict,
    ):
        raise MetaWebhookPayloadError(
            "El payload Meta debe ser un objeto JSON."
        )

    if payload.get(
        "object"
    ) != "whatsapp_business_account":
        raise MetaWebhookPayloadError(
            "El webhook no pertenece a WhatsApp Business."
        )

    return payload


# ============================================================
# EXTRAER CAMBIOS DE WHATSAPP
# ============================================================


def extract_meta_message_changes(
    payload,
):
    """
    Extrae únicamente cambios field=messages.

    Devuelve bloques neutrales:

    [
        {
            "waba_id": "...",
            "phone_number_id": "...",
            "display_phone_number": "...",
            "messages": [...],
            "statuses": [...],
            "value": {...},
        }
    ]

    Todavía NO resuelve company.
    Esa operación se hará estrictamente mediante
    phone_number_id en la siguiente capa.
    """

    if not isinstance(
        payload,
        dict,
    ):
        raise MetaWebhookPayloadError(
            "Payload Meta inválido."
        )

    entries = payload.get(
        "entry",
        [],
    )

    if not isinstance(
        entries,
        list,
    ):
        raise MetaWebhookPayloadError(
            "entry debe ser una lista."
        )

    extracted = []

    for entry in entries:
        if not isinstance(
            entry,
            dict,
        ):
            continue

        waba_id = str(
            entry.get("id")
            or ""
        ).strip()

        changes = entry.get(
            "changes",
            [],
        )

        if not isinstance(
            changes,
            list,
        ):
            raise MetaWebhookPayloadError(
                "changes debe ser una lista."
            )

        for change in changes:
            if not isinstance(
                change,
                dict,
            ):
                continue

            if change.get("field") != "messages":
                continue

            value = change.get(
                "value",
                {},
            )

            if not isinstance(
                value,
                dict,
            ):
                raise MetaWebhookPayloadError(
                    "value debe ser un objeto."
                )

            metadata = value.get(
                "metadata",
                {},
            )

            if not isinstance(
                metadata,
                dict,
            ):
                raise MetaWebhookPayloadError(
                    "metadata debe ser un objeto."
                )

            phone_number_id = str(
                metadata.get(
                    "phone_number_id"
                )
                or ""
            ).strip()

            if (
                not phone_number_id
                or not phone_number_id.isdigit()
            ):
                raise MetaWebhookPayloadError(
                    (
                        "El webhook no contiene un "
                        "Phone Number ID válido."
                    )
                )

            display_phone_number = str(
                metadata.get(
                    "display_phone_number"
                )
                or ""
            ).strip()

            messages = value.get(
                "messages",
                [],
            )

            statuses = value.get(
                "statuses",
                [],
            )

            if messages is None:
                messages = []

            if statuses is None:
                statuses = []

            if not isinstance(
                messages,
                list,
            ):
                raise MetaWebhookPayloadError(
                    "messages debe ser una lista."
                )

            if not isinstance(
                statuses,
                list,
            ):
                raise MetaWebhookPayloadError(
                    "statuses debe ser una lista."
                )

            extracted.append(
                {
                    "waba_id": waba_id,
                    "phone_number_id": (
                        phone_number_id
                    ),
                    "display_phone_number": (
                        display_phone_number
                    ),
                    "messages": messages,
                    "statuses": statuses,
                    "value": value,
                }
            )

    return extracted


# ============================================================
# RESOLUCIÓN SEGURA DEL CANAL / TENANT
# ============================================================


class MetaWebhookChannelError(MetaWebhookError):
    """
    No fue posible resolver de forma segura el canal Meta
    correspondiente al Phone Number ID recibido.
    """


def resolve_meta_whatsapp_channel(
    phone_number_id,
):
    """
    Resuelve exactamente:

        Meta phone_number_id
                ↓
        WhatsAppNumber
                ↓
        Warehouse
                ↓
        Company

    Seguridad SaaS:
    - nunca usa display_number;
    - nunca usa el teléfono del cliente;
    - nunca usa fallback;
    - exige provider Meta;
    - exige meta_enabled;
    - exige canal activo;
    - exige company activa;
    - exige bodega concreta y activa.

    Devuelve un único WhatsAppNumber seguro.
    """

    from .models import WhatsAppNumber

    phone_number_id = str(phone_number_id or "").strip()

    if not phone_number_id:
        raise MetaWebhookChannelError("No se recibió Phone Number ID.")

    if not phone_number_id.isdigit():
        raise MetaWebhookChannelError("El Phone Number ID recibido no es válido.")

    channels = WhatsAppNumber.objects.select_related(
        "company",
        "warehouse",
    ).filter(
        phone_number_id=phone_number_id,
        provider=WhatsAppNumber.PROVIDER_META,
        meta_enabled=True,
        is_active=True,
        company__is_active=True,
        warehouse__isnull=False,
        warehouse__is_active=True,
    )

    # No usamos .first() como fallback.
    #
    # Aunque phone_number_id ya es unique en el modelo,
    # mantenemos esta defensa explícita en la frontera pública.
    if channels.count() != 1:
        raise MetaWebhookChannelError(
            (
                "No existe un único canal Meta activo "
                "para el Phone Number ID recibido."
            )
        )

    channel = channels.first()

    if channel.company_id != channel.warehouse.company_id:
        raise MetaWebhookChannelError(
            ("La bodega del canal Meta no pertenece " "a la empresa del canal.")
        )

    return channel


# ============================================================
# PROCESAMIENTO DE MENSAJES Y ESTADOS META
# ============================================================


class MetaWebhookProcessingError(MetaWebhookError):
    """
    Error procesando un evento Meta ya autenticado.
    """


def _validate_meta_channel_for_processing(
    channel,
):
    """
    Revalida el canal antes de escribir en DB.

    No confiamos únicamente en que el caller haya usado
    previamente resolve_meta_whatsapp_channel().
    """

    if channel is None:
        raise MetaWebhookChannelError("No se recibió un canal Meta.")

    phone_number_id = str(
        getattr(
            channel,
            "phone_number_id",
            "",
        )
        or ""
    ).strip()

    resolved_channel = resolve_meta_whatsapp_channel(phone_number_id)

    if resolved_channel.pk != channel.pk:
        raise MetaWebhookChannelError(
            ("El canal recibido no coincide " "con el Phone Number ID.")
        )

    return resolved_channel


def _meta_provider_datetime(
    value,
):
    """
    Convierte timestamp Unix enviado por Meta
    a datetime aware UTC.

    Si el timestamp no es utilizable devuelve None.
    """

    from datetime import (
        datetime,
        timezone as datetime_timezone,
    )

    try:
        timestamp = int(str(value or "").strip())

    except (
        TypeError,
        ValueError,
    ):
        return None

    if timestamp <= 0:
        return None

    try:
        return datetime.fromtimestamp(
            timestamp,
            tz=datetime_timezone.utc,
        )

    except (
        OverflowError,
        OSError,
        ValueError,
    ):
        return None


# Traducciones amigables de los códigos de error más comunes que Meta
# reporta en los webhooks de estado "failed". Si un código no está en
# este diccionario, se usa el título original que manda Meta como
# respaldo (ver _translate_meta_failure_errors).
META_FAILURE_ERROR_TRANSLATIONS = {
    131047: (
        "Han pasado más de 24 horas sin que el cliente te escriba. "
        "Usa una plantilla aprobada para reabrir la conversación."
    ),
    131026: ("El número del cliente no tiene WhatsApp activo o no es " "válido."),
    131053: (
        "El archivo adjunto no se pudo procesar o el formato no es " "compatible."
    ),
    131031: (
        "Esta cuenta de WhatsApp Business está restringida por "
        "Meta. Revisa el estado de la cuenta en el panel de Meta."
    ),
    132000: (
        "La plantilla usada no coincide con lo que Meta tiene "
        "aprobado. Revisa el contenido de la plantilla."
    ),
    132001: "La plantilla usada no existe o no está aprobada.",
}


def _translate_meta_failure_errors(
    status_errors,
):
    """
    Traduce los errores de un webhook de estado 'failed' de Meta a
    mensajes en español simple, para mostrarlos al agente en el chat.

    Conserva siempre el código y el título original de Meta, y agrega
    un campo 'friendly_message' cuando existe traducción conocida.
    """

    if not isinstance(status_errors, list):
        return status_errors

    translated = []

    for error_item in status_errors:
        if not isinstance(error_item, dict):
            translated.append(error_item)
            continue

        enriched_error = dict(error_item)

        error_code = error_item.get("code")

        friendly_message = META_FAILURE_ERROR_TRANSLATIONS.get(error_code)

        if friendly_message:
            enriched_error["friendly_message"] = friendly_message

        translated.append(enriched_error)

    return translated


def _meta_message_type(
    provider_type,
):
    """
    Mapea el tipo de Meta al modelo Message de WMS360.
    """

    from .models import Message

    provider_type = str(provider_type or "").strip().lower()

    mapping = {
        "text": Message.TYPE_TEXT,
        "image": Message.TYPE_IMAGE,
        "audio": Message.TYPE_AUDIO,
        "video": Message.TYPE_VIDEO,
        "document": Message.TYPE_DOCUMENT,
        "location": Message.TYPE_LOCATION,
        "contacts": Message.TYPE_CONTACT,
        "contact": Message.TYPE_CONTACT,
    }

    return mapping.get(
        provider_type,
        Message.TYPE_UNKNOWN,
    )


def _extract_meta_message_body(
    message,
):
    """
    Extrae únicamente contenido textual útil.

    Los archivos multimedia todavía NO se descargan
    desde Meta en esta fase.
    """

    if not isinstance(
        message,
        dict,
    ):
        return None

    provider_type = str(message.get("type") or "").strip().lower()

    if provider_type == "text":
        text_data = message.get(
            "text",
            {},
        )

        if isinstance(
            text_data,
            dict,
        ):
            body = str(text_data.get("body") or "").strip()

            return body or None

    if provider_type in {
        "image",
        "video",
        "document",
    }:
        media_data = message.get(
            provider_type,
            {},
        )

        if isinstance(
            media_data,
            dict,
        ):
            caption = str(media_data.get("caption") or "").strip()

            return caption or None

    return None


def _extract_meta_profile_name(
    *,
    value,
    normalized_phone,
):
    """
    Busca el nombre del contacto correspondiente al remitente.

    El nombre es opcional y nunca se usa para identificar
    company ni canal.
    """

    from .meta_api import (
        MetaWhatsAppConfigurationError,
        normalize_meta_phone,
    )

    if not isinstance(
        value,
        dict,
    ):
        return None

    contacts = value.get(
        "contacts",
        [],
    )

    if not isinstance(
        contacts,
        list,
    ):
        return None

    for contact in contacts:
        if not isinstance(
            contact,
            dict,
        ):
            continue

        wa_id = contact.get("wa_id")

        try:
            contact_phone = normalize_meta_phone(wa_id)

        except MetaWhatsAppConfigurationError:
            continue

        if contact_phone != normalized_phone:
            continue

        profile = contact.get(
            "profile",
            {},
        )

        if not isinstance(
            profile,
            dict,
        ):
            return None

        name = str(profile.get("name") or "").strip()

        if not name:
            return None

        # MessagingCustomer.name tiene max_length=255.
        return name[:255]

    return None


def _minimal_meta_inbound_event_payload(
    *,
    channel,
    message,
):
    """
    Payload reducido para WebhookEvent.

    Evita duplicar innecesariamente datos personales
    presentes en el webhook completo.
    """

    return {
        "phone_number_id": str(channel.phone_number_id),
        "message": {
            "id": str(message.get("id") or ""),
            "from": str(message.get("from") or ""),
            "timestamp": str(message.get("timestamp") or ""),
            "type": str(message.get("type") or ""),
        },
    }


def _minimal_meta_status_event_payload(
    *,
    channel,
    status_payload,
):
    """
    Guarda únicamente lo necesario para auditoría
    del cambio de estado.
    """

    errors = []

    raw_errors = status_payload.get(
        "errors",
        [],
    )

    if isinstance(
        raw_errors,
        list,
    ):
        for item in raw_errors[:5]:
            if not isinstance(
                item,
                dict,
            ):
                continue

            errors.append(
                {
                    "code": item.get("code"),
                    "title": str(item.get("title") or "")[:255],
                }
            )

    return {
        "phone_number_id": str(channel.phone_number_id),
        "status": {
            "id": str(status_payload.get("id") or ""),
            "status": str(status_payload.get("status") or ""),
            "timestamp": str(status_payload.get("timestamp") or ""),
            "errors": errors,
        },
    }


def process_meta_inbound_message(
    *,
    channel,
    message,
    value=None,
):
    """
    Procesa UN mensaje entrante Meta.

    Seguridad:
    - revalida phone_number_id -> channel -> company;
    - customer siempre queda dentro de channel.company;
    - conversation siempre usa channel;
    - Message queda limitado a company + channel;
    - provider_message_id mantiene idempotencia global;
    - WebhookEvent mantiene idempotencia por tenant/evento.
    """

    from django.db import transaction
    from django.utils import timezone

    from .meta_api import (
        MetaWhatsAppConfigurationError,
        normalize_meta_phone,
    )
    from .models import (
        Conversation,
        Message,
        MessagingCustomer,
        WebhookEvent,
    )

    channel = _validate_meta_channel_for_processing(channel)

    if not isinstance(
        message,
        dict,
    ):
        raise MetaWebhookPayloadError("El mensaje Meta no es válido.")

    provider_message_id = str(message.get("id") or "").strip()

    if not provider_message_id:
        raise MetaWebhookPayloadError("El mensaje Meta no contiene ID.")

    try:
        normalized_from = normalize_meta_phone(message.get("from"))

    except MetaWhatsAppConfigurationError as error:
        raise MetaWebhookPayloadError("El remitente Meta no es válido.") from error

    profile_name = _extract_meta_profile_name(
        value=value or {},
        normalized_phone=normalized_from,
    )

    event_type = "meta_whatsapp_inbound"

    webhook_event, _ = WebhookEvent.objects.get_or_create(
        company=channel.company,
        whatsapp_number=channel,
        event_type=event_type,
        provider_message_id=(provider_message_id),
        defaults={
            "payload": (
                _minimal_meta_inbound_event_payload(
                    channel=channel,
                    message=message,
                )
            ),
        },
    )

    # Retry ya procesado.
    if webhook_event.is_processed:
        existing_message = Message.objects.filter(
            company=channel.company,
            whatsapp_number=channel,
            provider_message_id=(provider_message_id),
            direction=(Message.DIRECTION_INBOUND),
        ).first()

        if existing_message is None:
            webhook_event.mark_failed(
                ("Evento marcado como procesado " "sin Message correspondiente.")
            )

            raise MetaWebhookProcessingError(
                ("Inconsistencia detectada " "procesando mensaje Meta.")
            )

        return existing_message

    try:
        with transaction.atomic():
            customer, customer_created = MessagingCustomer.objects.get_or_create(
                company=channel.company,
                phone=normalized_from,
                defaults={
                    "name": profile_name,
                    "source": ("whatsapp_meta"),
                },
            )

            if not customer_created and profile_name and customer.name != profile_name:
                customer.name = profile_name

                customer.save(
                    update_fields=[
                        "name",
                        "updated_at",
                    ]
                )

            conversation, conversation_created = Conversation.objects.get_or_create(
                company=channel.company,
                whatsapp_number=channel,
                customer=customer,
                defaults={
                    "status": (Conversation.STATUS_OPEN),
                    "last_message_at": (timezone.now()),
                },
            )

            if (
                not conversation_created
                and conversation.status == Conversation.STATUS_CLOSED
            ):
                conversation.status = Conversation.STATUS_OPEN

                conversation.save(
                    update_fields=[
                        "status",
                        "updated_at",
                    ]
                )

            provider_timestamp = _meta_provider_datetime(message.get("timestamp"))

            message_object, created = Message.objects.get_or_create(
                company=channel.company,
                whatsapp_number=channel,
                provider_message_id=(provider_message_id),
                direction=(Message.DIRECTION_INBOUND),
                defaults={
                    "conversation": conversation,
                    "sender_type": (Message.SENDER_CUSTOMER),
                    "message_type": (_meta_message_type(message.get("type"))),
                    "body": (_extract_meta_message_body(message)),
                    "media_url": None,
                    "raw_payload": message,
                    "status": (Message.STATUS_RECEIVED),
                    "provider_timestamp": (provider_timestamp or timezone.now()),
                },
            )

            if not created and message_object.conversation_id != conversation.pk:
                raise MetaWebhookProcessingError(
                    ("El ID del mensaje Meta " "ya pertenece a otra conversación.")
                )

            webhook_event.mark_processed()

        return message_object

    except Exception as error:
        webhook_event.mark_failed(error)
        raise


def _should_apply_meta_status(
    *,
    current_status,
    incoming_status,
):
    """
    Decide si un status puede avanzar el mensaje.

    Nunca permitimos degradar:

        read -> delivered
        read -> sent
        delivered -> sent

    failed se considera terminal.
    """

    from .models import Message

    current_status = str(current_status or "").strip().lower()

    incoming_status = str(incoming_status or "").strip().lower()

    if current_status == incoming_status:
        return False

    if current_status == Message.STATUS_FAILED:
        return False

    if incoming_status == Message.STATUS_FAILED:
        return current_status in {
            Message.STATUS_RECEIVED,
            Message.STATUS_SENT,
        }

    rank = {
        Message.STATUS_SENT: 1,
        Message.STATUS_DELIVERED: 2,
        Message.STATUS_READ: 3,
    }

    # Algunos mensajes salientes pueden existir localmente
    # unos milisegundos como "received" antes de que se
    # registre el resultado del proveedor.
    if current_status == Message.STATUS_RECEIVED:
        return incoming_status in rank

    if current_status in rank and incoming_status in rank:
        return rank[incoming_status] > rank[current_status]

    return False


def process_meta_message_status(
    *,
    channel,
    status_payload,
):
    """
    Procesa UN cambio de estado Meta.

    Solamente puede modificar un Message:
    - de la misma company;
    - del mismo WhatsAppNumber;
    - outbound;
    - con el mismo provider_message_id.

    Si no encuentra ese mensaje, registra el WebhookEvent
    como no procesado para posible conciliación posterior.
    """

    from django.db import transaction

    from .models import (
        Message,
        WebhookEvent,
    )

    channel = _validate_meta_channel_for_processing(channel)

    if not isinstance(
        status_payload,
        dict,
    ):
        raise MetaWebhookPayloadError("El status Meta no es válido.")

    provider_message_id = str(status_payload.get("id") or "").strip()

    if not provider_message_id:
        raise MetaWebhookPayloadError("El status Meta no contiene ID.")

    incoming_status = str(status_payload.get("status") or "").strip().lower()

    supported_statuses = {
        Message.STATUS_SENT,
        Message.STATUS_DELIVERED,
        Message.STATUS_READ,
        Message.STATUS_FAILED,
    }

    if incoming_status not in supported_statuses:
        raise MetaWebhookPayloadError(
            ("Meta envió un estado " "de mensaje no soportado.")
        )

    event_type = f"meta_status_{incoming_status}"

    webhook_event, _ = WebhookEvent.objects.get_or_create(
        company=channel.company,
        whatsapp_number=channel,
        event_type=event_type,
        provider_message_id=(provider_message_id),
        defaults={
            "payload": (
                _minimal_meta_status_event_payload(
                    channel=channel,
                    status_payload=(status_payload),
                )
            ),
        },
    )

    message_object = Message.objects.filter(
        company=channel.company,
        whatsapp_number=channel,
        provider_message_id=(provider_message_id),
        direction=(Message.DIRECTION_OUTBOUND),
    ).first()

    if message_object is None:
        webhook_event.mark_failed(
            ("No se encontró un mensaje saliente " "del mismo tenant para este status.")
        )

        return None

    # Retry idéntico ya procesado.
    if webhook_event.is_processed:
        return message_object

    try:
        with transaction.atomic():
            should_apply = _should_apply_meta_status(
                current_status=(message_object.status),
                incoming_status=(incoming_status),
            )

            if should_apply:
                provider_timestamp = _meta_provider_datetime(
                    status_payload.get("timestamp")
                )
                update_values = {
                    "status": incoming_status,
                }
                if provider_timestamp:
                    update_values["provider_timestamp"] = provider_timestamp
                if incoming_status == Message.STATUS_FAILED:
                    status_errors = status_payload.get("errors")
                    if status_errors:
                        merged_payload = dict(message_object.raw_payload or {})
                        merged_payload["failure_errors"] = (
                            _translate_meta_failure_errors(status_errors)
                        )
                        update_values["raw_payload"] = merged_payload
                Message.objects.filter(
                    pk=message_object.pk,
                    company=channel.company,
                    whatsapp_number=channel,
                    direction=(Message.DIRECTION_OUTBOUND),
                ).update(**update_values)

                message_object.refresh_from_db()

            webhook_event.mark_processed()

        return message_object

    except Exception as error:
        webhook_event.mark_failed(error)
        raise
