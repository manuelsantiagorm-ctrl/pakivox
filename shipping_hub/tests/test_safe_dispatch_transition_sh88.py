from pathlib import Path

from django.test import SimpleTestCase


class SafeDispatchTransitionSH88Tests(SimpleTestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        self.views = (
            root / "shipping_hub" / "views.py"
        ).read_text(encoding="utf-8")
        self.template = (
            root
            / "templates"
            / "shipping_hub"
            / "cubication.html"
        ).read_text(encoding="utf-8")

    def test_page_requires_four_safe_conditions(self):
        self.assertIn(
            "ShippingPurchase.STATUS_COMPLETED",
            self.views,
        )
        self.assertIn(
            "CarrierShipment.STATUS_LABEL_READY",
            self.views,
        )
        self.assertIn("dispatch_tracking", self.views)
        self.assertIn("dispatch_label_url", self.views)

    def test_polling_uses_same_readiness_rule(self):
        self.assertIn(
            '"dispatch_ready": dispatch_ready',
            self.views,
        )
        self.assertIn('"dispatch_url": (', self.views)
        self.assertIn(
            '"carrier_shipment_status": (',
            self.views,
        )

    def test_panel_only_exists_inside_dispatch_ready(self):
        ready = self.template.index(
            "{% if dispatch_ready %}"
        )
        panel = self.template.index(
            'id="shippingDispatchTransition"'
        )
        end = self.template.index(
            "{% endif %}",
            panel,
        )
        self.assertLess(ready, panel)
        self.assertLess(panel, end)

    def test_print_and_dispatch_actions_exist(self):
        self.assertIn(
            "Abrir / imprimir etiqueta",
            self.template,
        )
        self.assertIn(
            "Ir al Centro de Despacho",
            self.template,
        )
        self.assertIn(
            'id="cancelDispatchRedirect"',
            self.template,
        )

    def test_browser_requires_all_three_values(self):
        self.assertIn(
            "if (!dispatchUrl || !labelUrl || !tracking)",
            self.template,
        )
        self.assertIn(
            "window.location.assign(dispatchUrl)",
            self.template,
        )

    def test_existing_live_frontend_remains_single(self):
        self.assertEqual(
            self.template.count(
                'id="shippingPurchaseLiveStatus"'
            ),
            1,
        )
        self.assertEqual(
            self.template.count(
                'id="shippingPurchaseBottomNotice"'
            ),
            1,
        )
        self.assertIn(
            "const NOTICE_DURATION_MS = 10000;",
            self.template,
        )
