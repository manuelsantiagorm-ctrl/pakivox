from pathlib import Path
from types import SimpleNamespace

from django.test import SimpleTestCase

from shipping_hub.services.purchase_ui import build_purchase_ui


class ShippingPurchaseProfessionalMessagesTests(SimpleTestCase):
    def shipment(self):
        return SimpleNamespace(
            status="generating",
            master_tracking_number="",
            get_status_display=lambda: "Generando guía",
        )

    def purchase(self, error, charge_status="not_charged"):
        return SimpleNamespace(
            pk=99,
            status="failed_final",
            charge_status=charge_status,
            last_error_message=error,
            get_status_display=lambda: "failed_final",
        )

    def test_insufficient_balance_is_professional(self):
        ui = build_purchase_ui(
            purchase=self.purchase("Créditos insuficientes."),
            carrier_shipment=self.shipment(),
        )
        self.assertEqual(ui["title"], "No se pudo generar la guía")
        self.assertEqual(ui["severity"], "warning")
        self.assertIn("Motivo:", ui["description"])
        self.assertIn("Acción recomendada:", ui["description"])
        self.assertIn("No se realizó ningún cargo.", ui["description"])
        self.assertTrue(ui["allow_retry"])
        self.assertFalse(ui["blocks_repurchase"])

    def test_uncertain_result_remains_protected(self):
        ui = build_purchase_ui(
            purchase=self.purchase(
                "Gateway timeout",
                charge_status="unknown",
            ),
            carrier_shipment=self.shipment(),
        )
        self.assertEqual(ui["severity"], "danger")
        self.assertTrue(ui["blocks_repurchase"])
        self.assertFalse(ui["allow_retry"])

    def test_notice_keeps_ten_seconds_and_progress(self):
        root = Path(__file__).resolve().parents[2]
        template = (
            root / "templates" / "shipping_hub" / "cubication.html"
        ).read_text(encoding="utf-8")
        self.assertIn("const NOTICE_DURATION_MS = 10000;", template)
        self.assertIn("shipping-purchase-notice-countdown", template)
        self.assertIn("white-space: pre-line;", template)
