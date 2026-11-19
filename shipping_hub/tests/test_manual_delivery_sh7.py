from pathlib import Path

from django.test import SimpleTestCase


class ManualDeliveryContractTests(SimpleTestCase):
    def _root(self):
        return Path(__file__).resolve().parents[1]

    def test_service_is_atomic_and_company_scoped(self):
        source = (
            self._root() / "services" / "dispatch_flow.py"
        ).read_text(encoding="utf-8-sig")
        self.assertIn(
            "def mark_shipment_delivered_manually(",
            source,
        )
        self.assertIn("@transaction.atomic", source)
        self.assertIn(
            ".get(pk=shipment_id, company_id=company_id)",
            source,
        )
        self.assertIn(
            'action="delivery_closed_manually"',
            source,
        )

    def test_view_requires_approval(self):
        source = (
            self._root() / "dispatch_views.py"
        ).read_text(encoding="utf-8-sig")
        self.assertIn("def mark_delivered(", source)
        self.assertIn(
            "required_level=ACCESS_APPROVE",
            source,
        )

    def test_route_and_button_exist(self):
        root = self._root()
        urls = (root / "dispatch_urls.py").read_text(
            encoding="utf-8-sig"
        )
        template = (
            root / "templates" / "shipping_hub"
            / "dispatch_center.html"
        ).read_text(encoding="utf-8-sig")
        self.assertIn('name="mark_delivered"', urls)
        self.assertIn("Marcar entregado", template)

    def test_bar_is_not_reintroduced(self):
        template = (
            self._root() / "templates" / "shipping_hub"
            / "dispatch_center.html"
        ).read_text(encoding="utf-8-sig")
        self.assertNotIn(
            'class="progress-track"',
            template,
        )
