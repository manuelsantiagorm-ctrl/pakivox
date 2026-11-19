from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase, override_settings

from shipping_hub.services.flight_recorder import (
    clear_flight_context,
    record_flight_event,
    sanitize,
    set_flight_context,
)


class ShippingFlightRecorderSH89Tests(SimpleTestCase):
    def tearDown(self):
        clear_flight_context()

    def test_secrets_are_never_recorded(self):
        result = sanitize(
            {
                "Authorization": "Bearer secret",
                "client_secret": "top-secret",
                "access_token": "token-value",
            }
        )
        self.assertEqual(
            result["Authorization"],
            "[REDACTED_SECRET]",
        )
        self.assertEqual(
            result["client_secret"],
            "[REDACTED_SECRET]",
        )
        self.assertEqual(
            result["access_token"],
            "[REDACTED_SECRET]",
        )

    def test_personal_data_is_fingerprinted(self):
        result = sanitize(
            {
                "name": "Cliente Uno",
                "email": "cliente@example.com",
                "street1": "Calle Privada 10",
            }
        )
        self.assertTrue(result["name"]["redacted"])
        self.assertTrue(result["email"]["redacted"])
        self.assertTrue(result["street1"]["redacted"])
        self.assertNotIn(
            "Cliente Uno",
            str(result),
        )

    def test_event_is_written_with_purchase_context(self):
        with TemporaryDirectory() as directory:
            with override_settings(
                SHIPPING_HUB_FLIGHT_RECORDER_ENABLED=True,
                SHIPPING_HUB_FLIGHT_RECORDER_DIR=directory,
            ):
                set_flight_context(
                    purchase_id=99,
                    company_id=7,
                    shipment_id=12,
                )
                record_flight_event(
                    "test_event",
                    {"status": "ok"},
                )

                files = list(
                    Path(directory).glob(
                        "purchase_99_*.jsonl"
                    )
                )
                self.assertEqual(len(files), 1)
                content = files[0].read_text(
                    encoding="utf-8"
                )
                self.assertIn(
                    '"event_type": "test_event"',
                    content,
                )
                self.assertIn(
                    '"purchase_id": 99',
                    content,
                )
