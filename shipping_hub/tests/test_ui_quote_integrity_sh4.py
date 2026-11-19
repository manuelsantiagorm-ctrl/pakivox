from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase

from shipping_hub.services.quote_selection import (
    build_quote_integrity_payload,
    quote_integrity_hash,
)


class UiQuoteIntegrityTests(SimpleTestCase):
    def test_ui_integrity_hash_is_deterministic(self):
        payload = build_quote_integrity_payload(
            company_id=4,
            shipping_order_id=23,
            integration_id=3,
            provider="skydropx",
            carrier_code="fedex",
            carrier_name="FedEx",
            service_code="express_saver",
            service_name="Express Saver",
            amount=Decimal("52.27"),
            currency="MXN",
            estimated_delivery_days=4,
        )
        self.assertEqual(
            quote_integrity_hash(payload),
            quote_integrity_hash(dict(payload)),
        )

    def test_integrity_changes_when_amount_changes(self):
        base = dict(
            company_id=4,
            shipping_order_id=23,
            integration_id=3,
            provider="skydropx",
            carrier_code="fedex",
            carrier_name="FedEx",
            service_code="express_saver",
            service_name="Express Saver",
            currency="MXN",
            estimated_delivery_days=4,
        )
        first = build_quote_integrity_payload(
            amount=Decimal("52.27"),
            **base,
        )
        second = build_quote_integrity_payload(
            amount=Decimal("52.28"),
            **base,
        )
        self.assertNotEqual(
            quote_integrity_hash(first),
            quote_integrity_hash(second),
        )
