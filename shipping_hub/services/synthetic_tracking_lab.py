from __future__ import annotations

from dataclasses import dataclass
import uuid

from django.apps import apps
from django.db import transaction

from shipping_hub.models import (
    CarrierShipment,
    ShipmentTrackingEvent,
    ShippingProviderEvent,
)
from shipping_hub.services.provider_event_engine import process_provider_event
from shipping_hub.services.provider_event_simulator import send_simulated_webhook


ACTIVE_SHIPMENT_STATUSES = {
    CarrierShipment.STATUS_GENERATING,
    CarrierShipment.STATUS_RECONCILING,
    CarrierShipment.STATUS_LABEL_READY,
    CarrierShipment.STATUS_PRINTED,
    CarrierShipment.STATUS_READY,
    CarrierShipment.STATUS_SHIPPED,
    CarrierShipment.STATUS_IN_TRANSIT,
    CarrierShipment.STATUS_OUT_FOR_DELIVERY,
    CarrierShipment.STATUS_DELIVERED,
}


@dataclass(frozen=True)
class SyntheticTrackingResult:
    shipment_id: int
    tracking_number: str
    event_ids: tuple[int, ...]
    tracking_event_count: int
    final_status: str


def shipping_order_model():
    return apps.get_model("warehouse", "ShippingOrder")


def available_shipping_orders(*, integration, limit=20):
    ShippingOrder = shipping_order_model()
    return (
        ShippingOrder.objects.filter(company_id=integration.company_id)
        .exclude(
            carrier_shipments__status__in=ACTIVE_SHIPMENT_STATUSES,
        )
        .order_by("-pk")
        .distinct()[:limit]
    )


def _payload(
    *,
    run_id: str,
    sequence: int,
    tracking_number: str,
    provider_purchase_id: str,
    status: str,
    event_type: str,
):
    event_id = f"sim-track-{run_id}-{sequence:02d}-{status}"
    return {
        "id": event_id,
        "type": event_type,
        "simulation": True,
        "simulation_run_id": run_id,
        "data": {
            "event_id": event_id,
            "shipmentId": provider_purchase_id,
            "trackingNumber": tracking_number,
            "status": status,
            "description": (
                "Evento sintético enlazado a un envío sintético de WMS360."
            ),
            "location": "WMS360 Simulation Lab",
        },
    }


def synthetic_tracking_payloads(
    *,
    run_id: str,
    tracking_number: str,
    provider_purchase_id: str,
):
    definitions = (
        ("created", "label.created"),
        ("in_transit", "tracking.in_transit"),
        ("out_for_delivery", "tracking.out_for_delivery"),
        ("delivered", "shipment.delivered"),
    )
    return [
        _payload(
            run_id=run_id,
            sequence=index,
            tracking_number=tracking_number,
            provider_purchase_id=provider_purchase_id,
            status=status,
            event_type=event_type,
        )
        for index, (status, event_type) in enumerate(definitions, start=1)
    ]


@transaction.atomic
def create_synthetic_shipment(*, integration, shipping_order):
    if shipping_order.company_id != integration.company_id:
        raise ValueError("La orden pertenece a otra empresa.")

    has_active = CarrierShipment.objects.filter(
        company_id=integration.company_id,
        shipping_order=shipping_order,
        status__in=ACTIVE_SHIPMENT_STATUSES,
    ).exists()
    if has_active:
        raise ValueError(
            "La orden seleccionada ya tiene un envío activo."
        )

    run_id = uuid.uuid4().hex
    tracking_number = f"WMS360SIMLINK{run_id[:16].upper()}"
    provider_purchase_id = f"SIM-LINK-{run_id}"

    shipment = CarrierShipment.objects.create(
        company_id=integration.company_id,
        shipping_order=shipping_order,
        source=CarrierShipment.SOURCE_AUTOMATIC,
        provider=integration.provider,
        carrier_code="wms360-simulator",
        carrier_name="WMS360 Simulator",
        service_code="synthetic-tracking",
        service_name="Synthetic Tracking",
        external_shipment_id=provider_purchase_id,
        master_tracking_number=tracking_number,
        status=CarrierShipment.STATUS_LABEL_READY,
        request_data={
            "simulation": True,
            "simulation_run_id": run_id,
            "purpose": "event_engine_tracking_end_to_end",
        },
        response_data={},
    )
    return shipment, run_id


def run_synthetic_tracking_flow(*, integration, shipping_order):
    shipment, run_id = create_synthetic_shipment(
        integration=integration,
        shipping_order=shipping_order,
    )
    payloads = synthetic_tracking_payloads(
        run_id=run_id,
        tracking_number=shipment.master_tracking_number,
        provider_purchase_id=shipment.external_shipment_id,
    )

    event_ids = []
    for payload in payloads:
        response, _ = send_simulated_webhook(
            integration=integration,
            payload=payload,
            valid_signature=True,
        )
        if response.status_code not in {200, 202}:
            raise RuntimeError(
                f"Webhook sintético falló con HTTP {response.status_code}."
            )
        data = response.json()
        event_id = int(data["event_id"])
        event_ids.append(event_id)

    for event_id in event_ids:
        result = process_provider_event(event_id)
        if not result.processed:
            raise RuntimeError(
                f"El evento {event_id} no pudo procesarse."
            )

    shipment.refresh_from_db()
    tracking_event_count = ShipmentTrackingEvent.objects.filter(
        company_id=integration.company_id,
        carrier_shipment=shipment,
    ).count()

    return SyntheticTrackingResult(
        shipment_id=shipment.pk,
        tracking_number=shipment.master_tracking_number,
        event_ids=tuple(event_ids),
        tracking_event_count=tracking_event_count,
        final_status=shipment.status,
    )


@transaction.atomic
def cleanup_synthetic_tracking_flow(*, shipment_id, company_id):
    shipment = CarrierShipment.objects.select_for_update().get(
        pk=shipment_id,
        company_id=company_id,
        request_data__simulation=True,
        request_data__purpose="event_engine_tracking_end_to_end",
    )

    ShippingProviderEvent.objects.filter(
        company_id=company_id,
        carrier_shipment=shipment,
        payload__simulation=True,
    ).delete()

    ShipmentTrackingEvent.objects.filter(
        company_id=company_id,
        carrier_shipment=shipment,
    ).delete()

    tracking_number = shipment.master_tracking_number
    shipment.delete()
    return tracking_number
