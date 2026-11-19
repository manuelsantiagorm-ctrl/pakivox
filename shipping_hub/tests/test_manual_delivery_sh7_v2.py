from pathlib import Path

from django.test import SimpleTestCase


class ManualDeliveryV2Tests(SimpleTestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]

    def test_service_contract(self):
        source = (
            self.root / "services" / "dispatch_flow.py"
        ).read_text(encoding="utf-8-sig")
        self.assertIn(
            "def mark_shipment_delivered_manually(",
            source,
        )
        self.assertIn("@transaction.atomic", source)
        self.assertIn(
            'action="delivery_closed_manually"',
            source,
        )

    def test_view_and_route_contract(self):
        views = (
            self.root / "dispatch_views.py"
        ).read_text(encoding="utf-8-sig")
        urls = (
            self.root / "dispatch_urls.py"
        ).read_text(encoding="utf-8-sig")
        self.assertIn("def mark_delivered(", views)
        self.assertIn(
            "required_level=ACCESS_APPROVE",
            views,
        )
        self.assertIn('name="mark_delivered"', urls)
