from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from django.utils import timezone

from shipping_hub.models import CarrierShipment


PROGRESS_STEPS = (
    ("label", "Guía"),
    ("shipped", "Enviado"),
    ("in_transit", "En tránsito"),
    ("out_for_delivery", "En reparto"),
    ("delivered", "Entregado"),
)

STATUS_PROGRESS = {
    CarrierShipment.STATUS_DRAFT: 0,
    CarrierShipment.STATUS_GENERATING: 0,
    CarrierShipment.STATUS_RECONCILING: 0,
    CarrierShipment.STATUS_LABEL_READY: 1,
    CarrierShipment.STATUS_PRINTED: 1,
    CarrierShipment.STATUS_READY: 1,
    CarrierShipment.STATUS_SHIPPED: 2,
    CarrierShipment.STATUS_IN_TRANSIT: 3,
    CarrierShipment.STATUS_OUT_FOR_DELIVERY: 4,
    CarrierShipment.STATUS_DELIVERED: 5,
    CarrierShipment.STATUS_EXCEPTION: 0,
    CarrierShipment.STATUS_CANCELLED: 0,
    CarrierShipment.STATUS_FAILED: 0,
}


@dataclass(frozen=True)
class TimelineItem:
    occurred_at: object
    title: str
    description: str
    location: str
    source: str
    state: str
    sort_key: tuple


def progress_for(shipment):
    completed = STATUS_PROGRESS.get(shipment.status, 0)
    steps = []
    for index, (code, label) in enumerate(PROGRESS_STEPS, start=1):
        if completed >= index:
            state = "complete"
        elif completed + 1 == index:
            state = "current"
        else:
            state = "pending"
        steps.append(
            {
                "code": code,
                "label": label,
                "state": state,
            }
        )
    return steps


def _tracking_items(shipment):
    items = []
    for event in shipment.tracking_events.all():
        items.append(
            TimelineItem(
                occurred_at=event.occurred_at,
                title=event.event_name or event.event_code,
                description=event.description or "",
                location=event.location or "",
                source="Proveedor",
                state=event.event_code or "",
                sort_key=(event.occurred_at, 30, event.pk),
            )
        )
    return items


def _lifecycle_items(shipment):
    labels = dict(CarrierShipment.STATUS_CHOICES)
    items = []
    for transition in shipment.lifecycle_transitions.all():
        title = labels.get(
            transition.to_state,
            transition.to_state.replace("_", " ").title(),
        )
        items.append(
            TimelineItem(
                occurred_at=transition.occurred_at,
                title=title,
                description=transition.reason or "",
                location="",
                source="WMS360",
                state=transition.to_state,
                sort_key=(transition.occurred_at, 20, transition.pk),
            )
        )
    return items


def _audit_items(shipment):
    titles = {
        "delivery_closed_automatically": "Pedido cerrado automáticamente",
        "shipment_dispatched": "Pedido marcado como enviado",
        "label_generated": "Guía generada",
    }
    items = []
    for audit in shipment.audit_events.all():
        detail = audit.detail or {}
        message = detail.get("message") or detail.get("reason") or ""
        title = titles.get(
            audit.action,
            audit.action.replace("_", " ").title(),
        )
        items.append(
            TimelineItem(
                occurred_at=audit.created_at,
                title=title,
                description=str(message),
                location=str(detail.get("location") or ""),
                source="Auditoría",
                state=audit.action,
                sort_key=(audit.created_at, 10, audit.pk),
            )
        )
    return items


def timeline_for(shipment):
    items = (
        _tracking_items(shipment)
        + _lifecycle_items(shipment)
        + _audit_items(shipment)
    )

    # El mismo cambio puede quedar registrado por tracking, lifecycle y
    # auditoría. Conservamos el registro más expresivo y eliminamos duplicados
    # cercanos para mantener un timeline legible.
    items.sort(key=lambda item: item.sort_key, reverse=True)
    result = []
    seen = set()
    for item in items:
        minute_key = None
        if item.occurred_at:
            minute_key = item.occurred_at.replace(second=0, microsecond=0)
        key = (item.state, minute_key, item.title)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def enrich_shipment(shipment):
    shipment.dispatch_progress = progress_for(shipment)
    shipment.latest_tracking_event = next(
        iter(shipment.tracking_events.all()),
        None,
    )

    events = list(shipment.tracking_events.all())
    started_at = None
    delivered_at = None
    for event in reversed(events):
        if event.event_code in {"shipped", "in_transit"} and started_at is None:
            started_at = event.occurred_at
        if event.event_code == "delivered":
            delivered_at = event.occurred_at

    if started_at:
        end = delivered_at or timezone.now()
        shipment.transit_duration = end - started_at
        shipment.transit_days = max(
            0,
            int((end - started_at).total_seconds() // 86400),
        )
    else:
        shipment.transit_duration = None
        shipment.transit_days = None

    shipment.primary_package = next(
        iter(shipment.packages.all()),
        None,
    )
    return shipment
