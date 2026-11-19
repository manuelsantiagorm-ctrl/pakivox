from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from messaging.whatsapp_gateway import (
    WhatsAppProviderError,
    WhatsAppProviderNotSupported,
    is_whatsapp_channel_ready,
    send_whatsapp_text,
)
from messaging.ycloud_api import YCloudWhatsAppHTTPError


class YCloudGatewayTests(SimpleTestCase):
    def make_channel(self, **overrides):
        values = {
            "company_id": 1,
            "warehouse_id": 10,
            "provider": "ycloud",
            "is_active": True,
            "is_ycloud_ready": True,
            "display_number": "+525512345678",
            "PROVIDER_TWILIO": "twilio",
            "PROVIDER_META": "meta",
            "PROVIDER_YCLOUD": "ycloud",
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_ready_ycloud_channel_is_ready(self):
        self.assertTrue(is_whatsapp_channel_ready(self.make_channel()))

    def test_incomplete_ycloud_channel_is_not_ready(self):
        self.assertFalse(
            is_whatsapp_channel_ready(self.make_channel(is_ycloud_ready=False))
        )

    def test_inactive_ycloud_channel_is_not_ready(self):
        self.assertFalse(
            is_whatsapp_channel_ready(self.make_channel(is_active=False))
        )

    @patch("messaging.ycloud_api.send_ycloud_whatsapp_text")
    def test_ycloud_routes_through_api_and_normalizes_result(self, mock_send):
        mock_send.return_value = {
            "provider": "ycloud",
            "provider_message_id": "wamid.YCLOUD001",
            "status": "sent",
            "raw_response": {"id": "wamid.YCLOUD001", "status": "sent"},
        }
        result = send_whatsapp_text(
            whatsapp_number=self.make_channel(),
            to_phone="525500001111",
            body="Hola desde WMS360",
        )
        mock_send.assert_called_once_with(
            from_phone="+525512345678",
            to_phone="525500001111",
            body="Hola desde WMS360",
        )
        self.assertEqual(result["provider"], "ycloud")
        self.assertEqual(result["provider_message_id"], "wamid.YCLOUD001")
        self.assertEqual(result["status"], "sent")

    @patch("messaging.ycloud_api.send_ycloud_whatsapp_text")
    def test_incomplete_channel_is_blocked_before_api(self, mock_send):
        with self.assertRaises(WhatsAppProviderError):
            send_whatsapp_text(
                whatsapp_number=self.make_channel(is_ycloud_ready=False),
                to_phone="525500001111",
                body="Hola",
            )
        mock_send.assert_not_called()

    @patch("messaging.ycloud_api.send_ycloud_whatsapp_text")
    def test_ycloud_error_is_wrapped_and_api_key_is_redacted(self, mock_send):
        secret = "ycloud_super_secret_123456"
        mock_send.side_effect = YCloudWhatsAppHTTPError(f"X-API-Key={secret}")
        with self.assertRaises(WhatsAppProviderError) as context:
            send_whatsapp_text(
                whatsapp_number=self.make_channel(),
                to_phone="525500001111",
                body="Hola",
            )
        self.assertNotIn(secret, str(context.exception))
        self.assertIn("[REDACTED]", str(context.exception))

    @patch("messaging.ycloud_api.send_ycloud_whatsapp_text")
    def test_unexpected_error_is_safely_wrapped(self, mock_send):
        secret = "unexpected_secret_123456"
        mock_send.side_effect = RuntimeError(f"api_key={secret}")
        with self.assertRaises(WhatsAppProviderError) as context:
            send_whatsapp_text(
                whatsapp_number=self.make_channel(),
                to_phone="525500001111",
                body="Hola",
            )
        self.assertNotIn(secret, str(context.exception))

    def test_missing_company_is_rejected(self):
        with self.assertRaises(WhatsAppProviderError):
            send_whatsapp_text(
                whatsapp_number=self.make_channel(company_id=None),
                to_phone="525500001111",
                body="Hola",
            )

    def test_missing_warehouse_is_rejected(self):
        with self.assertRaises(WhatsAppProviderError):
            send_whatsapp_text(
                whatsapp_number=self.make_channel(warehouse_id=None),
                to_phone="525500001111",
                body="Hola",
            )

    def test_unknown_provider_remains_rejected(self):
        with self.assertRaises(WhatsAppProviderNotSupported):
            send_whatsapp_text(
                whatsapp_number=self.make_channel(provider="unknown"),
                to_phone="525500001111",
                body="Hola",
            )
