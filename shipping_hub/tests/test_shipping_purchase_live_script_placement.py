from pathlib import Path

from django.test import SimpleTestCase


class ShippingPurchaseLiveScriptPlacementTests(
    SimpleTestCase
):
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        self.template = (
            root
            / "templates"
            / "shipping_hub"
            / "cubication.html"
        ).read_text(encoding="utf-8")

    def test_title_block_contains_no_script(self):
        title_start = self.template.index(
            "{% block title %}"
        )
        title_end = self.template.index(
            "{% endblock %}",
            title_start,
        )
        title_section = self.template[
            title_start:title_end
        ]
        self.assertIn(
            "Mesa de Cubicación | PAKIVOX",
            title_section,
        )
        self.assertNotIn("<script", title_section)

    def test_live_script_is_after_live_status_element(
        self,
    ):
        element_index = self.template.index(
            'id="shippingPurchaseLiveStatus"'
        )
        poll_index = self.template.index(
            "const liveStatus = "
            "document.getElementById("
        )
        self.assertGreater(
            poll_index,
            element_index,
        )

    def test_live_script_remains_inside_content_block(
        self,
    ):
        poll_index = self.template.index(
            "const liveStatus = "
            "document.getElementById("
        )
        final_endblock = self.template.rfind(
            "{% endblock %}"
        )
        self.assertLess(
            poll_index,
            final_endblock,
        )
        self.assertIn(
            "window.setTimeout(poll, 1500)",
            self.template,
        )
        self.assertIn(
            'credentials: "same-origin"',
            self.template,
        )

    def test_terminal_result_updates_dock_without_reload(
        self,
    ):
        self.assertIn(
            "if (data.terminal) {",
            self.template,
        )
        self.assertIn(
            "persistBottomNotice(data);",
            self.template,
        )
        self.assertIn(
            '"wms360:shipping-purchase-status"',
            self.template,
        )
        self.assertNotIn(
            "stopped = true;\n"
            "          window.location.reload();",
            self.template,
        )

    def test_bottom_notice_last_ten_seconds(self):
        self.assertIn(
            'id="shippingPurchaseBottomNotice"',
            self.template,
        )
        self.assertIn(
            "const NOTICE_DURATION_MS = 10000;",
            self.template,
        )
        self.assertIn(
            "restoreBottomNotice();",
            self.template,
        )
        self.assertIn(
            "bottom: 24px;",
            self.template,
        )
