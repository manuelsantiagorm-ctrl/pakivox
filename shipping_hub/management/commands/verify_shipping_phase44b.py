import hashlib
import json

from django.core.management.base import BaseCommand

from shipping_hub.services.provider_event_engine import (
    normalize_provider_envelope,
    parse_json_body,
    sanitize_headers,
)


class Command(BaseCommand):
    help = "Verifica la recepción universal sin abrir endpoints ni llamar proveedores."

    def handle(self, *args, **options):
        body = json.dumps({
            "id": "evt-demo",
            "type": "tracking.updated",
            "data": {
                "shipmentId": "ship-demo",
                "trackingNumber": "TRACK-DEMO",
            },
        }).encode("utf-8")
        payload = parse_json_body(body)
        envelope = normalize_provider_envelope(
            provider="demo",
            payload=payload,
            body=body,
        )

        assert envelope["provider_event_id"] == "evt-demo"
        assert envelope["provider_purchase_id"] == "ship-demo"
        assert envelope["tracking_number"] == "TRACK-DEMO"
        assert len(envelope["deduplication_key"]) == 64
        assert envelope["payload_sha256"] == hashlib.sha256(body).hexdigest()

        headers = sanitize_headers({
            "Authorization": "secret",
            "X-Webhook-Signature": "signature",
            "Content-Type": "application/json",
        })
        assert "Authorization" not in headers
        assert headers["Content-Type"] == "application/json"

        self.stdout.write(
            self.style.SUCCESS(
                "FASE 4.4B RECEPCIÓN Y WORKER DE EVENTOS VERIFICADA: OK"
            )
        )
        self.stdout.write(
            "No se guardó ningún evento y no se llamó a proveedores."
        )
