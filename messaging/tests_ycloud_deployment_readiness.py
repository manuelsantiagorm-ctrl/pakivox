from unittest.mock import patch

from django.core.management import call_command
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from messaging.production_readiness import collect_production_issues


PRODUCTION = dict(
    DEBUG=False,
    SECRET_KEY="s" * 64,
    ALLOWED_HOSTS=["wms360.mx"],
    CSRF_TRUSTED_ORIGINS=["https://wms360.mx"],
    SECURE_SSL_REDIRECT=True,
    SESSION_COOKIE_SECURE=True,
    CSRF_COOKIE_SECURE=True,
    YCLOUD_API_BASE_URL="https://api.ycloud.com/v2",
)


def production_settings(**overrides):
    return {**PRODUCTION, **overrides}


class ProductionReadinessUnitTests(SimpleTestCase):
    @override_settings(**production_settings(YCLOUD_ENABLED=False))
    def test_safe_staging_mode_passes_without_ycloud_secrets(self):
        issues = collect_production_issues(require_ycloud=False)
        self.assertFalse([i for i in issues if i.id.startswith("wms360.E")])

    @override_settings(**production_settings(YCLOUD_ENABLED=True, YCLOUD_API_KEY="k" * 32, YCLOUD_WEBHOOK_SECRET="w" * 48))
    def test_complete_production_configuration_passes(self):
        self.assertEqual(collect_production_issues(require_ycloud=True), [])

    @override_settings(**{**PRODUCTION, "SECRET_KEY": "short"})
    def test_short_django_secret_is_rejected(self):
        self.assertIn("wms360.E001", [i.id for i in collect_production_issues(False)])

    @override_settings(**{**PRODUCTION, "ALLOWED_HOSTS": ["*"]})
    def test_wildcard_host_is_rejected(self):
        self.assertIn("wms360.E002", [i.id for i in collect_production_issues(False)])

    @override_settings(**production_settings(CSRF_TRUSTED_ORIGINS=["http://wms360.mx"]))
    def test_non_https_csrf_origin_is_rejected(self):
        self.assertIn("wms360.E003", [i.id for i in collect_production_issues(False)])

    @override_settings(**production_settings(SESSION_COOKIE_SECURE=False))
    def test_insecure_session_cookie_is_rejected(self):
        self.assertIn("wms360.E004", [i.id for i in collect_production_issues(False)])

    @override_settings(**production_settings(YCLOUD_API_KEY="", YCLOUD_WEBHOOK_SECRET="w" * 48))
    def test_missing_ycloud_api_key_is_rejected_when_required(self):
        self.assertIn("wms360.E010", [i.id for i in collect_production_issues(True)])

    @override_settings(**production_settings(YCLOUD_API_KEY="k" * 32, YCLOUD_WEBHOOK_SECRET=""))
    def test_missing_webhook_secret_is_rejected_when_required(self):
        self.assertIn("wms360.E011", [i.id for i in collect_production_issues(True)])

    @override_settings(**production_settings(YCLOUD_API_KEY="k" * 32, YCLOUD_WEBHOOK_SECRET="w" * 48, YCLOUD_API_BASE_URL="http://api.ycloud.com/v2"))
    def test_non_https_ycloud_api_is_rejected(self):
        self.assertIn("wms360.E012", [i.id for i in collect_production_issues(True)])

    @override_settings(**production_settings(YCLOUD_ENABLED=False))
    def test_management_command_passes_in_safe_staging_mode(self):
        call_command("check_production_readiness")


class ProductionHealthTests(TestCase):
    def test_liveness_endpoint_is_public_and_minimal(self):
        response = self.client.get(reverse("messaging:health_live"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_readiness_endpoint_checks_database(self):
        response = self.client.get(reverse("messaging:health_ready"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["database"], "ok")

    @patch("messaging.production_readiness.connection.cursor", side_effect=DatabaseError("offline"))
    def test_readiness_returns_503_when_database_is_down(self, mocked_cursor):
        response = self.client.get(reverse("messaging:health_ready"))
        self.assertEqual(response.status_code, 503)
        self.assertNotContains(response, "offline", status_code=503)

    def test_health_endpoints_reject_post(self):
        self.assertEqual(self.client.post(reverse("messaging:health_live")).status_code, 405)
        self.assertEqual(self.client.post(reverse("messaging:health_ready")).status_code, 405)




