from pathlib import Path
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from shipping_hub.forms import PROVIDER_CREDENTIAL_FIELDS
from shipping_hub.models import CompanyShippingIntegration
from shipping_hub.services.integration_credentials import (
    PROVIDER_REQUIRED_CREDENTIAL_FIELDS,
)
from shipping_hub.services.skydropx import SkydropxAPIError, SkydropxClient


SKYDROPX_SETTINGS = {
    "SHIPPING_HUB_SKYDROPX_PRO_ENABLED": True,
    "SHIPPING_HUB_SKYDROPX_SANDBOX_BASE_URL": "https://sandbox.example",
}


class ProviderCredentialsCorrectionTests(SimpleTestCase):
    def test_all_visible_providers_have_a_credential_schema(self):
        for provider, _label in CompanyShippingIntegration.PROVIDER_CHOICES:
            self.assertIn(provider, PROVIDER_CREDENTIAL_FIELDS)

    def test_skydropx_requires_client_credentials(self):
        self.assertEqual(
            PROVIDER_CREDENTIAL_FIELDS["skydropx"],
            ("client_id", "client_secret"),
        )

    def test_runtime_validation_matches_form_schema_for_every_provider(self):
        for provider, fields in PROVIDER_CREDENTIAL_FIELDS.items():
            self.assertEqual(PROVIDER_REQUIRED_CREDENTIAL_FIELDS[provider], fields)

    def test_empty_skydropx_client_id_is_rejected(self):
        with override_settings(**SKYDROPX_SETTINGS):
            with self.assertRaises(SkydropxAPIError) as caught:
                SkydropxClient("", "secret")
        self.assertEqual(caught.exception.error_code, "missing_credentials")

    def test_empty_skydropx_secret_is_rejected(self):
        with override_settings(**SKYDROPX_SETTINGS):
            with self.assertRaises(SkydropxAPIError):
                SkydropxClient("client", "")

    @override_settings(**SKYDROPX_SETTINGS)
    def test_oauth_uses_client_credentials_form_data(self):
        client = SkydropxClient("client", "secret")
        response = Mock(status_code=200)
        response.json.return_value = {"access_token": "bearer", "expires_in": 7200}
        with patch.object(client.session, "post", return_value=response) as post:
            token, _expires_at = client._request_access_token()
        self.assertEqual(token, "bearer")
        self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "client_credentials")
        self.assertEqual(post.call_args.kwargs["data"]["client_id"], "client")
        self.assertEqual(post.call_args.kwargs["data"]["client_secret"], "secret")

    @override_settings(**SKYDROPX_SETTINGS)
    def test_access_token_is_cached(self):
        client = SkydropxClient("client-cache", "secret")
        with patch.object(client, "_request_access_token", return_value=("token", 10**12)) as request:
            self.assertEqual(client._get_access_token(), "token")
            self.assertEqual(client._get_access_token(), "token")
        request.assert_called_once()

    @override_settings(**SKYDROPX_SETTINGS)
    def test_401_refreshes_token_once(self):
        client = SkydropxClient("client-refresh", "secret")
        denied = Mock(status_code=401)
        denied.json.return_value = {"error": "expired"}
        accepted = Mock(status_code=200)
        accepted.json.return_value = {"data": []}
        with patch.object(client, "_get_access_token", side_effect=["old", "new"]), patch.object(
            client.session, "request", side_effect=[denied, accepted]
        ) as request:
            result = client._request("GET", "/api/v1/shipments")
        self.assertEqual(result, {"data": []})
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[1].kwargs["headers"]["Authorization"], "Bearer new")

    def test_global_template_contains_every_credential_field(self):
        template = Path(__file__).parent / "templates" / "shipping_hub" / "integration_form.html"
        content = template.read_text(encoding="utf-8")
        for field_name in {field for fields in PROVIDER_CREDENTIAL_FIELDS.values() for field in fields}:
            self.assertIn(field_name, content)

    def test_global_template_has_dynamic_provider_map(self):
        template = Path(__file__).parent / "templates" / "shipping_hub" / "integration_form.html"
        content = template.read_text(encoding="utf-8")
        for provider, _label in CompanyShippingIntegration.PROVIDER_CHOICES:
            self.assertIn(provider, content)

    def test_master_mode_is_supported_by_same_form(self):
        template = Path(__file__).parent / "templates" / "shipping_hub" / "integration_form.html"
        content = template.read_text(encoding="utf-8")
        self.assertIn("form.account_mode", content)
        self.assertIn("master-account-note", content)

    def test_all_operational_options_are_rendered_once(self):
        template = Path(__file__).parent / "templates" / "shipping_hub" / "integration_form.html"
        content = template.read_text(encoding="utf-8")
        for field_name in ("priority", "allow_quote_fallback", "is_active", "is_primary", "webhook_secret"):
            self.assertIn(f"form.{field_name}", content)
