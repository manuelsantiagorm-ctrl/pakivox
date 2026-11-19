from pathlib import Path

from django.test import SimpleTestCase


class GuidePurchaseModalSH92Tests(SimpleTestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        self.template = (
            root / "templates" / "shipping_hub" / "cubication.html"
        ).read_text(encoding="utf-8")

    def test_large_overlay_modal_exists(self):
        self.assertIn('id="guideProcessOverlay"', self.template)
        self.assertIn('role="dialog"', self.template)
        self.assertIn("width: min(1180px", self.template)
        self.assertIn("background: rgba(2, 6, 23, .84)", self.template)

    def test_modal_has_three_process_steps(self):
        self.assertIn('id="guideProcessStepPurchase"', self.template)
        self.assertIn('id="guideProcessStepGenerate"', self.template)
        self.assertIn('id="guideProcessStepReady"', self.template)
        self.assertIn("Compra de guía", self.template)

    def test_no_horizontal_loading_bar_exists(self):
        self.assertNotIn('class="guide-process-progress"', self.template)
        self.assertNotIn("guideProgress", self.template)

    def test_modal_contains_shipping_information(self):
        self.assertIn("Información del envío", self.template)
        self.assertIn("{{ shipping_order.order.folio }}", self.template)
        self.assertIn("{{ origin_data.label", self.template)
        self.assertIn("{{ destination_data.label", self.template)

    def test_active_process_cannot_be_closed(self):
        self.assertIn("headerClose.disabled = true", self.template)
        self.assertIn("document.documentElement.style.overflow", self.template)

    def test_pdf_redirect_remains_eight_seconds(self):
        self.assertIn("let remaining = 8;", self.template)
        self.assertIn("}, 8000);", self.template)
        self.assertIn('pdf.addEventListener("click", startRedirectAfterPdf)', self.template)
