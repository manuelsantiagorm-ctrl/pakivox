import hashlib
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from django.utils import timezone

from shipping_hub.models import ShippingProviderEvent
from shipping_hub.services.provider_event_engine import (
    EVENT_RETRY_DELAYS,
    MAX_EVENT_ATTEMPTS,
    _looks_like_purchase_event,
    _looks_like_tracking_event,
    _retry_at,
    normalize_provider_envelope,
    parse_json_body,
    sanitize_headers,
    supplied_signature,
    validate_event_signature,
)


class ProviderEventEnvelopeTests(SimpleTestCase):
    def test_parse_json_object(self):
        body = b'{"id":"evt-1","type":"tracking.updated"}'
        self.assertEqual(parse_json_body(body)["id"], "evt-1")

    def test_empty_body_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_json_body(b"")

    def test_json_array_is_rejected(self):
        with self.assertRaises(ValueError):
            parse_json_body(b"[]")

    def test_provider_event_id_is_preserved(self):
        body = b'{"id":"evt-1","type":"tracking.updated"}'
        result = normalize_provider_envelope(
            provider="skydropx",
            payload=json.loads(body),
            body=body,
        )
        self.assertEqual(result["provider_event_id"], "evt-1")

    def test_payload_hash_used_when_event_id_missing(self):
        body = b'{"type":"tracking.updated"}'
        result = normalize_provider_envelope(
            provider="envia",
            payload=json.loads(body),
            body=body,
        )
        self.assertEqual(
            result["provider_event_id"],
            hashlib.sha256(body).hexdigest(),
        )

    def test_nested_identifiers_are_extracted(self):
        body = json.dumps({
            "eventId": "evt-nested",
            "type": "shipment.status",
            "data": {
                "shipmentId": "ship-10",
                "trackingNumber": "TRACK-10",
            },
        }).encode()
        result = normalize_provider_envelope(
            provider="envia",
            payload=json.loads(body),
            body=body,
        )
        self.assertEqual(result["provider_purchase_id"], "ship-10")
        self.assertEqual(result["tracking_number"], "TRACK-10")

    def test_dedupe_key_is_deterministic(self):
        body = b'{"id":"evt-1","type":"tracking.updated"}'
        payload = json.loads(body)
        first = normalize_provider_envelope(
            provider="skydropx", payload=payload, body=body
        )
        second = normalize_provider_envelope(
            provider="skydropx", payload=payload, body=body
        )
        self.assertEqual(
            first["deduplication_key"],
            second["deduplication_key"],
        )

    def test_different_provider_changes_dedupe_key(self):
        body = b'{"id":"evt-1","type":"tracking.updated"}'
        payload = json.loads(body)
        sky = normalize_provider_envelope(
            provider="skydropx", payload=payload, body=body
        )
        envia = normalize_provider_envelope(
            provider="envia", payload=payload, body=body
        )
        self.assertNotEqual(
            sky["deduplication_key"],
            envia["deduplication_key"],
        )


class ProviderEventHeaderTests(SimpleTestCase):
    def test_sensitive_headers_are_removed(self):
        result = sanitize_headers({
            "Authorization": "Bearer secret",
            "Cookie": "secret",
            "X-Api-Key": "secret",
            "Content-Type": "application/json",
        })
        self.assertNotIn("Authorization", result)
        self.assertNotIn("Cookie", result)
        self.assertNotIn("X-Api-Key", result)
        self.assertEqual(result["Content-Type"], "application/json")

    def test_signature_header_is_detected(self):
        self.assertEqual(
            supplied_signature({"X-Webhook-Signature": "abc"}),
            "abc",
        )

    def test_signature_header_priority_is_stable(self):
        result = supplied_signature({
            "X-WMS360-Signature": "preferred",
            "X-Webhook-Signature": "secondary",
        })
        self.assertEqual(result, "preferred")


class ProviderEventSignatureTests(SimpleTestCase):
    def setUp(self):
        self.integration = SimpleNamespace(pk=1)
        self.body = b'{"id":"evt"}'

    @patch(
        "shipping_hub.services.provider_event_engine.verify_signature",
        return_value=True,
    )
    def test_valid_signature(self, mocked_verify):
        valid, status = validate_event_signature(
            integration=self.integration,
            body=self.body,
            headers={"X-Webhook-Signature": "signature"},
        )
        self.assertTrue(valid)
        self.assertEqual(
            status,
            ShippingProviderEvent.SIGNATURE_VALID,
        )
        mocked_verify.assert_called_once()

    @patch(
        "shipping_hub.services.provider_event_engine.verify_signature",
        return_value=False,
    )
    def test_invalid_signature(self, mocked_verify):
        valid, status = validate_event_signature(
            integration=self.integration,
            body=self.body,
            headers={"X-Webhook-Signature": "bad"},
        )
        self.assertFalse(valid)
        self.assertEqual(
            status,
            ShippingProviderEvent.SIGNATURE_INVALID,
        )

    def test_missing_signature(self):
        valid, status = validate_event_signature(
            integration=self.integration,
            body=self.body,
            headers={},
        )
        self.assertFalse(valid)
        self.assertEqual(
            status,
            ShippingProviderEvent.SIGNATURE_NOT_PROVIDED,
        )

    @patch(
        "shipping_hub.services.provider_event_engine.verify_signature",
        side_effect=ValueError("unsupported"),
    )
    def test_unsupported_signature_configuration(self, mocked_verify):
        valid, status = validate_event_signature(
            integration=self.integration,
            body=self.body,
            headers={"X-Webhook-Signature": "value"},
        )
        self.assertFalse(valid)
        self.assertEqual(
            status,
            ShippingProviderEvent.SIGNATURE_NOT_SUPPORTED,
        )


class ProviderEventClassificationTests(SimpleTestCase):
    def event(self, **kwargs):
        defaults = {
            "event_type": "",
            "tracking_number": "",
            "provider_purchase_id": "",
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_tracking_number_marks_tracking_event(self):
        self.assertTrue(
            _looks_like_tracking_event(
                self.event(tracking_number="TRACK-1")
            )
        )

    def test_delivered_marks_tracking_event(self):
        self.assertTrue(
            _looks_like_tracking_event(
                self.event(event_type="shipment.delivered")
            )
        )

    def test_label_marks_purchase_event(self):
        self.assertTrue(
            _looks_like_purchase_event(
                self.event(event_type="label.ready")
            )
        )

    def test_remote_purchase_id_marks_purchase_event(self):
        self.assertTrue(
            _looks_like_purchase_event(
                self.event(provider_purchase_id="ship-1")
            )
        )

    def test_unknown_event_is_not_actionable(self):
        event = self.event(event_type="customer.updated")
        self.assertFalse(_looks_like_tracking_event(event))
        self.assertFalse(_looks_like_purchase_event(event))


class ProviderEventRetryTests(SimpleTestCase):
    def test_retry_delays_are_progressive(self):
        self.assertEqual(
            list(EVENT_RETRY_DELAYS),
            sorted(EVENT_RETRY_DELAYS),
        )

    def test_max_attempts_matches_delay_count(self):
        self.assertEqual(
            MAX_EVENT_ATTEMPTS,
            len(EVENT_RETRY_DELAYS),
        )

    def test_first_retry_is_in_future(self):
        before = timezone.now()
        retry_at = _retry_at(1)
        self.assertGreater(retry_at, before)
        self.assertLess(
            retry_at,
            before + timedelta(seconds=EVENT_RETRY_DELAYS[0] + 2),
        )

    def test_attempts_above_max_use_last_delay(self):
        before = timezone.now()
        retry_at = _retry_at(999)
        expected = EVENT_RETRY_DELAYS[-1]
        self.assertGreaterEqual(
            retry_at,
            before + timedelta(seconds=expected - 1),
        )
        self.assertLess(
            retry_at,
            before + timedelta(seconds=expected + 2),
        )
