from pathlib import Path
from django.test import SimpleTestCase


class ShippingStateCoherenceSH71ContractTests(SimpleTestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.source = (self.root / "services" / "dispatch_flow.py").read_text(encoding="utf-8-sig")

    def test_postgresql_safe_locks_and_company_scope(self):
        self.assertIn("CarrierShipment.objects.select_for_update()", self.source)
        self.assertIn("OrderFulfillment.objects.select_for_update()", self.source)
        self.assertIn("company_id=company_id", self.source)
        self.assertNotIn("select_related(", self.source.split("select_for_update", 1)[0])

    def test_dispatch_syncs_operational_models_and_audit(self):
        self.assertIn("_sync_shipping_order_dispatch", self.source)
        self.assertIn("OrderFulfillment.STATUS_SHIPPED", self.source)
        self.assertIn('action="shipment_dispatched_from_center"', self.source)
        self.assertIn("shipped_at", self.source)
        self.assertIn("shipped_by", self.source)

    def test_delivery_is_multiwarehouse_aware_and_terminal_safe(self):
        self.assertIn("_sync_order_from_fulfillments", self.source)
        self.assertIn("statuses == {OrderFulfillment.STATUS_DELIVERED}", self.source)
        self.assertIn("Un cumplimiento en estado terminal no puede retroceder", self.source)
        self.assertIn("fulfillment_changed", self.source)
