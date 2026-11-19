from pathlib import Path

from django.test import SimpleTestCase

from shipping_hub.services.delivery_closure import (
    DeliveryClosureResult,
)


class DeliveryClosureContractTests(SimpleTestCase):
    def test_result_contract_is_stable(self):
        result = DeliveryClosureResult(
            shipment_id=8,
            shipping_order_id=23,
            order_id=46,
            packages_delivered=1,
            idempotent=False,
        )
        self.assertEqual(result.shipment_id, 8)
        self.assertEqual(result.packages_delivered, 1)
        self.assertFalse(result.idempotent)

    def test_tracking_engine_contains_delivery_hook(self):
        root = Path(__file__).resolve().parents[1]
        source = (
            root / "services" / "tracking.py"
        ).read_text(encoding="utf-8-sig")
        self.assertIn(
            "close_operational_delivery(",
            source,
        )
        self.assertIn(
            "status == CarrierShipment.STATUS_DELIVERED",
            source,
        )
