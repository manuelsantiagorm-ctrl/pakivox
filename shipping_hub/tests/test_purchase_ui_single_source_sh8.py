from pathlib import Path
from django.test import SimpleTestCase

class PurchaseUISingleSourceSH8Tests(SimpleTestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        self.views = (root / "shipping_hub" / "views.py").read_text(encoding="utf-8")
        self.template = (root / "templates" / "shipping_hub" / "cubication.html").read_text(encoding="utf-8")
        self.presenter = (root / "shipping_hub" / "services" / "purchase_ui.py").read_text(encoding="utf-8")

    def test_state_logic_is_outside_template(self):
        self.assertIn("build_purchase_ui(", self.views)
        self.assertNotIn('current_purchase.status == "queued"', self.template)
        self.assertNotIn('current_purchase.status == "failed_final"', self.template)

    def test_both_sections_share_contract(self):
        self.assertEqual(self.template.count('data-purchase-ui-code="{{ purchase_ui.code }}"'), 2)

    def test_failed_not_charged_contract(self):
        self.assertIn('"failed_final"', self.presenter)
        self.assertIn('charge_status == "not_charged"', self.presenter)
        self.assertIn("No se realizó ningún cargo.", self.presenter)

    def test_uncertain_contract(self):
        self.assertIn('"reconciling"', self.presenter)
        self.assertIn('"manual_review"', self.presenter)
        self.assertIn("No vuelvas a comprar", self.presenter)
        self.assertIn("15 minutos", self.presenter)

    def test_completed_tracking_contract(self):
        self.assertIn('"completed"', self.presenter)
        self.assertIn('status == "completed" and bool(tracking)', self.presenter)
