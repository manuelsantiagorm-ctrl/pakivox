from pathlib import Path

from django.test import SimpleTestCase


class GuideProcessDockSH91Tests(SimpleTestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        self.views = (root / "shipping_hub" / "views.py").read_text(encoding="utf-8")
        self.template = (root / "templates" / "shipping_hub" / "cubication.html").read_text(encoding="utf-8")

    def test_reconciling_is_not_terminal(self):
        terminal_block = self.views.split("terminal_states = {", 1)[1].split("}", 1)[0]
        self.assertNotIn("STATUS_RECONCILING", terminal_block)

    def test_polling_continues_during_reconciling(self):
        active = self.template.split("const activeStates = new Set([", 1)[1].split("]);", 1)[0]
        self.assertIn('"reconciling"', active)

    def test_three_state_professional_dock_exists(self):
        self.assertIn('id="guideProcessDock"', self.template)
        self.assertIn("Comprando guía", self.template)
        self.assertIn("Cargando y procesando guía", self.template)
        self.assertIn("Guía generada correctamente", self.template)
        self.assertIn("Descargar / imprimir guía", self.template)

    def test_completed_state_updates_without_reload(self):
        self.assertIn('"wms360:shipping-purchase-status"', self.template)
        self.assertIn("showReady(data)", self.template)
        self.assertNotIn("stopped = true;\n          window.location.reload();", self.template)

    def test_redirect_starts_only_after_pdf_click(self):
        self.assertIn('pdf.addEventListener("click", startRedirectAfterPdf)', self.template)
        self.assertIn("}, 8000);", self.template)
        self.assertIn("PDF abierto. Centro de Despacho en", self.template)

    def test_manual_resolution_is_not_normal_completed_flow(self):
        self.assertIn("show_manual_guide_resolution", self.views)
        block = self.views.split('"show_manual_guide_resolution": bool(', 1)[1].split('),\n            "manual_resolution_confirmation"', 1)[0]
        self.assertNotIn("STATUS_COMPLETED", block)
