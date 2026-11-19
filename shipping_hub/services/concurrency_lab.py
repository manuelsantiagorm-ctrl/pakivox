from __future__ import annotations

from dataclasses import asdict, dataclass
import inspect
from typing import Any

from django.db import connection
from django.db.models import Count
from django.utils import timezone

from shipping_hub.models import (
    ShippingLifecycleTransition,
    ShippingProviderEvent,
    ShippingPurchase,
)
from shipping_hub.services import (
    provider_event_engine,
    purchase_engine,
    recovery_engine,
)


@dataclass(frozen=True)
class ConcurrencyCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class ConcurrencyReport:
    backend: str
    supports_select_for_update: bool
    supports_skip_locked: bool
    checks: tuple[ConcurrencyCheck, ...]
    open_purchase_duplicates: int
    provider_event_duplicates: int
    active_purchase_leases: int
    active_event_leases: int

    @property
    def passed(self) -> bool:
        return (
            all(check.passed for check in self.checks)
            and self.open_purchase_duplicates == 0
            and self.provider_event_duplicates == 0
        )

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["passed"] = self.passed
        return data


def _source_contains(callable_object, *fragments: str) -> bool:
    source = inspect.getsource(callable_object)
    return all(fragment in source for fragment in fragments)


def _open_purchase_duplicate_groups() -> int:
    open_statuses = [
        ShippingPurchase.STATUS_CREATED,
        ShippingPurchase.STATUS_QUEUED,
        ShippingPurchase.STATUS_SENDING,
        ShippingPurchase.STATUS_PROVIDER_PENDING,
        ShippingPurchase.STATUS_RECONCILING,
        ShippingPurchase.STATUS_MANUAL_REVIEW,
    ]
    return (
        ShippingPurchase.objects.filter(status__in=open_statuses)
        .values("company_id", "carrier_shipment_id")
        .annotate(total=Count("pk"))
        .filter(total__gt=1)
        .count()
    )


def _provider_event_duplicate_groups() -> int:
    by_event_id = (
        ShippingProviderEvent.objects.values(
            "company_id",
            "provider",
            "environment",
            "provider_event_id",
        )
        .annotate(total=Count("pk"))
        .filter(total__gt=1)
        .count()
    )
    by_dedupe = (
        ShippingProviderEvent.objects.values(
            "company_id",
            "provider",
            "environment",
            "deduplication_key",
        )
        .annotate(total=Count("pk"))
        .filter(total__gt=1)
        .count()
    )
    return by_event_id + by_dedupe


def build_concurrency_report() -> ConcurrencyReport:
    now = timezone.now()
    features = connection.features

    checks = (
        ConcurrencyCheck(
            name="purchase_worker_row_lock",
            passed=_source_contains(
                purchase_engine.process_shipping_purchase,
                "select_for_update(skip_locked=True)",
                "STATUS_SENDING",
            ),
            detail=(
                "El worker de compras toma el registro con lock y "
                "skip_locked antes de enviarlo."
            ),
        ),
        ConcurrencyCheck(
            name="purchase_request_row_lock",
            passed=_source_contains(
                purchase_engine.request_shipping_purchase,
                "select_for_update()",
                "open_purchase",
            ),
            detail=(
                "La solicitud bloquea el envío y busca una compra abierta "
                "antes de crear otra."
            ),
        ),
        ConcurrencyCheck(
            name="purchase_unique_open_constraint",
            passed=any(
                constraint.name == "uniq_open_ship_purchase"
                for constraint in ShippingPurchase._meta.constraints
            ),
            detail=(
                "Existe constraint de base de datos para una sola compra "
                "abierta por empresa y envío."
            ),
        ),
        ConcurrencyCheck(
            name="purchase_generation_constraint",
            passed=any(
                constraint.name == "uniq_ship_purchase_generation"
                for constraint in ShippingPurchase._meta.constraints
            ),
            detail=(
                "Cada generación de compra es única por empresa y envío."
            ),
        ),
        ConcurrencyCheck(
            name="event_claim_row_lock",
            passed=_source_contains(
                provider_event_engine.claim_event,
                "select_for_update()",
                "STATUS_PROCESSING",
                "lease_expires_at",
            ),
            detail=(
                "El worker de eventos bloquea y valida estado/lease antes "
                "de tomar el evento."
            ),
        ),
        ConcurrencyCheck(
            name="event_deduplication_constraints",
            passed={
                constraint.name
                for constraint in ShippingProviderEvent._meta.constraints
            }.issuperset(
                {
                    "uniq_ship_provider_event",
                    "uniq_ship_provider_dedupe",
                }
            ),
            detail=(
                "Los eventos tienen unicidad por ID remoto y clave de "
                "deduplicación."
            ),
        ),
        ConcurrencyCheck(
            name="expired_purchase_lease_reconciliation",
            passed=_source_contains(
                purchase_engine.mark_expired_purchase_leases_for_reconciliation,
                "STATUS_RECONCILING",
                "lease_expires_at",
            ),
            detail=(
                "Una compra cuyo worker murió pasa a conciliación y no "
                "se recompra automáticamente."
            ),
        ),
        ConcurrencyCheck(
            name="expired_event_lease_retry",
            passed=_source_contains(
                provider_event_engine.release_expired_event_leases,
                "STATUS_RETRY",
                "lease_expires_at",
            ),
            detail=(
                "Un evento con lease vencido vuelve a retry de forma "
                "idempotente."
            ),
        ),
        ConcurrencyCheck(
            name="recovery_row_lock",
            passed=_source_contains(
                recovery_engine.claim_recovery_purchase,
                "select_for_update",
                "lease_expires_at",
            ),
            detail=(
                "Recovery bloquea la compra antes de tomar el lease."
            ),
        ),
        ConcurrencyCheck(
            name="lifecycle_audit_enabled",
            passed=ShippingLifecycleTransition._meta.get_field(
                "correlation_id"
            ) is not None,
            detail=(
                "Las transiciones concurrentes quedan auditadas y "
                "correlacionadas."
            ),
        ),
    )

    return ConcurrencyReport(
        backend=connection.vendor,
        supports_select_for_update=bool(
            features.has_select_for_update
        ),
        supports_skip_locked=bool(
            features.has_select_for_update_skip_locked
        ),
        checks=checks,
        open_purchase_duplicates=_open_purchase_duplicate_groups(),
        provider_event_duplicates=_provider_event_duplicate_groups(),
        active_purchase_leases=ShippingPurchase.objects.filter(
            status=ShippingPurchase.STATUS_SENDING,
            lease_expires_at__gt=now,
        ).count(),
        active_event_leases=ShippingProviderEvent.objects.filter(
            status=ShippingProviderEvent.STATUS_PROCESSING,
            lease_expires_at__gt=now,
        ).count(),
    )
