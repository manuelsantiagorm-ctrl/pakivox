from django.test import SimpleTestCase

from shipping_hub.services.synthetic_tracking_lab import (
    synthetic_tracking_payloads,
)


class SyntheticTrackingPayloadTests(SimpleTestCase):
    def test_all_events_share_same_tracking(self):
        payloads = synthetic_tracking_payloads(
            run_id="run123",
            tracking_number="WMS360SIMLINKTRACK",
            provider_purchase_id="SIM-PURCHASE",
        )
        self.assertEqual(len(payloads), 4)
        self.assertEqual(
            {
                payload["data"]["trackingNumber"]
                for payload in payloads
            },
            {"WMS360SIMLINKTRACK"},
        )

    def test_status_order_ends_delivered(self):
        payloads = synthetic_tracking_payloads(
            run_id="run123",
            tracking_number="TRACK",
            provider_purchase_id="PURCHASE",
        )
        statuses = [
            payload["data"]["status"]
            for payload in payloads
        ]
        self.assertEqual(
            statuses,
            [
                "created",
                "in_transit",
                "out_for_delivery",
                "delivered",
            ],
        )

    def test_event_ids_are_unique(self):
        payloads = synthetic_tracking_payloads(
            run_id="run123",
            tracking_number="TRACK",
            provider_purchase_id="PURCHASE",
        )
        self.assertEqual(
            len({payload["id"] for payload in payloads}),
            4,
        )
