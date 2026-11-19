from __future__ import annotations

import sentry_sdk

from dataclasses import dataclass
from datetime import timedelta
import time
import uuid

from django.db import transaction
from django.utils import timezone

from shipping_hub.models import (
    CarrierShipment,
    ShippingPurchase,
    ShippingPurchaseAttempt,
    ShippingPurchaseEvent,
)
from shipping_hub.strategies.purchase_adapter_v2 import get_purchase_adapter_v2
from shipping_hub.strategies.contracts import ProviderPurchaseResult
from shipping_hub.services.lifecycle_service import (
    SOURCE_RECOVERY_ENGINE,
    SOURCE_SYSTEM,
    transition_purchase,
    transition_shipment,
)


# SkydropX PRO puede responder HTTP 202 con paid + in_progress
# mientras el carrier termina de generar tracking y etiqueta.
# Recovery consulta únicamente la MISMA operación remota.
RECOVERY_DELAYS_SECONDS = (
    10,
    20,
    40,
    60,
    120,
    120,
    120,
    120,
    120,
    120,
)

MAX_AUTOMATIC_RECOVERY_ATTEMPTS = len(
    RECOVERY_DELAYS_SECONDS
)
ASYNC_RECOVERY_WINDOW_SECONDS = 15 * 60
RECOVERY_LEASE_SECONDS = 120


@dataclass(frozen=True)
class RecoveryRunResult:
    purchase_id: int
    processed: bool
    completed: bool
    pending: bool
    manual_review: bool
    message: str


def _next_retry_at(*, attempt_count: int):
    index = max(
        0,
        min(
            attempt_count - 1,
            len(RECOVERY_DELAYS_SECONDS) - 1,
        ),
    )
    return timezone.now() + timedelta(
        seconds=RECOVERY_DELAYS_SECONDS[index]
    )


def _async_recovery_window_expired(*, purchase, now) -> bool:
    requested_at = getattr(purchase, "requested_at", None)
    if requested_at is None:
        return False
    return (
        now - requested_at
    ).total_seconds() >= ASYNC_RECOVERY_WINDOW_SECONDS


def recovery_candidate_queryset(*, now=None):
    now = now or timezone.now()
    return (
        ShippingPurchase.objects.filter(
            status__in=[
                ShippingPurchase.STATUS_PROVIDER_PENDING,
                ShippingPurchase.STATUS_RECONCILING,
            ],
            next_retry_at__lte=now,
        )
        .filter(
            lease_expires_at__isnull=True
        )
        .order_by("next_retry_at", "requested_at", "pk")
    )


def release_expired_recovery_leases(*, purchase_id=None, limit=100) -> int:
    """
    Libera leases vencidos del Recovery Engine.

    No cambia la operación a queued y jamás reenvía una compra.
    La mantiene en reconciliación para una nueva consulta segura.
    """
    now = timezone.now()
    queryset = ShippingPurchase.objects.filter(
        status__in=[
            ShippingPurchase.STATUS_PROVIDER_PENDING,
            ShippingPurchase.STATUS_RECONCILING,
        ],
        lease_expires_at__isnull=False,
        lease_expires_at__lte=now,
    ).order_by("lease_expires_at", "pk")
    if purchase_id:
        queryset = queryset.filter(pk=purchase_id)

    ids = list(queryset.values_list("pk", flat=True)[: max(1, min(limit, 1000))])
    if not ids:
        return 0

    with transaction.atomic():
        locked = list(
            ShippingPurchase.objects.select_for_update()
            .filter(pk__in=ids)
            .order_by("pk")
        )
        changed = 0
        for purchase in locked:
            if (
                purchase.status
                not in {
                    ShippingPurchase.STATUS_PROVIDER_PENDING,
                    ShippingPurchase.STATUS_RECONCILING,
                }
                or not purchase.lease_expires_at
                or purchase.lease_expires_at > now
            ):
                continue
            previous_status = purchase.status
            transition_purchase(
                entity_id=purchase.pk,
                company_id=purchase.company_id,
                to_state=ShippingPurchase.STATUS_RECONCILING,
                source=SOURCE_SYSTEM,
                reason=(
                    "Lease vencido del Recovery Engine. "
                    "La compra permanece en conciliación segura."
                ),
                actor=purchase.requested_by,
                metadata={
                    "operation": "release_expired_recovery_lease",
                    "provider": purchase.provider,
                },
            )
            purchase.lease_expires_at = None
            purchase.next_retry_at = now
            purchase.last_error_code = "recovery_lease_expired"
            purchase.last_error_message = (
                "Un conciliador se interrumpió. La compra no se repetirá; "
                "se realizará otra consulta segura al proveedor."
            )
            purchase.save(
                update_fields=[
                    "lease_expires_at",
                    "next_retry_at",
                    "last_error_code",
                    "last_error_message",
                    "updated_at",
                ]
            )
            ShippingPurchaseEvent.objects.create(
                company_id=purchase.company_id,
                purchase=purchase,
                event_type="recovery_lease_expired",
                from_status=previous_status,
                to_status=ShippingPurchase.STATUS_RECONCILING,
                message=purchase.last_error_message,
                metadata={"safe_action": "reconcile_only"},
            )
            changed += 1
    return changed


def claim_recovery_purchase(purchase_id: int):
    now = timezone.now()
    lease_until = now + timedelta(seconds=RECOVERY_LEASE_SECONDS)

    with transaction.atomic():
        purchase = (
            ShippingPurchase.objects.select_for_update()
            .select_related(
                "company",
                "integration",
                "carrier_shipment",
                "selected_quote",
                "warehouse",
            )
            .filter(pk=purchase_id)
            .first()
        )
        if purchase is None:
            return None
        if purchase.status not in {
            ShippingPurchase.STATUS_PROVIDER_PENDING,
            ShippingPurchase.STATUS_RECONCILING,
        }:
            return None
        if purchase.next_retry_at and purchase.next_retry_at > now:
            return None
        if purchase.lease_expires_at and purchase.lease_expires_at > now:
            return None

        previous_status = purchase.status
        transition_purchase(
            entity_id=purchase.pk,
            company_id=purchase.company_id,
            to_state=ShippingPurchase.STATUS_RECONCILING,
            source=SOURCE_RECOVERY_ENGINE,
            reason="WMS360 inició una consulta segura al proveedor.",
            actor=purchase.requested_by,
            metadata={
                "operation": "claim_recovery_purchase",
                "provider": purchase.provider,
                "next_attempt": purchase.attempt_count + 1,
            },
        )
        purchase.lease_expires_at = lease_until
        purchase.attempt_count += 1
        purchase.save(
            update_fields=[
                "lease_expires_at",
                "attempt_count",
                "updated_at",
            ]
        )
        ShippingPurchaseEvent.objects.create(
            company_id=purchase.company_id,
            purchase=purchase,
            event_type="recovery_started",
            from_status=previous_status,
            to_status=ShippingPurchase.STATUS_RECONCILING,
            message="WMS360 inició una consulta segura al proveedor.",
            metadata={
                "attempt_count": purchase.attempt_count,
                "lease_expires_at": lease_until.isoformat(),
                "recovery_correlation_id": str(uuid.uuid4()),
            },
        )
        return purchase


def _persist_recovery_result(
    *,
    purchase_id: int,
    result: ProviderPurchaseResult,
    started_monotonic: float,
) -> RecoveryRunResult:
    now = timezone.now()
    with transaction.atomic():
        purchase = (
            ShippingPurchase.objects.select_for_update()
            .select_related("carrier_shipment")
            .get(pk=purchase_id)
        )
        shipment = CarrierShipment.objects.select_for_update().get(
            pk=purchase.carrier_shipment_id,
            company_id=purchase.company_id,
        )

        previous_status = purchase.status
        purchase.provider_request_id = (
            result.provider_request_id or purchase.provider_request_id
        )
        purchase.provider_purchase_id = (
            result.provider_purchase_id or purchase.provider_purchase_id
        )
        purchase.provider_reference = (
            result.provider_reference or purchase.provider_reference
        )
        if result.charged_amount is not None:
            purchase.charged_amount = result.charged_amount
        if result.currency:
            purchase.currency = result.currency

        duration_ms = max(0, int((time.monotonic() - started_monotonic) * 1000))

        if result.outcome == "completed":
            target_purchase_status = ShippingPurchase.STATUS_COMPLETED
            purchase.charge_status = ShippingPurchase.CHARGE_CONFIRMED
            purchase.completed_at = now
            purchase.next_retry_at = None
            purchase.lease_expires_at = None
            purchase.last_error_code = ""
            purchase.last_error_message = ""
            event_type = "recovery_completed"
            message = "La compra fue localizada y recuperada correctamente."
            attempt_outcome = ShippingPurchaseAttempt.OUTCOME_COMPLETED
            completed = True
            pending = False
            manual_review = False

        elif (
            _async_recovery_window_expired(
                purchase=purchase,
                now=now,
            )
            or purchase.attempt_count
            >= MAX_AUTOMATIC_RECOVERY_ATTEMPTS
            or result.retry_disposition == "manual_review"
        ):
            target_purchase_status = ShippingPurchase.STATUS_MANUAL_REVIEW
            purchase.charge_status = ShippingPurchase.CHARGE_POSSIBLE
            purchase.next_retry_at = None
            purchase.lease_expires_at = None
            purchase.last_error_code = (
                result.error_code or "recovery_attempts_exhausted"
            )
            purchase.last_error_message = (
                result.error_message
                or (
                    "SkydropX confirmó el pago, pero no entregó "
                    "tracking y etiqueta dentro de la ventana "
                    "automática de 15 minutos. La recompra "
                    "permanece bloqueada."
                )
            )
            event_type = "recovery_manual_review"
            message = purchase.last_error_message
            attempt_outcome = ShippingPurchaseAttempt.OUTCOME_UNCERTAIN
            completed = False
            pending = False
            manual_review = True

        elif result.outcome == "failed_final":
            # En recuperación, un fallo remoto no demuestra que no hubo cargo.
            # Por seguridad nunca habilita recompra automática.
            target_purchase_status = ShippingPurchase.STATUS_MANUAL_REVIEW
            purchase.charge_status = ShippingPurchase.CHARGE_POSSIBLE
            purchase.next_retry_at = None
            purchase.lease_expires_at = None
            purchase.last_error_code = result.error_code or "recovery_failed_final"
            purchase.last_error_message = (
                result.error_message
                or "El proveedor devolvió un resultado definitivo durante la conciliación."
            )
            event_type = "recovery_failed_manual_review"
            message = purchase.last_error_message
            attempt_outcome = ShippingPurchaseAttempt.OUTCOME_FAILED_FINAL
            completed = False
            pending = False
            manual_review = True

        else:
            target_purchase_status = ShippingPurchase.STATUS_RECONCILING
            purchase.charge_status = ShippingPurchase.CHARGE_POSSIBLE
            purchase.next_retry_at = _next_retry_at(
                attempt_count=purchase.attempt_count
            )
            purchase.lease_expires_at = None
            purchase.last_error_code = result.error_code or "provider_pending"
            purchase.last_error_message = (
                result.error_message
                or (
                    "SkydropX confirmó la operación y continúa "
                    "generando el tracking y la etiqueta. "
                    "WMS360 seguirá consultando la misma compra "
                    "sin realizar otro cargo."
                )
            )
            event_type = "recovery_pending"
            message = purchase.last_error_message
            attempt_outcome = (
                ShippingPurchaseAttempt.OUTCOME_PENDING
                if result.outcome == "pending"
                else ShippingPurchaseAttempt.OUTCOME_UNCERTAIN
            )
            completed = False
            pending = True
            manual_review = False

        transition_purchase(
            entity_id=purchase.pk,
            company_id=purchase.company_id,
            to_state=target_purchase_status,
            source=SOURCE_RECOVERY_ENGINE,
            reason=message,
            actor=purchase.requested_by,
            metadata={
                "operation": "persist_recovery_result",
                "provider": purchase.provider,
                "outcome": result.outcome,
                "retry_disposition": result.retry_disposition,
            },
        )

        purchase.save(
            update_fields=[
                "provider_request_id",
                "provider_purchase_id",
                "provider_reference",
                "charged_amount",
                "currency",
                "charge_status",
                "completed_at",
                "next_retry_at",
                "lease_expires_at",
                "last_error_code",
                "last_error_message",
                "updated_at",
            ]
        )

        ShippingPurchaseAttempt.objects.create(
            company_id=purchase.company_id,
            purchase=purchase,
            attempt_number=purchase.attempt_count,
            outcome=attempt_outcome,
            provider_request_id=result.provider_request_id,
            provider_purchase_id=result.provider_purchase_id,
            response_payload=result.raw_response,
            error_code=result.error_code,
            error_message=result.error_message,
            finished_at=now,
            duration_ms=duration_ms,
        )

        ShippingPurchaseEvent.objects.create(
            company_id=purchase.company_id,
            purchase=purchase,
            event_type=event_type,
            from_status=previous_status,
            to_status=target_purchase_status,
            message=message,
            metadata={
                "attempt_count": purchase.attempt_count,
                "retry_disposition": result.retry_disposition,
                "next_retry_at": (
                    purchase.next_retry_at.isoformat()
                    if purchase.next_retry_at
                    else None
                ),
            },
        )

        if target_purchase_status in {
            ShippingPurchase.STATUS_RECONCILING,
            ShippingPurchase.STATUS_MANUAL_REVIEW,
        }:
            transition_shipment(
                entity_id=shipment.pk,
                company_id=purchase.company_id,
                to_state=CarrierShipment.STATUS_RECONCILING,
                source=SOURCE_RECOVERY_ENGINE,
                reason=message,
                actor=purchase.requested_by,
                metadata={
                    "operation": "persist_recovery_result",
                    "purchase_id": purchase.pk,
                    "provider": purchase.provider,
                },
            )
            shipment.last_error_type = "provider"
            shipment.last_error_code = purchase.last_error_code
            shipment.last_error_message = purchase.last_error_message
            shipment.save(
                update_fields=[
                    "last_error_type",
                    "last_error_code",
                    "last_error_message",
                    "updated_at",
                ]
            )

    return RecoveryRunResult(
        purchase_id=purchase_id,
        processed=True,
        completed=completed,
        pending=pending,
        manual_review=manual_review,
        message=message,
    )


def recover_shipping_purchase(purchase_id: int) -> RecoveryRunResult:
    purchase = claim_recovery_purchase(purchase_id)
    if purchase is None:
        return RecoveryRunResult(
            purchase_id=purchase_id,
            processed=False,
            completed=False,
            pending=False,
            manual_review=False,
            message="La compra no estaba disponible para conciliación.",
        )

    started = time.monotonic()
    try:
        adapter = get_purchase_adapter_v2(
            integration=purchase.integration,
            company=purchase.company,
        )
        if purchase.provider_purchase_id:
            result = adapter.get_purchase(purchase=purchase)
        else:
            result = adapter.find_purchase(purchase=purchase)
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        result = ProviderPurchaseResult(
            outcome="uncertain",
            retry_disposition="reconcile_only",
            error_code="recovery_unexpected_error",
            error_message=(
                "La consulta de conciliación terminó de forma incierta. "
                "WMS360 no repetirá la compra."
            ),
            raw_response={"exception_type": type(exc).__name__},
        )

    return _persist_recovery_result(
        purchase_id=purchase.pk,
        result=result,
        started_monotonic=started,
    )
