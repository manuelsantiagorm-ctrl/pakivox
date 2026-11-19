from decimal import Decimal

from django.test import SimpleTestCase

from shipping_hub.services.live_purchase_preflight import (
    PurchasePreflight,
    build_live_confirmation,
)


class LivePurchaseConfirmationTests(SimpleTestCase):
    def _preflight(self):
        return PurchasePreflight(
            purchase_id=1,
            company_id=4,
            shipment_id=1,
            shipping_order_id=13,
            quote_id=59,
            integration_id=3,
            provider="skydropx",
            environment="production",
            amount=Decimal("99.88"),
            currency="MXN",
            package_count=1,
            status="queued",
            charge_status="unknown",
            idempotency_key="abc",
            integrity_sha256="hash",
        )

    def test_confirmation_contains_purchase_provider_and_amount(self):
        self.assertEqual(
            build_live_confirmation(self._preflight()),
            "COMPRAR-GUIA-1-SKYDROPX-MXN-99.88",
        )

    def test_confirmation_changes_if_amount_changes(self):
        preflight = self._preflight()
        changed = PurchasePreflight(
            **{
                **preflight.__dict__,
                "amount": Decimal("100.00"),
            }
        )
        self.assertNotEqual(
            build_live_confirmation(preflight),
            build_live_confirmation(changed),
        )
