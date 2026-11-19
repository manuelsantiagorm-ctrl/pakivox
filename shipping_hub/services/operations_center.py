from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any

from django.db.models import Count, Q
from django.utils import timezone

from shipping_hub.models import (
    ShippingProviderEvent,
    ShippingPurchase,
)


HEALTHY = "healthy"
WARNING = "warning"
CRITICAL = "critical"


@dataclass(frozen=True)
class OperationsThresholds:
    waiting_minutes: int = 5
    reconciling_minutes: int = 15
    event_minutes: int = 5
    possible_charge_minutes: int = 10


@dataclass(frozen=True)
class HealthFinding:
    severity: str
    code: str
    message: str
    count: int


def _status_counts(queryset, field="status") -> dict[str, int]:
    return {
        str(row[field]): int(row["total"])
        for row in queryset.values(field).annotate(total=Count("id")).order_by()
    }


def _grouped_purchase_metrics(queryset) -> list[dict[str, Any]]:
    rows = (
        queryset.values("company_id", "provider")
        .annotate(
            total=Count("id"),
            waiting=Count(
                "id",
                filter=Q(
                    status__in=[
                        ShippingPurchase.STATUS_CREATED,
                        ShippingPurchase.STATUS_QUEUED,
                    ]
                ),
            ),
            reconciling=Count(
                "id",
                filter=Q(
                    status__in=[
                        ShippingPurchase.STATUS_PROVIDER_PENDING,
                        ShippingPurchase.STATUS_RECONCILING,
                    ]
                ),
            ),
            completed=Count(
                "id",
                filter=Q(status=ShippingPurchase.STATUS_COMPLETED),
            ),
            failed_final=Count(
                "id",
                filter=Q(status=ShippingPurchase.STATUS_FAILED_FINAL),
            ),
            manual_review=Count(
                "id",
                filter=Q(status=ShippingPurchase.STATUS_MANUAL_REVIEW),
            ),
            possible_charge=Count(
                "id",
                filter=Q(
                    charge_status=ShippingPurchase.CHARGE_POSSIBLE,
                ),
            ),
        )
        .order_by("company_id", "provider")
    )
    return [
        {
            key: int(value) if key not in {"provider"} else value
            for key, value in row.items()
        }
        for row in rows
    ]


def _grouped_event_metrics(queryset) -> list[dict[str, Any]]:
    rows = (
        queryset.values("company_id", "provider")
        .annotate(
            total=Count("id"),
            received=Count(
                "id",
                filter=Q(status=ShippingProviderEvent.STATUS_RECEIVED),
            ),
            processing=Count(
                "id",
                filter=Q(status=ShippingProviderEvent.STATUS_PROCESSING),
            ),
            retry=Count(
                "id",
                filter=Q(status=ShippingProviderEvent.STATUS_RETRY),
            ),
            processed=Count(
                "id",
                filter=Q(status=ShippingProviderEvent.STATUS_PROCESSED),
            ),
            ignored=Count(
                "id",
                filter=Q(status=ShippingProviderEvent.STATUS_IGNORED),
            ),
            dead_letter=Count(
                "id",
                filter=Q(status=ShippingProviderEvent.STATUS_DEAD_LETTER),
            ),
        )
        .order_by("company_id", "provider")
    )
    return [
        {
            key: int(value) if key not in {"provider"} else value
            for key, value in row.items()
        }
        for row in rows
    ]


def evaluate_health(metrics: dict[str, Any]) -> tuple[str, list[HealthFinding]]:
    purchases = metrics["purchases"]
    events = metrics["events"]
    findings: list[HealthFinding] = []

    critical_rules = (
        (
            purchases["possible_charge_stale"],
            "possible_charge_stale",
            "Compras con posible cobro sin resolver.",
        ),
        (
            purchases["manual_review"],
            "purchase_manual_review",
            "Compras en revisión manual.",
        ),
        (
            events["dead_letter"],
            "event_dead_letter",
            "Eventos en dead letter.",
        ),
    )
    for count, code, message in critical_rules:
        if count:
            findings.append(
                HealthFinding(
                    severity=CRITICAL,
                    code=code,
                    message=message,
                    count=int(count),
                )
            )

    warning_rules = (
        (
            purchases["waiting_stale"],
            "purchase_waiting_stale",
            "Compras esperando más del límite.",
        ),
        (
            purchases["reconciling_stale"],
            "purchase_reconciling_stale",
            "Compras conciliando más del límite.",
        ),
        (
            purchases["expired_leases"],
            "purchase_expired_lease",
            "Compras con lease vencido.",
        ),
        (
            events["stale"],
            "event_stale",
            "Eventos pendientes más del límite.",
        ),
        (
            events["retry"],
            "event_retry",
            "Eventos programados para reintento.",
        ),
        (
            events["expired_leases"],
            "event_expired_lease",
            "Eventos con lease vencido.",
        ),
    )
    for count, code, message in warning_rules:
        if count:
            findings.append(
                HealthFinding(
                    severity=WARNING,
                    code=code,
                    message=message,
                    count=int(count),
                )
            )

    if any(item.severity == CRITICAL for item in findings):
        status = CRITICAL
    elif findings:
        status = WARNING
    else:
        status = HEALTHY
    return status, findings


def collect_shipping_operations_metrics(
    *,
    company_id: int | None = None,
    provider: str | None = None,
    thresholds: OperationsThresholds | None = None,
    now=None,
) -> dict[str, Any]:
    thresholds = thresholds or OperationsThresholds()
    now = now or timezone.now()

    purchases = ShippingPurchase.objects.all()
    events = ShippingProviderEvent.objects.all()

    if company_id is not None:
        purchases = purchases.filter(company_id=company_id)
        events = events.filter(company_id=company_id)
    if provider:
        purchases = purchases.filter(provider=provider)
        events = events.filter(provider=provider)

    waiting_cutoff = now - timedelta(minutes=thresholds.waiting_minutes)
    reconciling_cutoff = now - timedelta(
        minutes=thresholds.reconciling_minutes
    )
    event_cutoff = now - timedelta(minutes=thresholds.event_minutes)
    possible_charge_cutoff = now - timedelta(
        minutes=thresholds.possible_charge_minutes
    )

    waiting_statuses = [
        ShippingPurchase.STATUS_CREATED,
        ShippingPurchase.STATUS_QUEUED,
    ]
    reconciling_statuses = [
        ShippingPurchase.STATUS_PROVIDER_PENDING,
        ShippingPurchase.STATUS_RECONCILING,
    ]

    purchase_metrics = {
        "total": purchases.count(),
        "status_counts": _status_counts(purchases),
        "waiting": purchases.filter(status__in=waiting_statuses).count(),
        "waiting_stale": purchases.filter(
            status__in=waiting_statuses,
            updated_at__lt=waiting_cutoff,
        ).count(),
        "reconciling": purchases.filter(
            status__in=reconciling_statuses
        ).count(),
        "reconciling_stale": purchases.filter(
            status__in=reconciling_statuses,
            updated_at__lt=reconciling_cutoff,
        ).count(),
        "possible_charge": purchases.filter(
            charge_status=ShippingPurchase.CHARGE_POSSIBLE,
        ).count(),
        "possible_charge_stale": purchases.filter(
            charge_status=ShippingPurchase.CHARGE_POSSIBLE,
            updated_at__lt=possible_charge_cutoff,
        ).exclude(
            status__in=[
                ShippingPurchase.STATUS_COMPLETED,
                ShippingPurchase.STATUS_CANCELLED,
            ],
        ).count(),
        "manual_review": purchases.filter(
            status=ShippingPurchase.STATUS_MANUAL_REVIEW,
        ).count(),
        "completed": purchases.filter(
            status=ShippingPurchase.STATUS_COMPLETED,
        ).count(),
        "failed_final": purchases.filter(
            status=ShippingPurchase.STATUS_FAILED_FINAL,
        ).count(),
        "expired_leases": purchases.filter(
            lease_expires_at__isnull=False,
            lease_expires_at__lte=now,
            status__in=[
                ShippingPurchase.STATUS_SENDING,
                ShippingPurchase.STATUS_PROVIDER_PENDING,
                ShippingPurchase.STATUS_RECONCILING,
            ],
        ).count(),
        "by_company_provider": _grouped_purchase_metrics(purchases),
    }

    pending_event_statuses = [
        ShippingProviderEvent.STATUS_RECEIVED,
        ShippingProviderEvent.STATUS_RETRY,
        ShippingProviderEvent.STATUS_PROCESSING,
    ]
    event_metrics = {
        "total": events.count(),
        "status_counts": _status_counts(events),
        "pending": events.filter(
            status__in=pending_event_statuses,
        ).count(),
        "stale": events.filter(
            status__in=pending_event_statuses,
            updated_at__lt=event_cutoff,
        ).count(),
        "retry": events.filter(
            status=ShippingProviderEvent.STATUS_RETRY,
        ).count(),
        "dead_letter": events.filter(
            status=ShippingProviderEvent.STATUS_DEAD_LETTER,
        ).count(),
        "processed": events.filter(
            status=ShippingProviderEvent.STATUS_PROCESSED,
        ).count(),
        "ignored": events.filter(
            status=ShippingProviderEvent.STATUS_IGNORED,
        ).count(),
        "expired_leases": events.filter(
            status=ShippingProviderEvent.STATUS_PROCESSING,
            lease_expires_at__isnull=False,
            lease_expires_at__lte=now,
        ).count(),
        "by_company_provider": _grouped_event_metrics(events),
    }

    metrics: dict[str, Any] = {
        "generated_at": now.isoformat(),
        "filters": {
            "company_id": company_id,
            "provider": provider or "",
        },
        "thresholds_minutes": asdict(thresholds),
        "purchases": purchase_metrics,
        "events": event_metrics,
    }
    health, findings = evaluate_health(metrics)
    metrics["health"] = {
        "status": health,
        "findings": [asdict(item) for item in findings],
    }
    return metrics
