from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
from typing import Iterable
import uuid

from django.test import Client
from django.urls import reverse

from shipping_hub.services.tracking import webhook_secret


SCENARIOS = {
    "created",
    "in_transit",
    "out_for_delivery",
    "delivered",
    "exception",
    "cancelled",
    "return",
    "timeout",
    "unknown",
}


@dataclass(frozen=True)
class SimulatedWebhook:
    scenario: str
    event_id: str
    tracking_number: str
    provider_purchase_id: str
    body: bytes
    signature: str


def scenario_payload(
    *,
    scenario: str,
    sequence: int,
    run_id: str,
    provider: str,
) -> dict:
    scenario = str(scenario or "").strip().casefold()
    if scenario not in SCENARIOS:
        raise ValueError(f"Escenario no soportado: {scenario}")

    event_id = f"sim-{run_id}-{sequence:06d}-{scenario}"
    tracking = f"WMS360SIM{run_id[:8].upper()}{sequence:06d}"
    purchase_id = f"SIM-PURCHASE-{run_id[:12]}-{sequence:06d}"

    status_map = {
        "created": "created",
        "in_transit": "in_transit",
        "out_for_delivery": "out_for_delivery",
        "delivered": "delivered",
        "exception": "exception",
        "cancelled": "cancelled",
        "return": "return_in_transit",
        "timeout": "provider_timeout",
        "unknown": "unknown_simulation",
    }

    event_type_map = {
        "created": "label.created",
        "in_transit": "tracking.in_transit",
        "out_for_delivery": "tracking.out_for_delivery",
        "delivered": "shipment.delivered",
        "exception": "tracking.exception",
        "cancelled": "shipment.cancelled",
        "return": "shipment.return",
        "timeout": "provider.timeout",
        "unknown": "simulation.unknown",
    }

    return {
        "id": event_id,
        "type": event_type_map[scenario],
        "simulation": True,
        "simulation_run_id": run_id,
        "provider": provider,
        "data": {
            "event_id": event_id,
            "shipmentId": purchase_id,
            "trackingNumber": tracking,
            "status": status_map[scenario],
            "description": (
                "Evento sintético generado por WMS360. "
                "No corresponde a una guía real."
            ),
            "location": "WMS360 Simulation Lab",
        },
    }


def encode_and_sign(*, integration, payload: dict) -> SimulatedWebhook:
    body = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    secret = webhook_secret(integration)
    signature = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()

    data = payload.get("data") or {}
    return SimulatedWebhook(
        scenario=str(payload.get("type") or ""),
        event_id=str(payload.get("id") or ""),
        tracking_number=str(data.get("trackingNumber") or ""),
        provider_purchase_id=str(data.get("shipmentId") or ""),
        body=body,
        signature=signature,
    )


def webhook_url(*, integration) -> str:
    return reverse(
        "shipping_hub:provider_event_webhook",
        kwargs={
            "provider": integration.provider,
            "integration_id": integration.pk,
        },
    )


def send_simulated_webhook(
    *,
    integration,
    payload: dict,
    valid_signature: bool = True,
    client: Client | None = None,
):
    simulated = encode_and_sign(
        integration=integration,
        payload=payload,
    )
    signature = (
        simulated.signature
        if valid_signature
        else "0" * 64
    )
    client = client or Client()
    response = client.post(
        webhook_url(integration=integration),
        data=simulated.body,
        content_type="application/json",
        HTTP_HOST="localhost",
        HTTP_X_WMS360_SIGNATURE=signature,
        HTTP_X_WMS360_SIMULATION="1",
    )
    return response, simulated


def scenario_sequence(name: str) -> tuple[str, ...]:
    name = str(name or "").strip().casefold()
    sequences = {
        "happy_path": (
            "created",
            "in_transit",
            "out_for_delivery",
            "delivered",
        ),
        "exception_path": (
            "created",
            "in_transit",
            "exception",
        ),
        "return_path": (
            "created",
            "in_transit",
            "delivered",
            "return",
        ),
        "cancel_path": (
            "created",
            "cancelled",
        ),
        "out_of_order": (
            "delivered",
            "in_transit",
            "created",
        ),
        "timeout_path": (
            "timeout",
            "created",
        ),
        "unknown": ("unknown",),
    }
    if name in SCENARIOS:
        return (name,)
    if name not in sequences:
        raise ValueError(
            "Secuencia no soportada. Opciones: "
            + ", ".join(sorted((*SCENARIOS, *sequences.keys())))
        )
    return sequences[name]


def build_payloads(
    *,
    scenario: str,
    count: int,
    provider: str,
    run_id: str | None = None,
) -> list[dict]:
    if count < 1:
        raise ValueError("count debe ser positivo.")
    run_id = run_id or uuid.uuid4().hex
    sequence = scenario_sequence(scenario)
    payloads = []
    index = 0
    for _ in range(count):
        for scenario_name in sequence:
            index += 1
            payloads.append(
                scenario_payload(
                    scenario=scenario_name,
                    sequence=index,
                    run_id=run_id,
                    provider=provider,
                )
            )
    return payloads
