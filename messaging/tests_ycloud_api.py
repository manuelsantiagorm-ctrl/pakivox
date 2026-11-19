from unittest.mock import MagicMock, patch

import requests
from django.test import SimpleTestCase, override_settings

from messaging.ycloud_api import (
    YCloudWhatsAppConfigurationError,
    YCloudWhatsAppHTTPError,
    clean_ycloud_exception_message,
    normalize_ycloud_base_url,
    normalize_ycloud_phone,
    send_ycloud_whatsapp_text,
)


@override_settings(
    YCLOUD_ENABLED=True,
    YCLOUD_API_KEY="ycloud_test_secret_123",
    YCLOUD_API_BASE_URL="https://api.ycloud.com/v2",
)
class YCloudWhatsAppAPITests(SimpleTestCase):
    def test_phone_is_normalized_to_e164(self):
        self.assertEqual(normalize_ycloud_phone("52 55 1234-5678"), "+525512345678")

    def test_mexican_legacy_prefix_is_normalized(self):
        self.assertEqual(normalize_ycloud_phone("+5215512345678"), "+525512345678")

    def test_invalid_phone_is_rejected(self):
        with self.assertRaises(YCloudWhatsAppConfigurationError):
            normalize_ycloud_phone("123")

    def test_only_official_https_base_url_is_accepted(self):
        self.assertEqual(
            normalize_ycloud_base_url("https://api.ycloud.com/v2/"),
            "https://api.ycloud.com/v2",
        )
        for value in (
            "http://api.ycloud.com/v2",
            "https://evil.example/v2",
            "https://api.ycloud.com/v2?key=secret",
            "https://api.ycloud.com/v2/../admin",
        ):
            with self.subTest(value=value):
                with self.assertRaises(YCloudWhatsAppConfigurationError):
                    normalize_ycloud_base_url(value)

    @override_settings(YCLOUD_ENABLED=False)
    def test_disabled_integration_is_rejected(self):
        with self.assertRaises(YCloudWhatsAppConfigurationError):
            send_ycloud_whatsapp_text(
                from_phone="+525500000001",
                to_phone="+525500000002",
                body="Hola",
            )

    @override_settings(YCLOUD_API_KEY="")
    def test_missing_api_key_is_rejected(self):
        with self.assertRaises(YCloudWhatsAppConfigurationError):
            send_ycloud_whatsapp_text(
                from_phone="+525500000001",
                to_phone="+525500000002",
                body="Hola",
            )

    def test_empty_message_is_rejected_without_http_call(self):
        with patch("messaging.ycloud_api.requests.post") as mock_post:
            with self.assertRaises(YCloudWhatsAppConfigurationError):
                send_ycloud_whatsapp_text(
                    from_phone="+525500000001",
                    to_phone="+525500000002",
                    body="   ",
                )
            mock_post.assert_not_called()

    def test_secret_is_redacted(self):
        cleaned = clean_ycloud_exception_message(
            "X-API-Key: ycloud_test_secret_123"
        )
        self.assertNotIn("ycloud_test_secret_123", cleaned)
        self.assertIn("[REDACTED]", cleaned)

    @patch("messaging.ycloud_api.requests.post")
    def test_success_returns_neutral_result(self, mock_post):
        response = MagicMock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {
            "id": "msg_ycloud_123",
            "status": "accepted",
        }
        mock_post.return_value = response

        result = send_ycloud_whatsapp_text(
            from_phone="+52 55 0000 0001",
            to_phone="+52 55 0000 0002",
            body="Hola desde WMS360",
        )

        self.assertEqual(result["provider"], "ycloud")
        self.assertEqual(result["provider_message_id"], "msg_ycloud_123")
        self.assertEqual(result["status"], "sent")
        _, kwargs = mock_post.call_args
        self.assertEqual(
            kwargs["json"],
            {
                "from": "+525500000001",
                "to": "+525500000002",
                "type": "text",
                "text": {"body": "Hola desde WMS360"},
            },
        )
        self.assertEqual(kwargs["headers"]["X-API-Key"], "ycloud_test_secret_123")
        self.assertEqual(kwargs["timeout"], (5, 20))

    @patch("messaging.ycloud_api.requests.post")
    def test_nested_message_id_is_supported(self, mock_post):
        response = MagicMock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {
            "whatsappMessage": {"id": "msg_nested_456"}
        }
        mock_post.return_value = response
        result = send_ycloud_whatsapp_text(
            from_phone="+525500000001",
            to_phone="+525500000002",
            body="Hola",
        )
        self.assertEqual(result["provider_message_id"], "msg_nested_456")

    @patch("messaging.ycloud_api.requests.post")
    def test_external_id_and_both_remote_ids_are_preserved(self, mock_post):
        response = MagicMock(ok=True, status_code=200)
        response.json.return_value = {
            "whatsappMessage": {
                "id": "ycloud-internal-1",
                "wamid": "wamid.meta-1",
                "externalId": "wms-message-1",
            }
        }
        mock_post.return_value = response
        result = send_ycloud_whatsapp_text(
            from_phone="+525500000001",
            to_phone="+525500000002",
            body="Hola",
            external_id="wms-message-1",
        )
        self.assertEqual(result["provider_message_id"], "ycloud-internal-1")
        self.assertEqual(result["raw_response"]["wamid"], "wamid.meta-1")
        self.assertEqual(result["raw_response"]["externalId"], "wms-message-1")
        self.assertEqual(mock_post.call_args.kwargs["json"]["externalId"], "wms-message-1")

    @patch("messaging.ycloud_api.requests.post")
    def test_success_without_message_id_is_rejected(self, mock_post):
        response = MagicMock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {"status": "accepted"}
        mock_post.return_value = response
        with self.assertRaises(YCloudWhatsAppHTTPError):
            send_ycloud_whatsapp_text(
                from_phone="+525500000001",
                to_phone="+525500000002",
                body="Hola",
            )

    @patch("messaging.ycloud_api.requests.post")
    def test_network_error_is_controlled_and_redacted(self, mock_post):
        mock_post.side_effect = requests.Timeout(
            "timeout X-API-Key: ycloud_test_secret_123"
        )
        with self.assertRaises(YCloudWhatsAppHTTPError) as context:
            send_ycloud_whatsapp_text(
                from_phone="+525500000001",
                to_phone="+525500000002",
                body="Hola",
            )
        self.assertNotIn("ycloud_test_secret_123", str(context.exception))

    @patch("messaging.ycloud_api.requests.post")
    def test_http_errors_are_controlled(self, mock_post):
        for status_code in (400, 401, 403, 404, 429, 500, 503):
            with self.subTest(status_code=status_code):
                response = MagicMock()
                response.ok = False
                response.status_code = status_code
                response.json.return_value = {
                    "error": {
                        "code": "TEST_ERROR",
                        "message": "Solicitud inválida",
                    }
                }
                mock_post.return_value = response
                with self.assertRaises(YCloudWhatsAppHTTPError) as context:
                    send_ycloud_whatsapp_text(
                        from_phone="+525500000001",
                        to_phone="+525500000002",
                        body="Hola",
                    )
                self.assertIn(f"HTTP {status_code}", str(context.exception))

    @patch("messaging.ycloud_api.requests.post")
    def test_non_json_error_does_not_expose_raw_body(self, mock_post):
        response = MagicMock()
        response.ok = False
        response.status_code = 502
        response.json.side_effect = ValueError("invalid json")
        response.text = "X-API-Key: secret_from_proxy"
        mock_post.return_value = response
        with self.assertRaises(YCloudWhatsAppHTTPError) as context:
            send_ycloud_whatsapp_text(
                from_phone="+525500000001",
                to_phone="+525500000002",
                body="Hola",
            )
        self.assertNotIn("secret_from_proxy", str(context.exception))
