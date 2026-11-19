from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from shipping_hub.services.quote_selection import (
    _decimal_amount,
    build_quote_integrity_payload,
    quote_integrity_hash,
)


class QuoteSelectionSecurityTests(SimpleTestCase):
    def test_amount_is_rounded(self):
        self.assertEqual(
            _decimal_amount("99.886"),
            Decimal("99.89"),
        )

    def test_negative_amount_is_rejected(self):
        with self.assertRaises(ValidationError):
            _decimal_amount("-1")

    def test_hash_is_stable(self):
        payload = build_quote_integrity_payload(
            company_id=4,
            shipping_order_id=13,
            integration_id=3,
            provider="skydropx",
            carrier_code="imile",
            carrier_name="Imile",
            service_code="express",
            service_name="Express Jal",
            amount=Decimal("99.88"),
            currency="MXN",
            estimated_delivery_days=2,
        )
        self.assertEqual(
            quote_integrity_hash(payload),
            quote_integrity_hash(payload),
        )

    def test_hash_changes_when_amount_changes(self):
        base = build_quote_integrity_payload(
            company_id=4,
            shipping_order_id=13,
            integration_id=3,
            provider="skydropx",
            carrier_code="imile",
            carrier_name="Imile",
            service_code="express",
            service_name="Express Jal",
            amount=Decimal("99.88"),
            currency="MXN",
            estimated_delivery_days=2,
        )
        changed = dict(base)
        changed["amount"] = "999.88"
        self.assertNotEqual(
            quote_integrity_hash(base),
            quote_integrity_hash(changed),
        )
