
from pathlib import Path

from django.conf import settings
from django.template.loader import get_template
from django.test import SimpleTestCase


class MetaEmbeddedSignupES4UITests(SimpleTestCase):
    databases = set()

    def setUp(self):
        self.path = (
            Path(settings.BASE_DIR)
            / "templates"
            / "messaging"
            / "meta_embedded_signup_start.html"
        )

    def test_template_compiles(self):
        template = get_template(
            "messaging/meta_embedded_signup_start.html"
        )
        self.assertIsNotNone(template)

    def test_ready_and_verifying_states_exist(self):
        content = self.path.read_text(
            encoding="utf-8"
        )

        self.assertIn(
            'nextStep === "ready"',
            content,
        )
        self.assertIn(
            'nextStep === "verifying"',
            content,
        )

    def test_status_panel_contracts_exist(self):
        content = self.path.read_text(
            encoding="utf-8"
        )

        for marker in (
            'id="meta-connection-panel"',
            'id="meta-step-business"',
            'id="meta-step-waba"',
            'id="meta-step-phone"',
            'id="meta-step-webhook"',
            'id="meta-step-warehouse"',
        ):
            self.assertIn(marker, content)

    def test_secrets_are_not_embedded_in_template(self):
        content = self.path.read_text(
            encoding="utf-8"
        )

        for forbidden in (
            "META_APP_SECRET",
            "permanent_access_token",
            "meta_access_token",
            "Authorization:",
        ):
            self.assertNotIn(forbidden, content)

    def test_error_state_explicitly_does_not_claim_activation(self):
        content = self.path.read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "No se activó ningún canal.",
            content,
        )
