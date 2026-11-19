from pathlib import Path
from django.test import SimpleTestCase

class ShippingPurchaseWorkerSH4Tests(SimpleTestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        self.views = (root / "shipping_hub" / "views.py").read_text(encoding="utf-8")
        self.template = (root / "templates" / "shipping_hub" / "cubication.html").read_text(encoding="utf-8")
        self.worker = (root / "shipping_hub" / "management" / "commands" / "run_shipping_purchase_worker.py").read_text(encoding="utf-8")
        self.presenter = (root / "shipping_hub" / "services" / "purchase_ui.py").read_text(encoding="utf-8")

    def test_http_request_only_queues_purchase(self):
        section = self.views[self.views.index("def shipping_generate_label"):][:5000]
        self.assertIn("request_shipping_purchase(", section)
        self.assertNotIn("process_shipping_purchase(", section)

    def test_company_scoped_current_purchase_and_presenter(self):
        self.assertIn("ShippingPurchase.objects.for_company(company)", self.views)
        self.assertIn('"current_purchase": current_purchase', self.views)
        self.assertIn('"purchase_ui": purchase_ui', self.views)
        self.assertIn("build_purchase_ui(", self.views)

    def test_ui_uses_single_presenter_for_page_and_live_notice(self):
        self.assertEqual(
            self.template.count(
                'data-purchase-ui-code="{{ purchase_ui.code }}"'
            ),
            2,
        )
        self.assertEqual(
            self.template.count("{{ purchase_ui.title }}"),
            1,
        )
        self.assertIn(
            'data-initial-title="{{ purchase_ui.title|escape }}"',
            self.template,
        )
        self.assertIn(
            'data-initial-description="{{ purchase_ui.description|escape }}"',
            self.template,
        )

    def test_presenter_covers_safety_states(self):
        for state in ("queued", "sending", "provider_pending", "reconciling", "manual_review", "failed_final", "completed"):
            self.assertIn(f'"{state}"', self.presenter)
        self.assertIn('charge_status == "not_charged"', self.presenter)
        self.assertIn("blocks_repurchase", self.presenter)
        self.assertIn("allow_retry", self.presenter)

    def test_worker_uses_engine_and_lease_recovery(self):
        self.assertIn("process_shipping_purchase(", self.worker)
        self.assertIn("mark_expired_purchase_leases_for_reconciliation(", self.worker)
        self.assertIn("ShippingPurchase.STATUS_QUEUED", self.worker)

    def test_worker_does_not_expose_credentials(self):
        lowered = self.worker.lower()
        for token in ("authorization:", "api_key=", "access_token=", "secret="):
            self.assertNotIn(token, lowered)
