import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from shipping_hub.services.provider_event_simulator import (
    SCENARIOS,
    build_payloads,
    encode_and_sign,
    scenario_payload,
    scenario_sequence,
)


class ProviderEventSimulatorTests(SimpleTestCase):
    def test_all_scenarios_generate_payload(self):
        for index, scenario in enumerate(sorted(SCENARIOS), start=1):
            payload = scenario_payload(
                scenario=scenario,
                sequence=index,
                run_id="abcdef123456",
                provider="skydropx",
            )
            self.assertTrue(payload["simulation"])
            self.assertTrue(payload["id"].startswith("sim-"))
            self.assertTrue(
                payload["data"]["trackingNumber"].startswith("WMS360SIM")
            )

    def test_happy_path_has_four_events(self):
        self.assertEqual(
            scenario_sequence("happy_path"),
            (
                "created",
                "in_transit",
                "out_for_delivery",
                "delivered",
            ),
        )

    def test_out_of_order_starts_delivered(self):
        self.assertEqual(
            scenario_sequence("out_of_order")[0],
            "delivered",
        )

    def test_count_multiplies_sequence(self):
        payloads = build_payloads(
            scenario="happy_path",
            count=3,
            provider="envia",
            run_id="run123",
        )
        self.assertEqual(len(payloads), 12)
        self.assertEqual(len({item["id"] for item in payloads}), 12)

    def test_unknown_scenario_rejected(self):
        with self.assertRaises(ValueError):
            scenario_sequence("not-real")

    @patch(
        "shipping_hub.services.provider_event_simulator.webhook_secret",
        return_value="secret-demo",
    )
    def test_signature_is_valid_hmac_sha256(self, mocked_secret):
        integration = SimpleNamespace(pk=1)
        payload = scenario_payload(
            scenario="created",
            sequence=1,
            run_id="run123",
            provider="skydropx",
        )
        simulated = encode_and_sign(
            integration=integration,
            payload=payload,
        )
        expected = hmac.new(
            b"secret-demo",
            simulated.body,
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(simulated.signature, expected)
        self.assertEqual(
            json.loads(simulated.body)["id"],
            payload["id"],
        )
