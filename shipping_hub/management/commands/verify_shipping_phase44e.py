from django.core.management.base import BaseCommand

from shipping_hub.services.synthetic_tracking_lab import (
    synthetic_tracking_payloads,
)


class Command(BaseCommand):
    help = "Verifica payloads sintéticos sin usar DB."

    def handle(self, *args, **options):
        payloads = synthetic_tracking_payloads(
            run_id="verification",
            tracking_number="WMS360SIMLINKVERIFY",
            provider_purchase_id="SIM-VERIFY",
        )
        assert len(payloads) == 4
        assert payloads[-1]["data"]["status"] == "delivered"
        assert len({
            payload["data"]["trackingNumber"]
            for payload in payloads
        }) == 1

        self.stdout.write(
            self.style.SUCCESS(
                "FASE 4.4E TRACKING SINTÉTICO VERIFICADO: OK"
            )
        )
        self.stdout.write(
            "No se usó base de datos, red ni proveedores."
        )
