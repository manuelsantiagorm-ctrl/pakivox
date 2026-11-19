from pathlib import Path

from django.test import SimpleTestCase


class TrackingOrShipmentIdResolutionSH87Tests(SimpleTestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        self.reconciliation = (
            root
            / "shipping_hub"
            / "services"
            / "purchase_reconciliation.py"
        ).read_text(encoding="utf-8")
        self.manual = (
            root
            / "shipping_hub"
            / "services"
            / "manual_recovery.py"
        ).read_text(encoding="utf-8")

    def test_direct_remote_id_is_attempted_first(self):
        self.assertIn(
            "payload = client.get_shipment(exact_provider_id)",
            self.reconciliation,
        )

    def test_only_http_404_falls_back_to_tracking(self):
        self.assertIn(
            "except SkydropxAPIError as error:",
            self.reconciliation,
        )
        self.assertIn(
            "if error.status_code != 404:",
            self.reconciliation,
        )
        self.assertIn(
            "tracking_hint=exact_provider_id",
            self.reconciliation,
        )

    def test_tracking_search_inspects_included_packages(self):
        self.assertIn(
            "package_trackings = {",
            self.reconciliation,
        )
        self.assertIn(
            "_skydropx_packages(detail)",
            self.reconciliation,
        )
        self.assertIn(
            "tracking_hint not in package_trackings",
            self.reconciliation,
        )

    def test_tracking_is_not_saved_as_provider_purchase_id(self):
        self.assertIn(
            "and outcome.provider_shipment_id",
            self.manual,
        )
        self.assertNotIn(
            "outcome.provider_shipment_id or provider_id",
            self.manual,
        )
        self.assertIn(
            '"submitted_reference": provider_id',
            self.manual,
        )

    def test_resolution_never_rebuys(self):
        self.assertNotIn(
            "create_shipment(",
            self.reconciliation,
        )
        self.assertNotIn(
            "create_shipment(",
            self.manual,
        )
