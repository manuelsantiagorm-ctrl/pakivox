from pathlib import Path

from django.conf import settings
from django.template.loader import get_template
from django.test import SimpleTestCase


class AFMCV2ChatExactoTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        base = Path(settings.BASE_DIR)
        cls.template = (base / "templates/messaging/chat_detail.html").read_text(encoding="utf-8-sig")
        cls.css = (base / "static/css/messaging_chat_exact.css").read_text(encoding="utf-8-sig")
        cls.js = (base / "static/js/messaging_chat_exact.js").read_text(encoding="utf-8-sig")
        cls.widget_css = (base / "static/css/order_chat_widget.css").read_text(encoding="utf-8-sig")
        cls.widget_js = (base / "static/js/order_chat_widget.js").read_text(encoding="utf-8-sig")

    def test_template_compiles(self):
        self.assertIsNotNone(get_template("messaging/chat_detail.html"))

    def test_three_exact_desktop_columns_exist(self):
        self.assertIn("mx-inbox", self.template)
        self.assertIn("mx-chat", self.template)
        self.assertIn("mx-customer", self.template)
        self.assertIn("grid-template-columns:280px minmax(470px,1fr)300px", self.css)

    def test_all_real_mutating_actions_remain(self):
        for action in (
            "messaging:chat_reply", "messaging:chat_assign_me", "messaging:chat_close",
            "messaging:chat_reopen", "messaging:chat_create_order",
        ):
            self.assertIn(action, self.template)
        self.assertIn("reply_form.body", self.template)
        self.assertIn("whatsapp_smart_profile", self.template)

    def test_real_message_status_and_refresh_contract(self):
        for value in ("chat_message.status", "data-message-id", "Leído", "Entregado"):
            self.assertIn(value, self.template)
        self.assertIn("X-Requested-With", self.js)
        self.assertIn("lastIds", self.js)

    def test_mobile_navigation_matches_reference(self):
        for pane in ("inbox", "chat", "customer"):
            self.assertIn(f'data-mx-open="{pane}"', self.template)
            self.assertIn(f'data-mx-pane="{pane}"', self.template)
        self.assertIn("max-width:980px", self.css)
        self.assertIn("mx-mobile-nav", self.css)

    def test_quick_replies_profile_and_order_are_kept(self):
        self.assertIn("data-mx-reply", self.template)
        self.assertIn("Resumen comercial", self.template)
        self.assertIn("Crear pedido", self.template)
        self.assertIn("avg_response_time_display", self.template)

    def test_floating_widget_professional_controls_remain(self):
        for control in ("wmsOrderChatToggleSize", "wmsOrderChatReset", "wmsOrderChatMinimize", "wmsOrderChatRestore"):
            self.assertIn(control, self.widget_js)
        self.assertIn("bottom: 24px", self.widget_css)
        self.assertIn("height: min(700px, calc(100vh - 125px))", self.widget_css)

    def test_accessibility_and_reduced_motion(self):
        self.assertIn('aria-label="Navegación móvil"', self.template)
        self.assertIn("prefers-reduced-motion", self.css)
        self.assertIn("prefers-reduced-motion", self.widget_css)
