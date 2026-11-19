import hashlib
import hmac
import json
from pathlib import Path

from cryptography.fernet import Fernet
from django.test import SimpleTestCase, override_settings

from shipping_hub.models import (
    TrackingSyncAttempt,
    TrackingWebhookReceipt,
)
from shipping_hub.services.credentials import clear_credentials_cipher_cache, encrypt_credentials
from shipping_hub.services.tracking import normalize_event, normalize_status, verify_signature


class TrackingPhase7Tests(SimpleTestCase):
    def tearDown(self):
        clear_credentials_cipher_cache()

    def test_normalizes_in_transit_status(self):
        self.assertEqual(normalize_status("in-transit"), "in_transit")

    def test_normalizes_delivery_status(self):
        self.assertEqual(normalize_status("delivered"), "delivered")

    def test_unknown_status_cannot_change_shipment(self):
        self.assertEqual(normalize_status("mystery"), "")

    def test_event_fallback_id_is_deterministic(self):
        payload = {"tracking_number": "ABC", "status": "shipped"}
        self.assertEqual(
            normalize_event(payload)["provider_event_id"],
            normalize_event(payload)["provider_event_id"],
        )

    def test_event_accepts_camel_case_tracking(self):
        event = normalize_event({"trackingNumber": "MX123", "status": "delivered"})
        self.assertEqual(event["tracking_number"], "MX123")

    def test_webhook_receipt_is_unique_per_integration_event(self):
        constraint_names = {item.name for item in TrackingWebhookReceipt._meta.constraints}
        self.assertIn("uniq_tracking_hook_integration_event", constraint_names)

    def test_webhook_receipt_protects_integration_history(self):
        field = TrackingWebhookReceipt._meta.get_field("integration")
        self.assertEqual(field.remote_field.on_delete.__name__, "PROTECT")

    def test_sync_attempt_protects_integration_history(self):
        field = TrackingSyncAttempt._meta.get_field("integration")
        self.assertEqual(field.remote_field.on_delete.__name__, "PROTECT")

    def test_webhook_statuses_include_ignored(self):
        values = {value for value, _ in TrackingWebhookReceipt.STATUS_CHOICES}
        self.assertIn("ignored", values)

    def test_sync_statuses_include_failed(self):
        values = {value for value, _ in TrackingSyncAttempt.STATUS_CHOICES}
        self.assertIn("failed", values)

    @override_settings(SHIPPING_HUB_CREDENTIAL_KEYS=[])
    def test_missing_cipher_configuration_fails_closed(self):
        clear_credentials_cipher_cache()
        with self.assertRaises(Exception):
            encrypt_credentials({"webhook_secret": "secret"})

    def test_templates_include_automatic_refresh(self):
        template = Path(__file__).resolve().parent.parent / "templates" / "shipping_hub" / "tracking_detail.html"
        self.assertIn("setInterval(refresh,30000)", template.read_text(encoding="utf-8"))
