from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from shipping_hub.models import CompanyShippingIntegration, ShippingQuote
from shipping_hub.services.skydropx import SkydropxAPIError, SkydropxClient, normalize_skydropx_rates
from shipping_hub.strategies.resolver import STRATEGY_CLASSES


class MultiProviderPhase6Tests(SimpleTestCase):
    def test_skydropx_strategy_is_registered(self):
        self.assertIn(CompanyShippingIntegration.PROVIDER_SKYDROPX, STRATEGY_CLASSES)

    def test_quote_remembers_exact_integration(self):
        field = ShippingQuote._meta.get_field("integration")
        self.assertEqual(field.remote_field.on_delete.__name__, "PROTECT")

    def test_integration_has_priority(self):
        self.assertEqual(CompanyShippingIntegration._meta.get_field("priority").default, 100)

    def test_fallback_is_enabled_by_default(self):
        self.assertTrue(CompanyShippingIntegration._meta.get_field("allow_quote_fallback").default)

    @override_settings(SHIPPING_HUB_SKYDROPX_PRO_ENABLED=False)
    def test_skydropx_is_closed_by_default(self):
        with self.assertRaises(SkydropxAPIError) as caught:
            SkydropxClient("client-id", "client-secret", "sandbox")
        self.assertEqual(caught.exception.error_code, "provider_disabled")

    @override_settings(
        SHIPPING_HUB_SKYDROPX_PRO_ENABLED=True,
        SHIPPING_HUB_SKYDROPX_SANDBOX_BASE_URL="http://unsafe.example",
    )
    def test_non_https_endpoint_is_rejected(self):
        with self.assertRaises(SkydropxAPIError) as caught:
            SkydropxClient("client-id", "client-secret", "sandbox")
        self.assertEqual(caught.exception.error_code, "invalid_endpoint")

    @override_settings(
        SHIPPING_HUB_SKYDROPX_PRO_ENABLED=True,
        SHIPPING_HUB_SKYDROPX_SANDBOX_BASE_URL="https://sandbox.example/api",
    )
    def test_secret_is_not_preloaded_in_headers_or_url(self):
        client = SkydropxClient("client-id", "top-secret", "sandbox")
        self.assertNotIn("Authorization", client.session.headers)
        self.assertNotIn("top-secret", client.base_url)

    def test_rates_are_normalized(self):
        rates = normalize_skydropx_rates([{
            "id": "rate-1", "provider": "DHL", "service_level_name": "Express",
            "service_level_code": "EXP", "total_pricing": "199.90",
            "currency_local": "MXN", "days": 2,
        }])
        self.assertEqual(rates[0]["amount"], Decimal("199.90"))
        self.assertEqual(rates[0]["external_rate_id"], "rate-1")

    def test_rate_without_provider_rate_id_gets_safe_local_reference(self):
        rates = normalize_skydropx_rates(
            [{"provider": "DHL", "total_pricing": 100}]
        )

        self.assertEqual(len(rates), 1)
        rate = rates[0]
        self.assertEqual(rate["amount"], Decimal("100"))
        self.assertTrue(
            rate["external_rate_id"].startswith("skydropx-dhl-")
        )
        self.assertEqual(
            rate["rate_id"],
            rate["external_rate_id"],
        )
        self.assertEqual(rate["quotation_id"], "")
        self.assertTrue(
            rate["response_data"]["generated_rate_reference"]
        )

    @override_settings(
        SHIPPING_HUB_SKYDROPX_PRO_ENABLED=True,
        SHIPPING_HUB_SKYDROPX_SANDBOX_BASE_URL="https://sandbox.example/api",
    )
    def test_timeout_is_uncertain_and_retriable(self):
        client = SkydropxClient("client-id", "client-secret", "sandbox")
        with patch.object(client, "_get_access_token", return_value="token"), patch.object(client.session, "request", side_effect=__import__("requests").Timeout):
            with self.assertRaises(SkydropxAPIError) as caught:
                client.test_connection()
        self.assertTrue(caught.exception.is_temporary)
        self.assertEqual(caught.exception.error_code, "timeout")
