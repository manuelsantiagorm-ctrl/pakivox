from types import SimpleNamespace

from django.test import SimpleTestCase

from shipping_hub.services.purchase_ui import build_purchase_ui


class PurchaseUIViewModelSH82Tests(SimpleTestCase):
    def shipment(
        self,
        status="generating",
        tracking="",
    ):
        return SimpleNamespace(
            status=status,
            master_tracking_number=tracking,
            get_status_display=lambda: "Generando guía",
        )

    def purchase(
        self,
        status,
        charge="unknown",
        error="",
        pk=8,
    ):
        return SimpleNamespace(
            pk=pk,
            status=status,
            charge_status=charge,
            last_error_message=error,
            get_status_display=lambda: status,
        )

    def test_queue(self):
        ui = build_purchase_ui(
            purchase=self.purchase("queued"),
            carrier_shipment=self.shipment(),
        )

        self.assertEqual(ui["code"], "queued")
        self.assertTrue(ui["blocks_repurchase"])
        self.assertFalse(ui["allow_retry"])

    def test_failed_not_charged(self):
        ui = build_purchase_ui(
            purchase=self.purchase(
                "failed_final",
                "not_charged",
                "Créditos insuficientes.",
            ),
            carrier_shipment=self.shipment(),
        )

        self.assertIn(
            "No se realizó ningún cargo.",
            ui["description"],
        )
        self.assertFalse(ui["blocks_repurchase"])
        self.assertTrue(ui["allow_retry"])

    def test_uncertain(self):
        for status in (
            "reconciling",
            "manual_review",
        ):
            ui = build_purchase_ui(
                purchase=self.purchase(status),
                carrier_shipment=self.shipment(),
            )

            self.assertTrue(ui["blocks_repurchase"])
            self.assertFalse(ui["allow_retry"])

    def test_completed(self):
        ui = build_purchase_ui(
            purchase=self.purchase("completed"),
            carrier_shipment=self.shipment(
                "label_ready",
                "TRACK-123",
            ),
        )

        self.assertTrue(ui["show_tracking"])
