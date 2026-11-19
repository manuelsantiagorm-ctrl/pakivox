from decimal import Decimal

from django.test import SimpleTestCase

from shipping_hub.services.multi_provider import (
    _deduplication_key,
    _normalize_amount,
    _normalize_days,
    _normalize_rate,
)


class MultiProviderNormalizationTests(SimpleTestCase):
    def test_amount_is_decimal_and_rounded(self):
        self.assertEqual(_normalize_amount("10.239"), Decimal("10.24"))

    def test_negative_amount_is_rejected(self):
        self.assertIsNone(_normalize_amount("-0.01"))

    def test_invalid_days_are_rejected(self):
        self.assertIsNone(_normalize_days("mañana"))

    def test_rate_requires_valid_amount(self):
        class Integration:
            provider = "envia"
            id = 7

        self.assertIsNone(
            _normalize_rate(
                rate={"amount": "invalid"},
                integration=Integration(),
            )
        )

    def test_rate_is_normalized_without_losing_fields(self):
        class Integration:
            provider = "envia"
            id = 7

        result = _normalize_rate(
            rate={
                "amount": "99.9",
                "carrier_name": "DHL",
                "service_name": "Express",
                "estimated_delivery_days": "2",
                "currency": "MXN",
            },
            integration=Integration(),
        )
        self.assertEqual(result["amount"], Decimal("99.90"))
        self.assertEqual(result["carrier"], "DHL")
        self.assertEqual(result["service"], "Express")
        self.assertEqual(result["estimated_delivery_days"], 2)
        self.assertEqual(result["currency"], "MXN")

    def test_deduplication_key_is_case_insensitive(self):
        first = _deduplication_key({
            "provider": "ENVIA",
            "carrier": "DHL",
            "service_code": "EXP",
            "amount": Decimal("100.00"),
            "estimated_delivery_days": 1,
        })
        second = _deduplication_key({
            "provider": "envia",
            "carrier": "dhl",
            "service_code": "exp",
            "amount": Decimal("100.00"),
            "estimated_delivery_days": 1,
        })
        self.assertEqual(first, second)
