from pathlib import Path

from django.conf import settings
from django.template.loader import get_template
from django.test import SimpleTestCase


class AFMCChatProfessionalContractTests(SimpleTestCase):
    """Contrato de interfaz: no reemplaza las pruebas funcionales del gateway."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.base = Path(settings.BASE_DIR)
        cls.template = (
            cls.base / "templates" / "messaging" / "chat_detail.html"
        ).read_text(encoding="utf-8-sig")
        cls.chat_css = (
            cls.base / "static" / "css" / "messaging_chat_exact.css"
        ).read_text(encoding="utf-8-sig")
        cls.chat_js = (
            cls.base / "static" / "js" / "messaging_chat_exact.js"
        ).read_text(encoding="utf-8-sig")
        cls.widget_css = (
            cls.base / "static" / "css" / "order_chat_widget.css"
        ).read_text(encoding="utf-8-sig")
        cls.widget_js = (
            cls.base / "static" / "js" / "order_chat_widget.js"
        ).read_text(encoding="utf-8-sig")

    def test_template_compiles(self):
        self.assertIsNotNone(get_template("messaging/chat_detail.html"))

    def test_existing_real_actions_are_preserved(self):
        for action in (
            "messaging:chat_reply",
            "messaging:chat_assign_me",
            "messaging:chat_close",
            "messaging:chat_reopen",
            "messaging:chat_create_order",
        ):
            self.assertIn(action, self.template)
        self.assertIn("reply_form.body", self.template)
        self.assertIn("whatsapp_smart_profile", self.template)

    def test_responsive_three_pane_navigation_exists(self):
        for pane in ("inbox", "chat", "customer"):
            self.assertIn(f'data-mx-pane="{pane}"', self.template)
            self.assertIn(f'data-mx-open="{pane}"', self.template)
        self.assertIn("max-width:980px", self.chat_css)
        self.assertIn("mx-mobile-nav", self.chat_css)
        self.assertIn("data-mx-open", self.chat_js)

    def test_message_delivery_states_are_visible(self):
        for label in ("Leído", "Entregado", "Fallido", "Enviado"):
            self.assertIn(label, self.template)

    def test_floating_widget_keeps_all_controls_and_mobile_mode(self):
        for control in (
            "wmsOrderChatToggleSize",
            "wmsOrderChatReset",
            "wmsOrderChatMinimize",
            "wmsOrderChatRestore",
            "wmsOrderChatResizeHandle",
        ):
            self.assertIn(control, self.widget_js)
        self.assertIn("isMobileViewport", self.widget_js)
        self.assertIn("height: min(700px, calc(100vh - 125px))", self.widget_css)
        self.assertIn("bottom: 24px", self.widget_css)

    def test_accessibility_and_reduced_motion_contract(self):
        self.assertIn('aria-label="Navegación móvil"', self.template)
        self.assertIn("prefers-reduced-motion", self.chat_css)
        self.assertIn("prefers-reduced-motion", self.widget_css)
