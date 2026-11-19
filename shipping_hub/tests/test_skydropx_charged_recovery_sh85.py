from pathlib import Path

from django.test import SimpleTestCase


class SkydropxChargedGuideRecoverySH85Tests(SimpleTestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        self.strategy = (
            root
            / "shipping_hub"
            / "strategies"
            / "skydropx_strategy.py"
        ).read_text(encoding="utf-8")
        self.reconciliation = (
            root
            / "shipping_hub"
            / "services"
            / "purchase_reconciliation.py"
        ).read_text(encoding="utf-8")
        self.recovery = (
            root
            / "shipping_hub"
            / "services"
            / "skydropx_recovery.py"
        ).read_text(encoding="utf-8")

    def test_incomplete_response_preserves_remote_identity(self):
        self.assertIn(
            '"shipment_id": ",".join(shipment_ids)',
            self.strategy,
        )
        self.assertIn(
            '"raw_response": response',
            self.strategy,
        )
        self.assertNotIn(
            'raise SkydropxAPIError("Skydropx pudo crear o cobrar',
            self.strategy,
        )

    def test_recovery_uses_purchase_scoped_to_company_and_shipment(self):
        self.assertIn(
            "purchase_id=purchase.pk",
            self.recovery,
        )
        self.assertIn(
            "company_id=shipment.company_id",
            self.reconciliation,
        )
        self.assertIn(
            "carrier_shipment_id=shipment.pk",
            self.reconciliation,
        )

    def test_lookup_is_strict_and_never_arbitrary(self):
        self.assertIn(
            'score < 55 or len(set(signals)) < 2',
            self.reconciliation,
        )
        self.assertIn(
            "if len(best) != 1:",
            self.reconciliation,
        )
        self.assertIn(
            "return None, None",
            self.reconciliation,
        )

    def test_recovery_only_reads_provider(self):
        find_section = self.reconciliation[
            self.reconciliation.index("def _find_skydropx"):
            self.reconciliation.index(
                "def reconcile_carrier_shipment"
            )
        ]
        self.assertIn("client.get_shipment(", find_section)
        self.assertIn("client.list_shipments(", find_section)
        self.assertNotIn("create_shipment(", find_section)
