from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Prefetch
from django.utils import timezone

from shipping_hub.models import (
    CarrierShipment,
    DeliveryRetry,
    LogisticsIncident,
    ShipmentReturn,
    ShippingReconciliation,
)


ACTIVE_STATUSES = {
    CarrierShipment.STATUS_SHIPPED,
    CarrierShipment.STATUS_IN_TRANSIT,
    CarrierShipment.STATUS_OUT_FOR_DELIVERY,
}
PENDING_STATUSES = {
    CarrierShipment.STATUS_DRAFT,
    CarrierShipment.STATUS_GENERATING,
    CarrierShipment.STATUS_RECONCILING,
    CarrierShipment.STATUS_LABEL_READY,
    CarrierShipment.STATUS_PRINTED,
    CarrierShipment.STATUS_READY,
}
FINISHED_STATUSES = {
    CarrierShipment.STATUS_DELIVERED,
    CarrierShipment.STATUS_CANCELLED,
    CarrierShipment.STATUS_FAILED,
}


def dashboard_shipments(company, warehouse, start, end):
    """Única fuente autorizada para tablero y exportación."""
    return (
        CarrierShipment.objects.for_company(company)
        .filter(
            shipping_order__packing_order__picking_order__warehouse=warehouse,
            created_at__date__gte=start,
            created_at__date__lte=end,
        )
        .select_related("shipping_order", "selected_quote")
        .prefetch_related(
            "tracking_events",
            "delivery_evidences",
            Prefetch(
                "logistics_incidents",
                queryset=LogisticsIncident.objects.for_company(company),
            ),
            Prefetch("returns", queryset=ShipmentReturn.objects.for_company(company)),
            Prefetch(
                "delivery_retries",
                queryset=DeliveryRetry.objects.for_company(company),
            ),
            Prefetch(
                "reconciliations",
                queryset=ShippingReconciliation.objects.for_company(company),
            ),
        )
        .order_by("-created_at", "-pk")
    )


def _money(value):
    return Decimal(str(value)) if value is not None else Decimal("0.00")


def build_dashboard(shipments, *, now=None, stale_hours=48):
    now = now or timezone.now()
    rows = list(shipments)
    total = len(rows)
    delivered = [s for s in rows if s.status == CarrierShipment.STATUS_DELIVERED]
    active = [s for s in rows if s.status in ACTIVE_STATUSES]
    pending = [s for s in rows if s.status in PENDING_STATUSES]
    exceptions = [s for s in rows if s.status in {CarrierShipment.STATUS_EXCEPTION, CarrierShipment.STATUS_FAILED}]
    stale_limit = now - timedelta(hours=stale_hours)
    stale = [s for s in rows if s.status not in FINISHED_STATUSES and s.updated_at <= stale_limit]

    actual_cost = sum((_money(s.total_cost) for s in rows), Decimal("0.00"))
    estimated_cost = sum(
        (_money(s.purchase_quote_amount if s.purchase_quote_amount is not None else (s.selected_quote.amount if s.selected_quote_id else None)) for s in rows),
        Decimal("0.00"),
    )
    transit_hours = []
    on_time = 0
    carrier = defaultdict(lambda: {"shipments": 0, "delivered": 0, "incidents": 0, "cost": Decimal("0.00")})
    incident_count = return_count = retry_count = mismatch_count = 0

    for shipment in rows:
        events = list(shipment.tracking_events.all())
        evidence = list(shipment.delivery_evidences.all())
        incidents = list(shipment.logistics_incidents.all())
        returns = list(shipment.returns.all())
        retries = list(shipment.delivery_retries.all())
        reconciliations = list(shipment.reconciliations.all())
        incident_count += len(incidents)
        return_count += len(returns)
        retry_count += len(retries)
        mismatch_count += sum(1 for r in reconciliations if r.status == ShippingReconciliation.STATUS_MISMATCH)

        key = shipment.carrier_name.strip() or shipment.get_provider_display()
        bucket = carrier[key]
        bucket["shipments"] += 1
        bucket["delivered"] += int(shipment.status == CarrierShipment.STATUS_DELIVERED)
        bucket["incidents"] += len(incidents)
        bucket["cost"] += _money(shipment.total_cost)

        if shipment in delivered:
            delivered_at = evidence[0].recorded_at if evidence else None
            if delivered_at is None:
                delivered_events = [e for e in events if e.event_code == CarrierShipment.STATUS_DELIVERED]
                delivered_at = delivered_events[0].occurred_at if delivered_events else shipment.updated_at
            start_at = shipment.generated_at or shipment.created_at
            if delivered_at and start_at and delivered_at >= start_at:
                hours = (delivered_at - start_at).total_seconds() / 3600
                transit_hours.append(hours)
                estimate_days = shipment.selected_quote.estimated_delivery_days if shipment.selected_quote_id else None
                if estimate_days and delivered_at <= start_at + timedelta(days=estimate_days):
                    on_time += 1

    carrier_rows = []
    for name, data in carrier.items():
        data["name"] = name
        data["success_rate"] = round((data["delivered"] / data["shipments"] * 100), 1) if data["shipments"] else 0
        carrier_rows.append(data)
    carrier_rows.sort(key=lambda item: (-item["shipments"], item["name"].lower()))

    status_counts = defaultdict(int)
    for shipment in rows:
        status_counts[shipment.get_status_display()] += 1

    return {
        "shipments": rows,
        "total": total,
        "pending": len(pending),
        "active": len(active),
        "delivered": len(delivered),
        "exceptions": len(exceptions),
        "success_rate": round((len(delivered) / total * 100), 1) if total else 0,
        "on_time_rate": round((on_time / len(delivered) * 100), 1) if delivered else 0,
        "average_delivery_hours": round(sum(transit_hours) / len(transit_hours), 1) if transit_hours else 0,
        "estimated_cost": estimated_cost,
        "actual_cost": actual_cost,
        "cost_difference": actual_cost - estimated_cost,
        "incident_count": incident_count,
        "return_count": return_count,
        "retry_count": retry_count,
        "mismatch_count": mismatch_count,
        "stale_shipments": stale,
        "carrier_rows": carrier_rows,
        "chart_labels": list(status_counts.keys()),
        "chart_values": list(status_counts.values()),
    }
