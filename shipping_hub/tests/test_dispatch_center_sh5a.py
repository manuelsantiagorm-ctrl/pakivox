from django.test import SimpleTestCase

from shipping_hub.services.dispatch_flow import (
    IN_TRANSIT_STATUSES,
    READY_STATUSES,
)


class DispatchContractTests(SimpleTestCase):
    def test_ready_and_transit_are_disjoint(self):
        self.assertFalse(READY_STATUSES & IN_TRANSIT_STATUSES)

    def test_shipped_is_transit(self):
        self.assertIn("shipped", IN_TRANSIT_STATUSES)

    def test_label_ready_is_ready(self):
        self.assertIn("label_ready", READY_STATUSES)
