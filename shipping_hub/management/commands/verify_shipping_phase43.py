from types import SimpleNamespace

from django.core.management.base import BaseCommand

from shipping_hub.services.envia_recovery import (
    _find_candidate,
    _rows,
    _shipment_id,
    _tracking,
)


class Command(BaseCommand):
    help = "Verifica Fase 4.3 sin conectarse a Envia.com."

    def handle(self, *args, **options):
        payload = {
            "data": [
                {
                    "shipmentId": 1001,
                    "trackingNumber": "TRACK-ENVIA-1",
                    "carrier": "fedex",
                }
            ]
        }
        rows = _rows(payload)
        assert len(rows) == 1
        assert _shipment_id(rows[0]) == "1001"
        assert _tracking(rows[0]) == "TRACK-ENVIA-1"

        shipment = SimpleNamespace(
            external_shipment_id="1001",
            master_tracking_number="",
            carrier_code="fedex",
            carrier_name="FedEx",
        )
        purchase = SimpleNamespace(provider_purchase_id="1001")
        candidate = _find_candidate(
            rows,
            shipment=shipment,
            purchase=purchase,
        )
        assert candidate is rows[0]

        self.stdout.write(
            self.style.SUCCESS("FASE 4.3 ENVIA RECOVERY VERIFICADA: OK")
        )
        self.stdout.write(
            "No se llamó a Envia.com y no se creó ni compró ninguna guía."
        )
