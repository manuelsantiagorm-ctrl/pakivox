from pathlib import Path

from django.test import SimpleTestCase


class FlightRecorderWiringSH89Tests(SimpleTestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[2]
        self.client = (
            root
            / "shipping_hub"
            / "services"
            / "skydropx.py"
        ).read_text(encoding="utf-8")
        self.engine = (
            root
            / "shipping_hub"
            / "services"
            / "purchase_engine.py"
        ).read_text(encoding="utf-8")

    def test_http_exchange_is_recorded(self):
        self.assertIn(
            "record_provider_exchange(",
            self.client,
        )
        self.assertIn(
            'phase="http_response"',
            self.client,
        )
        self.assertIn(
            'phase="http_exception"',
            self.client,
        )

    def test_purchase_context_and_result_are_recorded(self):
        self.assertIn(
            "set_flight_context(",
            self.engine,
        )
        self.assertIn(
            '"provider_result_interpreted"',
            self.engine,
        )
        self.assertIn(
            "clear_flight_context()",
            self.engine,
        )

    def test_recorder_does_not_change_purchase_call(self):
        self.assertEqual(
            self.engine.count(
                "adapter.create_purchase("
            ),
            1,
        )
