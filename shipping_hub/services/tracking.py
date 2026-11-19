from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from shipping_hub.models import (
    CarrierShipment,
    CompanyShippingIntegration,
    ShipmentPackage,
    ShipmentTrackingEvent,
    TrackingSyncAttempt,
    TrackingWebhookReceipt,
)
from shipping_hub.services.credentials import decrypt_credentials
from shipping_hub.services.lifecycle import InvalidLifecycleTransition
from shipping_hub.services.delivery_closure import close_operational_delivery
from shipping_hub.services.lifecycle_service import (
    SOURCE_TRACKING_ENGINE,
    transition_package,
    transition_shipment,
)


STATUS_ALIASES = {
    "created": CarrierShipment.STATUS_LABEL_READY,
    "label_ready": CarrierShipment.STATUS_LABEL_READY,
    "ready": CarrierShipment.STATUS_READY,
    "shipped": CarrierShipment.STATUS_SHIPPED,
    "picked_up": CarrierShipment.STATUS_IN_TRANSIT,
    "in_transit": CarrierShipment.STATUS_IN_TRANSIT,
    "out_for_delivery": CarrierShipment.STATUS_OUT_FOR_DELIVERY,
    "delivered": CarrierShipment.STATUS_DELIVERED,
    "exception": CarrierShipment.STATUS_EXCEPTION,
    "failed_attempt": CarrierShipment.STATUS_EXCEPTION,
    "cancelled": CarrierShipment.STATUS_CANCELLED,
}


def normalize_status(value):
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return STATUS_ALIASES.get(key, "")


def webhook_secret(integration):
    values = decrypt_credentials(integration.webhook_secret_encrypted)
    secret = str(values.get("webhook_secret") or "").strip()
    if not secret:
        raise ValueError("La integración no tiene secreto de webhook configurado.")
    return secret


def verify_signature(*, integration, body, supplied_signature):
    supplied = str(supplied_signature or "").strip()
    if supplied.startswith("sha256="):
        supplied = supplied[7:]
    expected = hmac.new(webhook_secret(integration).encode(), body, hashlib.sha256).hexdigest()
    return bool(supplied) and hmac.compare_digest(expected, supplied)


def _occurred_at(value):
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = parse_datetime(str(value or ""))
    if parsed is None:
        return timezone.now()
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed)
    return parsed


def normalize_event(payload):
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    tracking = str(data.get("tracking_number") or data.get("trackingNumber") or "").strip()
    event_id = str(data.get("event_id") or data.get("id") or payload.get("id") or "").strip()
    raw_status = data.get("status") or data.get("event_code") or data.get("code")
    status = normalize_status(raw_status)
    if not event_id:
        event_id = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
    return {
        "provider_event_id": event_id,
        "tracking_number": tracking,
        "status": status,
        "event_code": str(raw_status or "unknown")[:100],
        "event_name": str(data.get("event_name") or data.get("status_name") or raw_status or "Actualización")[:150],
        "description": str(data.get("description") or data.get("message") or "")[:2000],
        "location": str(data.get("location") or data.get("city") or "")[:250],
        "occurred_at": _occurred_at(data.get("occurred_at") or data.get("timestamp") or data.get("date")),
        "raw_data": payload,
    }


def find_shipment(*, integration, tracking_number):
    if not tracking_number:
        return None, None
    package = (
        ShipmentPackage.objects.select_related("carrier_shipment")
        .filter(company=integration.company, tracking_number__iexact=tracking_number)
        .first()
    )
    if package:
        shipment = package.carrier_shipment
    else:
        shipment = CarrierShipment.objects.filter(
            company=integration.company,
            master_tracking_number__iexact=tracking_number,
        ).first()
    if shipment is None or shipment.provider != integration.provider:
        return None, None
    return shipment, package


@transaction.atomic
def apply_event(*, integration, event, receipt=None):
    shipment, package = find_shipment(
        integration=integration,
        tracking_number=event["tracking_number"],
    )
    if shipment is None:
        if receipt:
            receipt.status = TrackingWebhookReceipt.STATUS_IGNORED
            receipt.error_code = "tracking_not_found"
            receipt.error_message = (
                "No existe una guía de esta empresa con ese rastreo."
            )
            receipt.processed_at = timezone.now()
            receipt.save(
                update_fields=[
                    "status",
                    "error_code",
                    "error_message",
                    "processed_at",
                ]
            )
        return None, False

    tracking_event, created = ShipmentTrackingEvent.objects.get_or_create(
        company=integration.company,
        carrier_shipment=shipment,
        provider_event_id=event["provider_event_id"],
        defaults={
            "package": package,
            "event_code": event["event_code"],
            "event_name": event["event_name"],
            "description": event["description"],
            "location": event["location"],
            "occurred_at": event["occurred_at"],
            "raw_data": event["raw_data"],
        },
    )

    status = event["status"]
    transition_blocked = False
    transition_error = ""

    if status:
        lifecycle_metadata = {
            "operation": "apply_tracking_event",
            "provider": integration.provider,
            "provider_event_id": event["provider_event_id"],
            "event_code": event["event_code"],
            "tracking_event_id": tracking_event.pk,
        }

        try:
            transition_shipment(
                entity_id=shipment.pk,
                company_id=integration.company_id,
                to_state=status,
                source=SOURCE_TRACKING_ENGINE,
                reason=(
                    "Actualización recibida desde el Tracking Engine."
                ),
                metadata=lifecycle_metadata,
            )

            if package:
                transition_package(
                    entity_id=package.pk,
                    company_id=integration.company_id,
                    to_state=status,
                    source=SOURCE_TRACKING_ENGINE,
                    reason=(
                        "Actualización de bulto recibida desde Tracking."
                    ),
                    metadata=lifecycle_metadata,
                )
        except InvalidLifecycleTransition as error:
            # Un webhook atrasado se conserva como evidencia, pero nunca
            # puede regresar un envío o bulto a un estado anterior.
            transition_blocked = True
            transition_error = str(error)[:2000]

        if (
            status == CarrierShipment.STATUS_DELIVERED
            and not transition_blocked
        ):
            close_operational_delivery(
                company_id=integration.company_id,
                shipment_id=shipment.pk,
                occurred_at=event["occurred_at"],
                provider_event_id=event["provider_event_id"],
                tracking_event_id=tracking_event.pk,
            )

    if receipt:
        receipt.carrier_shipment = shipment
        receipt.status = TrackingWebhookReceipt.STATUS_PROCESSED
        receipt.processed_at = timezone.now()

        update_fields = [
            "carrier_shipment",
            "status",
            "processed_at",
        ]
        if transition_blocked:
            receipt.error_code = "lifecycle_transition_blocked"
            receipt.error_message = transition_error
            update_fields.extend(["error_code", "error_message"])

        receipt.save(update_fields=update_fields)

    return tracking_event, created


def receive_webhook(*, integration, body, payload):
    event = normalize_event(payload)
    digest = hashlib.sha256(body).hexdigest()
    try:
        receipt = TrackingWebhookReceipt.objects.create(
            company=integration.company,
            integration=integration,
            provider=integration.provider,
            provider_event_id=event["provider_event_id"],
            payload_sha256=digest,
        )
    except IntegrityError:
        return TrackingWebhookReceipt.objects.get(
            integration=integration, provider_event_id=event["provider_event_id"]
        ), False
    apply_event(integration=integration, event=event, receipt=receipt)
    return receipt, True


def integration_for_shipment(shipment):
    quote = shipment.selected_quote
    integration = getattr(quote, "integration", None) if quote else None
    if integration and integration.company_id == shipment.company_id:
        return integration
    return CompanyShippingIntegration.objects.filter(
        company=shipment.company, provider=shipment.provider, is_active=True
    ).order_by("priority", "id").first()


def record_sync_failure(*, shipment, integration, user, code, message):
    return TrackingSyncAttempt.objects.create(
        company=shipment.company,
        carrier_shipment=shipment,
        integration=integration,
        requested_by=user,
        status=TrackingSyncAttempt.STATUS_FAILED,
        error_code=str(code or "tracking_sync_failed")[:100],
        error_message=str(message or "")[:2000],
        finished_at=timezone.now(),
    )


def synchronize_shipment(*, shipment, user=None):
    from shipping_hub.strategies.resolver import get_shipping_strategy
    from shipping_hub.strategies.base import ShippingProviderError

    integration = integration_for_shipment(shipment)
    if integration is None:
        raise ValueError("No existe una integración activa para rastrear esta guía.")
    attempt = TrackingSyncAttempt.objects.create(
        company=shipment.company,
        carrier_shipment=shipment,
        integration=integration,
        requested_by=user,
        status=TrackingSyncAttempt.STATUS_SKIPPED,
    )
    try:
        result = get_shipping_strategy(integration=integration).track(
            tracking_number=shipment.master_tracking_number
        )
        payloads = result.get("events", []) if isinstance(result, dict) else result
        created_count = 0
        for payload in payloads or []:
            event = normalize_event(payload)
            _, created = apply_event(integration=integration, event=event)
            created_count += int(created)
        attempt.status = TrackingSyncAttempt.STATUS_COMPLETED
        attempt.events_created = created_count
        attempt.finished_at = timezone.now()
        attempt.save(update_fields=["status", "events_created", "finished_at"])
        return attempt
    except (ShippingProviderError, ValueError) as error:
        attempt.status = TrackingSyncAttempt.STATUS_FAILED
        attempt.error_code = str(getattr(error, "error_code", "") or "tracking_sync_failed")[:100]
        attempt.error_message = str(error)[:2000]
        attempt.finished_at = timezone.now()
        attempt.save(update_fields=["status", "error_code", "error_message", "finished_at"])
        raise
