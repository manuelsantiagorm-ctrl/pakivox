from __future__ import annotations

import sentry_sdk

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import time
import uuid

from django.db import IntegrityError, transaction
from django.utils import timezone

from shipping_hub.models import (
    CompanyShippingIntegration,
    ShippingProviderEvent,
    ShippingPurchase,
)
from shipping_hub.services.tracking import apply_event, normalize_event, verify_signature
from shipping_hub.services.lifecycle_service import (
    SOURCE_EVENT_ENGINE,
    SOURCE_SYSTEM,
    transition_provider_event,
)


EVENT_LEASE_SECONDS = 120
EVENT_RETRY_DELAYS = (15, 45, 120, 300, 900, 3600, 10800, 21600)
MAX_EVENT_ATTEMPTS = len(EVENT_RETRY_DELAYS)

SENSITIVE_HEADER_NAMES = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "api-key",
    "proxy-authorization",
}

SIGNATURE_HEADERS = (
    "X-WMS360-Signature",
    "X-Webhook-Signature",
    "X-Hub-Signature-256",
)


@dataclass(frozen=True)
class EventProcessResult:
    event_id: int
    processed: bool
    status: str
    message: str


def sanitize_headers(headers) -> dict:
    sanitized = {}
    for name, value in headers.items():
        normalized = str(name).strip()
        if normalized.casefold() in SENSITIVE_HEADER_NAMES:
            continue
        text = str(value or "")
        sanitized[normalized[:100]] = text[:1000]
    return sanitized


def parse_json_body(body: bytes) -> dict:
    if not body:
        raise ValueError("El webhook no contiene cuerpo.")
    payload = json.loads(body.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("El webhook debe contener un objeto JSON.")
    return payload


def supplied_signature(headers) -> str:
    for name in SIGNATURE_HEADERS:
        value = headers.get(name)
        if value:
            return str(value).strip()
    return ""


def _nested_data(payload):
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def normalize_provider_envelope(*, provider: str, payload: dict, body: bytes) -> dict:
    data = _nested_data(payload)

    event_id = str(
        payload.get("event_id")
        or payload.get("eventId")
        or payload.get("id")
        or data.get("event_id")
        or data.get("eventId")
        or data.get("id")
        or ""
    ).strip()

    event_type = str(
        payload.get("event_type")
        or payload.get("eventType")
        or payload.get("type")
        or data.get("event_type")
        or data.get("eventType")
        or data.get("type")
        or data.get("status")
        or data.get("event_code")
        or "unknown"
    ).strip()[:100]

    provider_purchase_id = str(
        payload.get("shipment_id")
        or payload.get("shipmentId")
        or payload.get("purchase_id")
        or payload.get("purchaseId")
        or data.get("shipment_id")
        or data.get("shipmentId")
        or data.get("purchase_id")
        or data.get("purchaseId")
        or ""
    ).strip()[:200]

    tracking_number = str(
        payload.get("tracking_number")
        or payload.get("trackingNumber")
        or data.get("tracking_number")
        or data.get("trackingNumber")
        or data.get("tracking")
        or data.get("guide")
        or ""
    ).strip()[:100]

    payload_sha256 = hashlib.sha256(body).hexdigest()
    if not event_id:
        event_id = payload_sha256

    dedupe_material = "|".join(
        (
            str(provider or "").strip().casefold(),
            event_id,
            event_type.casefold(),
            provider_purchase_id,
            tracking_number.casefold(),
            payload_sha256,
        )
    )
    deduplication_key = hashlib.sha256(dedupe_material.encode("utf-8")).hexdigest()

    return {
        "provider_event_id": event_id[:255],
        "deduplication_key": deduplication_key,
        "event_type": event_type,
        "provider_purchase_id": provider_purchase_id,
        "tracking_number": tracking_number,
        "payload_sha256": payload_sha256,
    }


def validate_event_signature(*, integration, body: bytes, headers) -> tuple[bool, str]:
    signature = supplied_signature(headers)
    if not signature:
        return False, ShippingProviderEvent.SIGNATURE_NOT_PROVIDED

    try:
        valid = verify_signature(
            integration=integration,
            body=body,
            supplied_signature=signature,
        )
    except ValueError:
        return False, ShippingProviderEvent.SIGNATURE_NOT_SUPPORTED

    return (
        bool(valid),
        ShippingProviderEvent.SIGNATURE_VALID
        if valid
        else ShippingProviderEvent.SIGNATURE_INVALID,
    )


def _find_purchase(*, integration, provider_purchase_id="", tracking_number=""):
    queryset = ShippingPurchase.objects.filter(
        company=integration.company,
        integration=integration,
        provider=integration.provider,
    ).select_related("carrier_shipment")

    if provider_purchase_id:
        purchase = queryset.filter(
            provider_purchase_id=provider_purchase_id,
        ).order_by("-requested_at", "-pk").first()
        if purchase:
            return purchase

        purchase = queryset.filter(
            carrier_shipment__external_shipment_id=provider_purchase_id,
        ).order_by("-requested_at", "-pk").first()
        if purchase:
            return purchase

    if tracking_number:
        return queryset.filter(
            carrier_shipment__master_tracking_number__iexact=tracking_number,
        ).order_by("-requested_at", "-pk").first()

    return None


def store_provider_event(*, integration, body: bytes, payload: dict, headers):
    envelope = normalize_provider_envelope(
        provider=integration.provider,
        payload=payload,
        body=body,
    )
    signature_valid, signature_status = validate_event_signature(
        integration=integration,
        body=body,
        headers=headers,
    )
    if not signature_valid:
        return None, False, signature_status

    purchase = _find_purchase(
        integration=integration,
        provider_purchase_id=envelope["provider_purchase_id"],
        tracking_number=envelope["tracking_number"],
    )
    shipment = purchase.carrier_shipment if purchase else None

    defaults = {
        "deduplication_key": envelope["deduplication_key"],
        "event_type": envelope["event_type"],
        "integration": integration,
        "purchase": purchase,
        "carrier_shipment": shipment,
        "provider_purchase_id": envelope["provider_purchase_id"],
        "tracking_number": envelope["tracking_number"],
        "payload": payload,
        "sanitized_headers": sanitize_headers(headers),
        "payload_sha256": envelope["payload_sha256"],
        "signature_status": signature_status,
        "status": ShippingProviderEvent.STATUS_RECEIVED,
        "next_retry_at": timezone.now(),
    }

    try:
        with transaction.atomic():
            event = ShippingProviderEvent.objects.create(
                company=integration.company,
                provider=integration.provider,
                environment=integration.environment,
                provider_event_id=envelope["provider_event_id"],
                **defaults,
            )
        return event, True, signature_status
    except IntegrityError:
        event = ShippingProviderEvent.objects.filter(
            company=integration.company,
            provider=integration.provider,
            environment=integration.environment,
            provider_event_id=envelope["provider_event_id"],
        ).first()
        if event is None:
            event = ShippingProviderEvent.objects.get(
                company=integration.company,
                provider=integration.provider,
                environment=integration.environment,
                deduplication_key=envelope["deduplication_key"],
            )
        return event, False, signature_status


def event_candidate_queryset(*, now=None):
    now = now or timezone.now()
    return ShippingProviderEvent.objects.filter(
        status__in=[
            ShippingProviderEvent.STATUS_RECEIVED,
            ShippingProviderEvent.STATUS_RETRY,
        ],
        next_retry_at__lte=now,
        lease_expires_at__isnull=True,
    ).order_by("next_retry_at", "received_at", "pk")


def release_expired_event_leases(*, limit=500) -> int:
    now = timezone.now()
    ids = list(
        ShippingProviderEvent.objects.filter(
            status=ShippingProviderEvent.STATUS_PROCESSING,
            lease_expires_at__isnull=False,
            lease_expires_at__lte=now,
        )
        .order_by("lease_expires_at", "pk")
        .values_list("pk", flat=True)[: max(1, min(limit, 5000))]
    )
    if not ids:
        return 0

    changed = 0
    for event_id in ids:
        with transaction.atomic():
            event = (
                ShippingProviderEvent.objects.select_for_update()
                .filter(
                    pk=event_id,
                    status=ShippingProviderEvent.STATUS_PROCESSING,
                    lease_expires_at__isnull=False,
                    lease_expires_at__lte=now,
                )
                .first()
            )
            if event is None:
                continue

            transition_provider_event(
                entity_id=event.pk,
                company_id=event.company_id,
                to_state=ShippingProviderEvent.STATUS_RETRY,
                source=SOURCE_SYSTEM,
                reason=(
                    "Lease vencido: el worker anterior no terminó y el "
                    "evento debe reintentarse de forma idempotente."
                ),
                metadata={"operation": "release_expired_event_lease"},
            )

            event.lease_expires_at = None
            event.next_retry_at = now
            event.error_code = "event_lease_expired"
            event.error_message = (
                "El worker anterior se interrumpió. El evento se volverá a "
                "procesar de forma idempotente."
            )
            event.save(
                update_fields=[
                    "lease_expires_at",
                    "next_retry_at",
                    "error_code",
                    "error_message",
                    "updated_at",
                ]
            )
            changed += 1
    return changed


def claim_event(event_id: int):
    now = timezone.now()
    with transaction.atomic():
        # PostgreSQL: bloquear solamente la fila base del evento.
        # purchase y carrier_shipment son relaciones nullable; incluirlas
        # mediante select_related() en SELECT ... FOR UPDATE genera un
        # LEFT OUTER JOIN que PostgreSQL no permite bloquear.
        event = (
            ShippingProviderEvent.objects.select_for_update()
            .filter(pk=event_id)
            .first()
        )

        if event is not None:
            # Las relaciones se cargan después del lock mediante una consulta
            # de lectura. El bloqueo sobre ShippingProviderEvent permanece
            # vigente hasta terminar transaction.atomic().
            event = (
                ShippingProviderEvent.objects.select_related(
                    "company",
                    "integration",
                    "purchase",
                    "carrier_shipment",
                )
                .get(pk=event.pk)
            )
        if event is None:
            return None
        if event.status not in {
            ShippingProviderEvent.STATUS_RECEIVED,
            ShippingProviderEvent.STATUS_RETRY,
        }:
            return None
        if event.next_retry_at and event.next_retry_at > now:
            return None
        if event.lease_expires_at and event.lease_expires_at > now:
            return None

        transition_provider_event(
            entity_id=event.pk,
            company_id=event.company_id,
            to_state=ShippingProviderEvent.STATUS_PROCESSING,
            source=SOURCE_EVENT_ENGINE,
            reason="El worker tomó el evento para procesamiento.",
            metadata={
                "operation": "claim_event",
                "next_attempt": event.processing_attempts + 1,
            },
        )

        event.processing_attempts += 1
        event.processing_started_at = now
        event.lease_expires_at = now + timedelta(
            seconds=EVENT_LEASE_SECONDS
        )
        event.save(
            update_fields=[
                "processing_attempts",
                "processing_started_at",
                "lease_expires_at",
                "updated_at",
            ]
        )
        event.refresh_from_db(fields=["status"])
        return event


def _schedule_purchase_recovery(event):
    purchase = event.purchase
    if purchase is None:
        purchase = _find_purchase(
            integration=event.integration,
            provider_purchase_id=event.provider_purchase_id,
            tracking_number=event.tracking_number,
        )
    if purchase is None:
        return None

    with transaction.atomic():
        purchase = ShippingPurchase.objects.select_for_update().get(
            pk=purchase.pk,
            company_id=event.company_id,
        )
        if purchase.status in {
            ShippingPurchase.STATUS_COMPLETED,
            ShippingPurchase.STATUS_CANCELLED,
        }:
            return purchase

        transition_purchase(
            entity_id=purchase.pk,
            company_id=purchase.company_id,
            to_state=ShippingPurchase.STATUS_RECONCILING,
            source=SOURCE_EVENT_ENGINE,
            reason=(
                "El proveedor notificó una actualización. "
                "WMS360 conciliará sin volver a generar la guía."
            ),
            actor=purchase.requested_by,
            metadata={
                "operation": "schedule_purchase_recovery",
                "provider_event_id": event.pk,
                "provider": event.provider,
            },
        )
        purchase.charge_status = ShippingPurchase.CHARGE_POSSIBLE
        purchase.next_retry_at = timezone.now()
        purchase.lease_expires_at = None
        purchase.last_error_code = "provider_event_received"
        purchase.last_error_message = (
            "El proveedor notificó una actualización. WMS360 conciliará la "
            "compra sin volver a generarla."
        )
        purchase.save(
            update_fields=[
                "charge_status",
                "next_retry_at",
                "lease_expires_at",
                "last_error_code",
                "last_error_message",
                "updated_at",
            ]
        )
        if event.purchase_id != purchase.pk:
            event.purchase = purchase
            event.carrier_shipment = purchase.carrier_shipment
            event.save(
                update_fields=["purchase", "carrier_shipment", "updated_at"]
            )
    return purchase


def _looks_like_tracking_event(event) -> bool:
    event_type = str(event.event_type or "").casefold()
    return bool(
        event.tracking_number
        or any(
            token in event_type
            for token in (
                "tracking",
                "transit",
                "delivered",
                "delivery",
                "shipment.status",
                "guide.status",
                "picked",
                "exception",
                "cancel",
            )
        )
    )


def _looks_like_purchase_event(event) -> bool:
    event_type = str(event.event_type or "").casefold()
    return bool(
        event.provider_purchase_id
        or any(
            token in event_type
            for token in (
                "label",
                "guide",
                "shipment",
                "purchase",
                "created",
                "ready",
            )
        )
    )


def _retry_at(attempts: int):
    index = max(0, min(attempts - 1, len(EVENT_RETRY_DELAYS) - 1))
    return timezone.now() + timedelta(seconds=EVENT_RETRY_DELAYS[index])


def _finish_event(*, event_id, status, message="", error_code="", error_message=""):
    now = timezone.now()
    with transaction.atomic():
        event = ShippingProviderEvent.objects.select_for_update().get(
            pk=event_id
        )

        transition_provider_event(
            entity_id=event.pk,
            company_id=event.company_id,
            to_state=status,
            source=SOURCE_EVENT_ENGINE,
            reason=message or error_message or "Finalización del evento.",
            metadata={
                "operation": "finish_event",
                "error_code": str(error_code or "")[:100],
            },
        )

        event.processed_at = (
            now
            if status
            in {
                ShippingProviderEvent.STATUS_PROCESSED,
                ShippingProviderEvent.STATUS_IGNORED,
                ShippingProviderEvent.STATUS_DEAD_LETTER,
            }
            else None
        )
        event.next_retry_at = None
        event.lease_expires_at = None
        event.error_code = str(error_code or "")[:100]
        event.error_message = str(
            error_message or message or ""
        )[:4000]
        event.save(
            update_fields=[
                "processed_at",
                "next_retry_at",
                "lease_expires_at",
                "error_code",
                "error_message",
                "updated_at",
            ]
        )

    return EventProcessResult(
        event_id=event_id,
        processed=True,
        status=status,
        message=message or error_message,
    )


def process_provider_event(event_id: int) -> EventProcessResult:
    event = claim_event(event_id)
    if event is None:
        return EventProcessResult(
            event_id=event_id,
            processed=False,
            status="",
            message="El evento no estaba disponible para procesamiento.",
        )

    try:
        actions = []

        if _looks_like_purchase_event(event):
            purchase = _schedule_purchase_recovery(event)
            if purchase:
                actions.append("purchase_recovery_scheduled")

        if _looks_like_tracking_event(event):
            normalized = normalize_event(event.payload)
            if not normalized.get("tracking_number") and event.tracking_number:
                normalized["tracking_number"] = event.tracking_number
            if not normalized.get("provider_event_id"):
                normalized["provider_event_id"] = event.provider_event_id

            tracking_event, _ = apply_event(
                integration=event.integration,
                event=normalized,
            )
            if tracking_event:
                actions.append("tracking_applied")
                if event.carrier_shipment_id is None:
                    event.carrier_shipment = tracking_event.carrier_shipment
                    event.save(
                        update_fields=["carrier_shipment", "updated_at"]
                    )

        if not actions:
            return _finish_event(
                event_id=event.pk,
                status=ShippingProviderEvent.STATUS_IGNORED,
                message=(
                    "El evento fue válido pero no contiene una actualización "
                    "reconocible para esta versión del procesador."
                ),
                error_code="event_not_actionable",
            )

        return _finish_event(
            event_id=event.pk,
            status=ShippingProviderEvent.STATUS_PROCESSED,
            message="Evento procesado: " + ", ".join(actions),
        )

    except Exception as error:
        sentry_sdk.capture_exception(error)
        now = timezone.now()
        with transaction.atomic():
            locked = ShippingProviderEvent.objects.select_for_update().get(
                pk=event.pk
            )
            error_name = type(error).__name__

            if locked.processing_attempts >= MAX_EVENT_ATTEMPTS:
                target_status = ShippingProviderEvent.STATUS_DEAD_LETTER
                next_retry_at = None
                processed_at = now
                message = (
                    "El evento agotó sus intentos automáticos y pasó a "
                    "revisión manual."
                )
            else:
                target_status = ShippingProviderEvent.STATUS_RETRY
                next_retry_at = _retry_at(locked.processing_attempts)
                processed_at = None
                message = "El evento quedó programado para reintento seguro."

            transition_provider_event(
                entity_id=locked.pk,
                company_id=locked.company_id,
                to_state=target_status,
                source=SOURCE_EVENT_ENGINE,
                reason=message,
                metadata={
                    "operation": "event_processing_error",
                    "exception_type": error_name,
                    "attempts": locked.processing_attempts,
                },
            )

            locked.lease_expires_at = None
            locked.next_retry_at = next_retry_at
            locked.processed_at = processed_at
            locked.error_code = "event_processing_error"
            locked.error_message = (
                "El evento no pudo procesarse todavía: " + error_name
            )[:4000]
            locked.save(
                update_fields=[
                    "lease_expires_at",
                    "next_retry_at",
                    "processed_at",
                    "error_code",
                    "error_message",
                    "updated_at",
                ]
            )

        return EventProcessResult(
            event_id=event.pk,
            processed=True,
            status=target_status,
            message=message,
        )
