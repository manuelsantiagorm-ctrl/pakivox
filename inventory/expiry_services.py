from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from inventory.models import LotBalance, LotExpiryAlert, ProductLot


EXPIRY_INFO_DAYS = 60
EXPIRY_NOTICE_DAYS = 30
EXPIRY_WARNING_DAYS = 15
EXPIRY_CRITICAL_DAYS = 7

SEVERITY_RANK = {
    LotExpiryAlert.SEVERITY_INFO: 1,
    LotExpiryAlert.SEVERITY_NOTICE: 2,
    LotExpiryAlert.SEVERITY_WARNING: 3,
    LotExpiryAlert.SEVERITY_CRITICAL: 4,
    LotExpiryAlert.SEVERITY_EXPIRED: 5,
}


def classify_expiry(expiration_date, *, today=None):
    today = today or timezone.localdate()

    if expiration_date is None:
        return {
            "severity": None,
            "label": "Sin caducidad",
            "days_to_expiry": None,
            "requires_alert": False,
            "is_expired": False,
        }

    days = (expiration_date - today).days

    if days < 0:
        severity = LotExpiryAlert.SEVERITY_EXPIRED
        label = "Vencido"
    elif days <= EXPIRY_CRITICAL_DAYS:
        severity = LotExpiryAlert.SEVERITY_CRITICAL
        label = "Crítico"
    elif days <= EXPIRY_WARNING_DAYS:
        severity = LotExpiryAlert.SEVERITY_WARNING
        label = "Advertencia"
    elif days <= EXPIRY_NOTICE_DAYS:
        severity = LotExpiryAlert.SEVERITY_NOTICE
        label = "Próximo a vencer"
    elif days <= EXPIRY_INFO_DAYS:
        severity = LotExpiryAlert.SEVERITY_INFO
        label = "En seguimiento"
    else:
        severity = None
        label = "Normal"

    return {
        "severity": severity,
        "label": label,
        "days_to_expiry": days,
        "requires_alert": severity is not None,
        "is_expired": days < 0,
    }


def _risk_message(*, lot_number, expiration_date, days_to_expiry, quantity, warehouse_name):
    if days_to_expiry < 0:
        timing = f"venció hace {abs(days_to_expiry)} día(s)"
    elif days_to_expiry == 0:
        timing = "vence hoy"
    else:
        timing = f"vence en {days_to_expiry} día(s)"

    return (
        f"Lote {lot_number} {timing} ({expiration_date:%d/%m/%Y}). "
        f"Cantidad física afectada en {warehouse_name}: {quantity}."
    )


def scan_expiry_risks(*, company=None, warehouse_ids=None, today=None):
    today = today or timezone.localdate()

    balances = (
        LotBalance.objects.filter(
            physical_quantity__gt=0,
            lot__expiration_date__isnull=False,
        )
        .exclude(status=LotBalance.STATUS_WASTE)
        .select_related(
            "company",
            "lot",
            "lot__variant",
            "lot__variant__product",
            "location",
            "location__warehouse",
        )
        .order_by(
            "company_id",
            "location__warehouse_id",
            "lot_id",
            "id",
        )
    )

    if company is not None:
        balances = balances.filter(company_id=company.id)

    if warehouse_ids is not None:
        warehouse_ids = {int(value) for value in warehouse_ids}
        if not warehouse_ids:
            return []
        balances = balances.filter(location__warehouse_id__in=sorted(warehouse_ids))

    grouped = {}

    for balance in balances.iterator():
        warehouse = balance.location.warehouse
        lot = balance.lot

        if warehouse.company_id != balance.company_id:
            continue
        if lot.company_id != balance.company_id:
            continue

        state = classify_expiry(lot.expiration_date, today=today)
        if not state["requires_alert"]:
            continue

        key = (balance.company_id, warehouse.id, lot.id)
        row = grouped.get(key)

        if row is None:
            row = {
                "company": balance.company,
                "warehouse": warehouse,
                "lot": lot,
                "severity": state["severity"],
                "label": state["label"],
                "expiration_date": lot.expiration_date,
                "days_to_expiry": state["days_to_expiry"],
                "affected_quantity": 0,
                "available_quantity": 0,
                "quarantine_quantity": 0,
                "blocked_quantity": 0,
                "is_expired": state["is_expired"],
            }
            grouped[key] = row

        quantity = int(balance.physical_quantity or 0)
        row["affected_quantity"] += quantity

        if balance.status == LotBalance.STATUS_AVAILABLE:
            row["available_quantity"] += quantity
        elif balance.status == LotBalance.STATUS_QUARANTINE:
            row["quarantine_quantity"] += quantity
        elif balance.status == LotBalance.STATUS_BLOCKED:
            row["blocked_quantity"] += quantity

    rows = list(grouped.values())

    for row in rows:
        row["message"] = _risk_message(
            lot_number=row["lot"].lot_number,
            expiration_date=row["expiration_date"],
            days_to_expiry=row["days_to_expiry"],
            quantity=row["affected_quantity"],
            warehouse_name=row["warehouse"].name,
        )

    rows.sort(
        key=lambda row: (
            row["days_to_expiry"],
            row["expiration_date"],
            row["warehouse"].name,
            row["lot"].lot_number,
        )
    )
    return rows


def get_expiry_summary(*, company=None, warehouse_ids=None, today=None):
    rows = scan_expiry_risks(
        company=company,
        warehouse_ids=warehouse_ids,
        today=today,
    )

    counts = {
        LotExpiryAlert.SEVERITY_EXPIRED: 0,
        LotExpiryAlert.SEVERITY_CRITICAL: 0,
        LotExpiryAlert.SEVERITY_WARNING: 0,
        LotExpiryAlert.SEVERITY_NOTICE: 0,
        LotExpiryAlert.SEVERITY_INFO: 0,
    }
    quantity = 0

    for row in rows:
        counts[row["severity"]] += 1
        quantity += int(row["affected_quantity"] or 0)

    return {
        "total": len(rows),
        "affected_quantity": quantity,
        "expired": counts[LotExpiryAlert.SEVERITY_EXPIRED],
        "critical": counts[LotExpiryAlert.SEVERITY_CRITICAL],
        "warning": counts[LotExpiryAlert.SEVERITY_WARNING],
        "notice": counts[LotExpiryAlert.SEVERITY_NOTICE],
        "info": counts[LotExpiryAlert.SEVERITY_INFO],
        "rows": rows,
    }


@transaction.atomic
def sync_lot_expiry_alerts(*, company=None, warehouse_ids=None, today=None, dry_run=False):
    today = today or timezone.localdate()
    risks = scan_expiry_risks(
        company=company,
        warehouse_ids=warehouse_ids,
        today=today,
    )

    risk_by_key = {
        (row["company"].id, row["warehouse"].id, row["lot"].id): row
        for row in risks
    }

    active = LotExpiryAlert.objects.select_for_update().filter(
        status=LotExpiryAlert.STATUS_ACTIVE,
    )

    if company is not None:
        active = active.filter(company_id=company.id)

    if warehouse_ids is not None:
        warehouse_ids = {int(value) for value in warehouse_ids}
        if not warehouse_ids:
            active = active.none()
        else:
            active = active.filter(warehouse_id__in=sorted(warehouse_ids))

    active_rows = {
        (alert.company_id, alert.warehouse_id, alert.lot_id): alert
        for alert in active.select_related("lot", "warehouse")
    }

    created = 0
    updated = 0
    resolved = 0

    if dry_run:
        return {
            "created": sum(1 for key in risk_by_key if key not in active_rows),
            "updated": sum(1 for key in risk_by_key if key in active_rows),
            "resolved": sum(1 for key in active_rows if key not in risk_by_key),
            "active": len(risk_by_key),
            "risks": risks,
        }

    for key, row in risk_by_key.items():
        alert = active_rows.get(key)

        if alert is None:
            try:
                with transaction.atomic():
                    alert = LotExpiryAlert.objects.create(
                        company=row["company"],
                        warehouse=row["warehouse"],
                        lot=row["lot"],
                        severity=row["severity"],
                        expiration_date=row["expiration_date"],
                        days_to_expiry=row["days_to_expiry"],
                        affected_quantity=row["affected_quantity"],
                        message=row["message"],
                    )
                created += 1
            except IntegrityError:
                alert = LotExpiryAlert.objects.select_for_update().get(
                    company=row["company"],
                    warehouse=row["warehouse"],
                    lot=row["lot"],
                    status=LotExpiryAlert.STATUS_ACTIVE,
                )
                active_rows[key] = alert

        if alert.pk:
            previous_rank = SEVERITY_RANK.get(alert.severity, 0)
            next_rank = SEVERITY_RANK.get(row["severity"], 0)
            escalated = next_rank > previous_rank

            changed = any(
                [
                    alert.severity != row["severity"],
                    alert.expiration_date != row["expiration_date"],
                    alert.days_to_expiry != row["days_to_expiry"],
                    alert.affected_quantity != row["affected_quantity"],
                    alert.message != row["message"],
                    alert.status != LotExpiryAlert.STATUS_ACTIVE,
                    alert.resolved_at is not None,
                ]
            )

            if changed or escalated:
                alert.severity = row["severity"]
                alert.status = LotExpiryAlert.STATUS_ACTIVE
                alert.expiration_date = row["expiration_date"]
                alert.days_to_expiry = row["days_to_expiry"]
                alert.affected_quantity = row["affected_quantity"]
                alert.message = row["message"]
                alert.resolved_at = None
                if escalated:
                    alert.is_read = False
                alert.full_clean()
                alert.save(
                    update_fields=[
                        "severity",
                        "status",
                        "expiration_date",
                        "days_to_expiry",
                        "affected_quantity",
                        "message",
                        "resolved_at",
                        "is_read",
                        "updated_at",
                    ]
                )
                if key in active_rows:
                    updated += 1

    now = timezone.now()

    for key, alert in active_rows.items():
        if key in risk_by_key:
            continue

        alert.status = LotExpiryAlert.STATUS_RESOLVED
        alert.resolved_at = now
        alert.is_read = True
        alert.save(
            update_fields=[
                "status",
                "resolved_at",
                "is_read",
                "updated_at",
            ]
        )
        resolved += 1

    return {
        "created": created,
        "updated": updated,
        "resolved": resolved,
        "active": len(risk_by_key),
        "risks": risks,
    }
