from types import SimpleNamespace
from unittest.mock import patch

from django.core.management.base import BaseCommand

from shipping_hub.services.purchase_reconciliation import ReconciliationOutcome
from shipping_hub.services.skydropx_recovery import (
    skydropx_outcome_to_provider_result,
)


class _EmptyPackageQuery:
    def filter(self, **kwargs):
        return self

    def order_by(self, *args):
        return []


class Command(BaseCommand):
    help = "Verifica Fase 4.2 sin conectarse a SkydropX."

    def handle(self, *args, **options):
        fake_shipment = SimpleNamespace(
            pk=700001,
            company_id=400001,
            external_shipment_id="remote-demo",
            internal_reference="WMS-DEMO",
            total_cost="51.19",
            purchase_quote_amount="51.19",
            currency="MXN",
        )

        pending = skydropx_outcome_to_provider_result(
            outcome=ReconciliationOutcome(
                completed=False,
                pending=True,
                message="Aún no está lista.",
            ),
            shipment=fake_shipment,
        )
        assert pending.outcome == "pending"
        assert pending.retry_disposition == "reconcile_only"

        with patch(
            "shipping_hub.services.skydropx_recovery.ShipmentPackage.objects",
            _EmptyPackageQuery(),
        ):
            completed = skydropx_outcome_to_provider_result(
                outcome=ReconciliationOutcome(
                    completed=True,
                    pending=False,
                    message="Recuperada.",
                    tracking_number="TRACK-DEMO",
                    provider_shipment_id="remote-demo",
                ),
                shipment=fake_shipment,
            )

        assert completed.outcome == "completed"
        assert completed.tracking_numbers == ("TRACK-DEMO",)
        assert completed.provider_purchase_id == "remote-demo"
        assert str(completed.charged_amount) == "51.19"

        self.stdout.write(
            self.style.SUCCESS("FASE 4.2 SKYDROPX RECOVERY VERIFICADA: OK")
        )
        self.stdout.write(
            "No se llamó a SkydropX y no se creó ni compró ninguna guía."
        )
