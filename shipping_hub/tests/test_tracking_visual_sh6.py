from pathlib import Path

from django.test import SimpleTestCase

from shipping_hub.services.tracking_presentation import (
    PROGRESS_STEPS,
    STATUS_PROGRESS,
)


class TrackingPresentationContractTests(SimpleTestCase):
    def test_progress_has_five_stable_steps(self):
        self.assertEqual(len(PROGRESS_STEPS), 5)
        self.assertEqual(PROGRESS_STEPS[-1][0], "delivered")

    def test_delivered_completes_all_steps(self):
        self.assertEqual(STATUS_PROGRESS["delivered"], 5)

    def test_dispatch_view_exposes_timeline(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "dispatch_views.py").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn("def shipment_tracking_timeline(", source)
        self.assertIn("prefetch_related(", source)

    def test_center_separates_out_for_delivery(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "dispatch_views.py").read_text(
            encoding="utf-8-sig"
        )
        self.assertIn('tab == "out_for_delivery"', source)
