from django.core.management.base import BaseCommand

from shipping_hub.services.provider_event_simulator import build_payloads


class Command(BaseCommand):
    help = "Verifica el simulador sin usar DB, red ni proveedores."

    def handle(self, *args, **options):
        payloads = build_payloads(
            scenario="happy_path",
            count=2,
            provider="skydropx",
            run_id="verification",
        )
        assert len(payloads) == 8
        assert len({payload["id"] for payload in payloads}) == 8
        assert all(payload["simulation"] for payload in payloads)
        assert all(
            payload["data"]["trackingNumber"].startswith("WMS360SIM")
            for payload in payloads
        )

        self.stdout.write(
            self.style.SUCCESS(
                "FASE 4.4D SIMULADOR END-TO-END VERIFICADO: OK"
            )
        )
        self.stdout.write(
            "No se usó la base de datos, no se llamó a proveedores y "
            "no se compró ninguna guía."
        )
