from pathlib import Path
from django.test import SimpleTestCase

class ShippingPurchaseLiveFrontendTests(SimpleTestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        self.views = (root / "shipping_hub" / "views.py").read_text(encoding="utf-8")
        self.urls = (root / "shipping_hub" / "urls.py").read_text(encoding="utf-8")
        self.template = (root / "templates" / "shipping_hub" / "cubication.html").read_text(encoding="utf-8")
        self.worker = (root / "shipping_hub" / "management" / "commands" / "run_shipping_purchase_worker.py").read_text(encoding="utf-8")

    def test_status_endpoint_is_company_scoped_and_read_only(self):
        start = self.views.index("def shipping_purchase_live_status")
        section = self.views[start:start + 7000]
        self.assertIn("ShippingPurchase.objects.for_company(company)", section)
        self.assertIn("ShipmentPackage.objects.for_company(company)", section)
        self.assertIn("@require_GET", self.views[start-100:start])
        self.assertNotIn("process_shipping_purchase(", section)

    def test_status_route_and_polling_exist(self):
        self.assertIn('name="shipping_purchase_live_status"', self.urls)
        self.assertIn('id="shippingPurchaseLiveStatus"', self.template)
        self.assertIn("window.setTimeout(poll, 1500)", self.template)
        self.assertIn("credentials: \"same-origin\"", self.template)

    def test_active_purchase_blocks_cubication_controls(self):
        self.assertIn('data-purchase-locked=', self.template)
        self.assertIn("lockCubication(", self.template)
        self.assertIn(".remove-package-button", self.template)

    def test_worker_writes_health_heartbeat(self):
        self.assertIn("shipping_worker_heartbeat.json", self.worker)
        self.assertIn("def _write_heartbeat", self.worker)
        self.assertIn("temporary_path.replace(heartbeat_path)", self.worker)
