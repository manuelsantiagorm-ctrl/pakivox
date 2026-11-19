from __future__ import annotations

import json
import time
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.models import Count
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company
from inventory.models import ProductVariant, Warehouse
from shipping_hub.services.operations_center import collect_shipping_operations_metrics
from subscriptions.models import Subscription


ZERO = Decimal("0.00")
ACTIVE_MRR_STATUSES = {Subscription.STATUS_ACTIVE}


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return ZERO


def monthly_recurring_value(subscription: Subscription) -> Decimal:
    """
    Monthlyiza únicamente suscripciones contractualmente activas.
    No cuenta trial/past_due/suspended/cancelled/expired como MRR.
    """
    if subscription.status not in ACTIVE_MRR_STATUSES:
        return ZERO

    plan = subscription.plan
    price = _decimal(getattr(plan, "price", ZERO))

    if getattr(plan, "billing_period", "monthly") == "yearly":
        return (price / Decimal("12")).quantize(Decimal("0.01"))

    return price.quantize(Decimal("0.01"))


def collect_financial_snapshot() -> dict[str, Any]:
    subscriptions = list(
        Subscription.objects.filter(
            status__in=ACTIVE_MRR_STATUSES,
        ).select_related("plan")
    )
    mrr = sum(
        (monthly_recurring_value(item) for item in subscriptions),
        ZERO,
    ).quantize(Decimal("0.01"))

    return {
        "mrr_contractual": mrr,
        "arr_contractual": (mrr * Decimal("12")).quantize(Decimal("0.01")),
        "paid_subscriptions": len(subscriptions),
        # No hay fuente contable fiable todavía para estos tres indicadores.
        "shipping_margin_available": False,
        "setup_income_available": False,
        "ltv_cac_available": False,
    }


def collect_portfolio_snapshot(limit: int = 15) -> dict[str, Any]:
    companies = list(
        Company.objects.values("id", "name", "is_active", "created_at")
        .order_by("-created_at", "-id")
    )
    company_ids = [row["id"] for row in companies]

    warehouse_counts = {
        row["company_id"]: row["total"]
        for row in Warehouse.objects.filter(company_id__in=company_ids)
        .values("company_id")
        .annotate(total=Count("id"))
    }
    user_counts = {
        row["company_id"]: row["total"]
        for row in UserProfile.objects.filter(company_id__in=company_ids)
        .values("company_id")
        .annotate(total=Count("id"))
    }
    sku_counts = {
        row["company_id"]: row["total"]
        for row in ProductVariant.objects.filter(company_id__in=company_ids)
        .values("company_id")
        .annotate(total=Count("id"))
    }

    subscriptions = {
        item.company_id: item
        for item in Subscription.objects.filter(company_id__in=company_ids)
        .select_related("plan")
    }

    rows = []
    for company in companies[:max(1, min(int(limit), 100))]:
        subscription = subscriptions.get(company["id"])
        rows.append(
            {
                "id": company["id"],
                "name": company["name"],
                "is_active": bool(company["is_active"]),
                "warehouses": int(warehouse_counts.get(company["id"], 0)),
                "users": int(user_counts.get(company["id"], 0)),
                "skus": int(sku_counts.get(company["id"], 0)),
                "plan_name": (
                    subscription.plan.name
                    if subscription and subscription.plan_id
                    else "Sin plan"
                ),
                "subscription_status": (
                    subscription.status if subscription else "none"
                ),
                # Fase posterior: necesita eventos de actividad normalizados.
                "health_available": False,
            }
        )

    subscription_status_counts = Counter(
        Subscription.objects.values_list("status", flat=True)
    )

    return {
        "companies_total": len(companies),
        "companies_active": sum(1 for row in companies if row["is_active"]),
        "companies_inactive": sum(1 for row in companies if not row["is_active"]),
        "warehouses_total": sum(warehouse_counts.values()),
        "tenant_users_total": sum(user_counts.values()),
        "all_users_total": get_user_model().objects.count(),
        "sku_variants_total": sum(sku_counts.values()),
        "subscription_status_counts": dict(subscription_status_counts),
        "rows": rows,
        "health_score_available": False,
    }


def collect_database_health() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            row = cursor.fetchone()

        return {
            "ok": bool(row and row[0] == 1),
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
            "vendor": connection.vendor,
            "error": "",
        }
    except Exception as exc:
        return {
            "ok": False,
            "latency_ms": None,
            "vendor": connection.vendor,
            # Nunca incluir DSN, credenciales o traceback.
            "error": exc.__class__.__name__,
        }


def collect_shipping_worker_health(now=None) -> dict[str, Any]:
    now = now or timezone.now()
    heartbeat_path = (
        Path(settings.BASE_DIR)
        / "runtime"
        / "shipping_worker_heartbeat.json"
    )

    unavailable = {
        "online": False,
        "age_seconds": None,
        "processed": None,
        "recovered": None,
        "loops": None,
        "pid": None,
        "status": "Sin heartbeat",
    }

    try:
        payload = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        heartbeat_at = timezone.datetime.fromisoformat(str(payload["updated_at"]))
        if timezone.is_naive(heartbeat_at):
            heartbeat_at = timezone.make_aware(heartbeat_at)

        age_seconds = max(0, int((now - heartbeat_at).total_seconds()))
        online = age_seconds <= 15

        return {
            "online": online,
            "age_seconds": age_seconds,
            "processed": int(payload.get("processed", 0)),
            "recovered": int(payload.get("recovered", 0)),
            "loops": int(payload.get("loops", 0)),
            "pid": (
                int(payload["pid"])
                if payload.get("pid") is not None
                else None
            ),
            "status": "Online" if online else "Heartbeat atrasado",
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return unavailable


def collect_system_snapshot() -> dict[str, Any]:
    shipping = collect_shipping_operations_metrics()

    return {
        "generated_at": timezone.now(),
        "application": {"ok": True, "status": "Online"},
        "database": collect_database_health(),
        "shipping_worker": collect_shipping_worker_health(),
        "shipping_health": shipping["health"],
        "shipping_purchases": shipping["purchases"],
        "shipping_events": shipping["events"],
        # Debe instrumentarse a partir de intentos reales de API.
        "api_error_rate_available": False,
    }


def collect_owner_control_center_snapshot() -> dict[str, Any]:
    """
    Agregación GLOBAL y read-only exclusiva del Owner/Superuser.

    Es una excepción deliberada al scoping tenant normal porque esta pantalla
    administra el portafolio SaaS completo. No recibe company_id/warehouse_id
    desde el navegador y no ejecuta escrituras.
    """
    return {
        "financial": collect_financial_snapshot(),
        "portfolio": collect_portfolio_snapshot(),
        "system": collect_system_snapshot(),
        "capabilities": {
            "tenant_creation": True,
            "impersonation": False,
            "feature_flags": False,
            "global_audit": False,
            "shipping_manual_resolution": False,
        },
    }
