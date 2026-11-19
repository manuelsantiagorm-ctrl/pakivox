from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from shipping_hub.services.skydropx import normalize_skydropx_rates
from shipping_hub.services.quote_selection import _decimal_amount


class SkydropxQuotationPersistenceTests(SimpleTestCase):
    def test_normalizer_preserves_quotation_and_rate_ids(self):
        payload = {
            "data": {
                "id": "quotation-123",
                "rates": [
                    {
                        "id": "rate-456",
                        "attributes": {
                            "success": True,
                            "total": "99.88",
                            "provider_display_name": "Imile",
                            "provider_name": "imile",
                            "provider_service_name": "Express Jal",
                            "provider_service_code": "EXP",
                            "currency": "MXN",
                            "days": 2,
                        },
                    }
                ],
            }
        }
        rate = normalize_skydropx_rates(payload)[0]
        self.assertEqual(rate["quotation_id"], "quotation-123")
        self.assertEqual(rate["rate_id"], "rate-456")
        self.assertEqual(rate["external_rate_id"], "rate-456")
        self.assertEqual(
            rate["response_data"]["skydropx_quotation_id"],
            "quotation-123",
        )

    def test_normalizer_uses_attribute_quotation_id_as_fallback(self):
        payload = {
            "rates": [
                {
                    "id": "rate-1",
                    "attributes": {
                        "quotation_id": "quotation-fallback",
                        "success": True,
                        "total": "120.00",
                    },
                }
            ]
        }
        rate = normalize_skydropx_rates(payload)[0]
        self.assertEqual(
            rate["quotation_id"],
            "quotation-fallback",
        )

    def test_amount_security_remains_enabled(self):
        self.assertEqual(_decimal_amount("99.88"), Decimal("99.88"))
        with self.assertRaises(ValidationError):
            _decimal_amount("-1")
