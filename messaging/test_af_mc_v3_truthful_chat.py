from pathlib import Path

from django.conf import settings
from django.template.loader import get_template
from django.test import SimpleTestCase


class AFMCV3TruthfulChatTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        base = Path(settings.BASE_DIR)
        cls.template = (base / "templates/messaging/chat_detail.html").read_text(encoding="utf-8-sig")
        cls.inbox = (base / "templates/messaging/chat_inbox.html").read_text(encoding="utf-8-sig")
        cls.views = (base / "messaging/views.py").read_text(encoding="utf-8-sig")
        cls.javascript = (base / "static/js/messaging_chat_exact.js").read_text(encoding="utf-8-sig")

    def test_template_compiles(self):
        self.assertIsNotNone(get_template("messaging/chat_detail.html"))

    def test_presence_is_not_invented(self):
        self.assertNotIn("En línea", self.template)
        self.assertIn("conversation.get_status_display", self.template)
        self.assertIn("conversation.whatsapp_number.channel_name", self.template)

    def test_badge_is_pending_reply_not_provider_read_status(self):
        self.assertIn("pending_reply_count", self.template)
        self.assertIn("pending_reply_count", self.views)
        self.assertIn("created_at__gt=last_outbound", self.views)
        self.assertIn("data-pending", self.template)
        self.assertIn("dataset.pending", self.javascript)
        self.assertIn("Sin responder", self.template)
        self.assertIn("Sin responder", self.inbox)
        self.assertIn("Pendientes de respuesta", self.inbox)
        self.assertNotIn("No leídos", self.inbox)

    def test_pending_reply_does_not_use_whatsapp_read_status(self):
        helper = self.views.split("def get_conversation_rows", 1)[1].split(
            "# ============================================================", 1
        )[0]
        pending_block = helper.split("pending_reply_messages", 1)[1].split("unread_count", 1)[0]
        self.assertNotIn("STATUS_READ", pending_block)
        self.assertIn("DIRECTION_INBOUND", pending_block)
        self.assertIn("DIRECTION_OUTBOUND", helper)

    def test_decorative_buttons_without_action_were_removed(self):
        for label in (
            'aria-label="Opciones de bandeja"',
            'aria-label="Más opciones"',
            'aria-label="Adjuntar archivo"',
            'aria-label="Insertar emoji"',
            'aria-label="Usar respuesta rápida"',
            "mx-template-button",
        ):
            self.assertNotIn(label, self.template)

    def test_real_actions_remain(self):
        for action in (
            "messaging:chat_reply", "messaging:chat_assign_me", "messaging:chat_close",
            "messaging:chat_reopen", "messaging:chat_create_order",
        ):
            self.assertIn(action, self.template)

    def test_mobile_and_profile_remain(self):
        for pane in ("inbox", "chat", "customer"):
            self.assertIn(f'data-mx-open="{pane}"', self.template)
        self.assertIn("whatsapp_smart_profile", self.template)
        self.assertIn("data-mx-reply", self.template)
