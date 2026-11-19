"""Indicadores cruzados, de solo lectura, para el Resumen del Dashboard CEO."""

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone


ZERO = Decimal("0.00")


def _money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _percent(numerator, denominator):
    if not denominator:
        return Decimal("0.0")
    return (
        Decimal(numerator) * Decimal("100") / Decimal(denominator)
    ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def build_ceo_executive_indicator_context(
    *,
    company,
    scoped_orders,
    supplier_balance_due_total,
    customer_balance_due_total,
    pending_pickings,
    pending_packings,
    pending_shippings,
    now=None,
):
    """Combina señales ya acotadas sin mutar datos ni representar caja bancaria.

    ``scoped_orders`` hereda el selector global de bodega de ``ceo_dashboard``.
    La defensa por empresa se reaplica aquí para impedir contaminación aunque un
    llamador entregue accidentalmente un QuerySet más amplio.
    """
    if company is None:
        raise ValueError("Los indicadores ejecutivos requieren una empresa activa.")

    instant = now or timezone.now()
    rolling_start = instant - timedelta(days=29)
    cohort = scoped_orders.filter(
        company_id=company.id,
        confirmed_at__gte=rolling_start,
        confirmed_at__lte=instant,
    ).exclude(status__in=("draft", "cancelled"))
    cohort_total = cohort.count()
    cohort_delivered = cohort.filter(status="delivered").count()
    cohort_in_process = max(cohort_total - cohort_delivered, 0)
    fulfillment_rate = _percent(cohort_delivered, cohort_total)

    stalled_counts = [
        max(int(pending_pickings or 0), 0),
        max(int(pending_packings or 0), 0),
        max(int(pending_shippings or 0), 0),
    ]
    stalled_total = sum(stalled_counts)
    supplier_commitment = max(_money(supplier_balance_due_total), ZERO)
    receivables = max(_money(customer_balance_due_total), ZERO)

    return {
        "executive_cohort_days": 30,
        "executive_cohort_total": cohort_total,
        "executive_cohort_delivered": cohort_delivered,
        "executive_cohort_in_process": cohort_in_process,
        "executive_fulfillment_rate": fulfillment_rate,
        "executive_fulfillment_counts": [
            cohort_delivered,
            cohort_in_process,
        ],
        "executive_stalled_total": stalled_total,
        "executive_stalled_counts": stalled_counts,
        "executive_capital_committed": supplier_commitment,
        "executive_customer_receivables": receivables,
        "executive_operating_position": receivables - supplier_commitment,
        "executive_capital_counts": [
            float(receivables),
            float(supplier_commitment),
        ],
    }
