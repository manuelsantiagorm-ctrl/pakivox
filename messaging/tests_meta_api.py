from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

from django.test import SimpleTestCase

from messaging.meta_api import (
    MetaWhatsAppConfigurationError,
    MetaWhatsAppHTTPError,
    clean_meta_exception_message,
    get_meta_credentials_from_channel,
    normalize_meta_api_version,
    normalize_meta_phone,
    send_meta_whatsapp_text,
)


class MetaWhatsAppAPITests(SimpleTestCase):
    """
    Pruebas aisladas de Meta Cloud API.

    No llaman a Internet.
    No llaman a Meta real.
    No usan base de datos.
    """

    def make_channel(
        self,
        *,
        company_id=1,
        warehouse_id=10,
        provider="meta",
        is_active=True,
        meta_enabled=True,
        phone_number_id="123456789012345",
        meta_access_token="EAA_TEST_TOKEN_1234567890",
        meta_api_version="v23.0",
    ):
        return SimpleNamespace(
            company_id=company_id,
            warehouse_id=warehouse_id,
            provider=provider,
            is_active=is_active,
            meta_enabled=meta_enabled,
            phone_number_id=phone_number_id,
            meta_access_token=meta_access_token,
            meta_api_version=meta_api_version,
            PROVIDER_META="meta",
        )

    # ==========================================================
    # NORMALIZACIÓN
    # ==========================================================

    def test_normalize_phone_removes_symbols(self):
        self.assertEqual(
            normalize_meta_phone("+52 55 1234 5678"),
            "525512345678",
        )

    def test_invalid_phone_is_rejected(self):
        with self.assertRaises(
            MetaWhatsAppConfigurationError
        ):
            normalize_meta_phone("123")

    def test_valid_api_version_is_accepted(self):
        self.assertEqual(
            normalize_meta_api_version("v23.0"),
            "v23.0",
        )

    def test_invalid_api_version_is_rejected(self):
        with self.assertRaises(
            MetaWhatsAppConfigurationError
        ):
            normalize_meta_api_version(
                "../v23.0"
            )

    # ==========================================================
    # CREDENCIALES / SEGURIDAD
    # ==========================================================

    def test_valid_meta_channel_returns_credentials(self):
        channel = self.make_channel()

        credentials = (
            get_meta_credentials_from_channel(
                channel
            )
        )

        self.assertEqual(
            credentials["phone_number_id"],
            "123456789012345",
        )

        self.assertEqual(
            credentials["api_version"],
            "v23.0",
        )

    def test_non_meta_channel_is_rejected(self):
        channel = self.make_channel(
            provider="twilio"
        )

        with self.assertRaises(
            MetaWhatsAppConfigurationError
        ):
            get_meta_credentials_from_channel(
                channel
            )

    def test_inactive_channel_is_rejected(self):
        channel = self.make_channel(
            is_active=False
        )

        with self.assertRaises(
            MetaWhatsAppConfigurationError
        ):
            get_meta_credentials_from_channel(
                channel
            )

    def test_disabled_meta_channel_is_rejected(self):
        channel = self.make_channel(
            meta_enabled=False
        )

        with self.assertRaises(
            MetaWhatsAppConfigurationError
        ):
            get_meta_credentials_from_channel(
                channel
            )

    def test_missing_phone_number_id_is_rejected(self):
        channel = self.make_channel(
            phone_number_id=""
        )

        with self.assertRaises(
            MetaWhatsAppConfigurationError
        ):
            get_meta_credentials_from_channel(
                channel
            )

    def test_non_numeric_phone_number_id_is_rejected(self):
        channel = self.make_channel(
            phone_number_id="abc123"
        )

        with self.assertRaises(
            MetaWhatsAppConfigurationError
        ):
            get_meta_credentials_from_channel(
                channel
            )

    def test_missing_access_token_is_rejected(self):
        channel = self.make_channel(
            meta_access_token=""
        )

        with self.assertRaises(
            MetaWhatsAppConfigurationError
        ):
            get_meta_credentials_from_channel(
                channel
            )

    # ==========================================================
    # REDACCIÓN
    # ==========================================================

    def test_bearer_token_is_redacted(self):
        cleaned = clean_meta_exception_message(
            "Authorization: Bearer EAA_SUPER_SECRET_TOKEN_123456"
        )

        self.assertNotIn(
            "EAA_SUPER_SECRET_TOKEN_123456",
            cleaned,
        )

        self.assertIn(
            "[REDACTED]",
            cleaned,
        )

    def test_access_token_is_redacted(self):
        cleaned = clean_meta_exception_message(
            "access_token=EAA_ANOTHER_SECRET_123456789"
        )

        self.assertNotIn(
            "EAA_ANOTHER_SECRET_123456789",
            cleaned,
        )

    # ==========================================================
    # ENVÍO EXITOSO
    # ==========================================================

    @patch(
        "messaging.meta_api.requests.post"
    )
    def test_successful_send_returns_neutral_result(
        self,
        mock_post,
    ):
        channel = self.make_channel()

        response = MagicMock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {
            "messaging_product": "whatsapp",
            "contacts": [
                {
                    "input": "525512345678",
                    "wa_id": "525512345678",
                }
            ],
            "messages": [
                {
                    "id": "wamid.TEST123"
                }
            ],
        }

        mock_post.return_value = response

        result = send_meta_whatsapp_text(
            whatsapp_number=channel,
            to_phone="+52 55 1234 5678",
            body="Hola desde WMS360",
        )

        self.assertEqual(
            result["provider"],
            "meta",
        )

        self.assertEqual(
            result["provider_message_id"],
            "wamid.TEST123",
        )

        self.assertEqual(
            result["status"],
            "sent",
        )

        self.assertEqual(
            result["raw_response"],
            {
                "messaging_product": "whatsapp",
                "messages": [
                    {
                        "id": "wamid.TEST123"
                    }
                ],
            },
        )

        mock_post.assert_called_once()

        call_kwargs = (
            mock_post.call_args.kwargs
        )

        self.assertEqual(
            call_kwargs["json"]["to"],
            "525512345678",
        )

        self.assertEqual(
            call_kwargs["json"]["type"],
            "text",
        )

        self.assertEqual(
            call_kwargs["json"]["text"]["body"],
            "Hola desde WMS360",
        )

    # ==========================================================
    # ERRORES HTTP / META
    # ==========================================================

    @patch(
        "messaging.meta_api.requests.post"
    )
    def test_meta_http_error_is_safely_wrapped(
        self,
        mock_post,
    ):
        channel = self.make_channel()

        response = MagicMock()
        response.ok = False
        response.status_code = 400
        response.json.return_value = {
            "error": {
                "message": "Invalid parameter",
                "code": 100,
                "error_subcode": 2494010,
            }
        }

        mock_post.return_value = response

        with self.assertRaises(
            MetaWhatsAppHTTPError
        ) as context:
            send_meta_whatsapp_text(
                whatsapp_number=channel,
                to_phone="525512345678",
                body="Hola",
            )

        error_text = str(
            context.exception
        )

        self.assertIn(
            "Invalid parameter",
            error_text,
        )

        self.assertIn(
            "HTTP 400",
            error_text,
        )

    @patch(
        "messaging.meta_api.requests.post"
    )
    def test_timeout_is_wrapped(
        self,
        mock_post,
    ):
        channel = self.make_channel()

        mock_post.side_effect = (
            requests.Timeout()
        )

        with self.assertRaises(
            MetaWhatsAppHTTPError
        ):
            send_meta_whatsapp_text(
                whatsapp_number=channel,
                to_phone="525512345678",
                body="Hola",
            )

    @patch(
        "messaging.meta_api.requests.post"
    )
    def test_connection_error_is_wrapped(
        self,
        mock_post,
    ):
        channel = self.make_channel()

        mock_post.side_effect = (
            requests.ConnectionError()
        )

        with self.assertRaises(
            MetaWhatsAppHTTPError
        ):
            send_meta_whatsapp_text(
                whatsapp_number=channel,
                to_phone="525512345678",
                body="Hola",
            )

    @patch(
        "messaging.meta_api.requests.post"
    )
    def test_invalid_json_is_rejected(
        self,
        mock_post,
    ):
        channel = self.make_channel()

        response = MagicMock()
        response.ok = True
        response.status_code = 200
        response.json.side_effect = ValueError(
            "invalid json"
        )

        mock_post.return_value = response

        with self.assertRaises(
            MetaWhatsAppHTTPError
        ):
            send_meta_whatsapp_text(
                whatsapp_number=channel,
                to_phone="525512345678",
                body="Hola",
            )

    @patch(
        "messaging.meta_api.requests.post"
    )
    def test_missing_message_id_is_rejected(
        self,
        mock_post,
    ):
        channel = self.make_channel()

        response = MagicMock()
        response.ok = True
        response.status_code = 200
        response.json.return_value = {
            "messaging_product": "whatsapp",
            "messages": [],
        }

        mock_post.return_value = response

        with self.assertRaises(
            MetaWhatsAppHTTPError
        ):
            send_meta_whatsapp_text(
                whatsapp_number=channel,
                to_phone="525512345678",
                body="Hola",
            )

    # ==========================================================
    # BODY VACÍO
    # ==========================================================

    @patch(
        "messaging.meta_api.requests.post"
    )
    def test_empty_body_is_rejected_before_http(
        self,
        mock_post,
    ):
        channel = self.make_channel()

        with self.assertRaises(
            MetaWhatsAppConfigurationError
        ):
            send_meta_whatsapp_text(
                whatsapp_number=channel,
                to_phone="525512345678",
                body="   ",
            )

        mock_post.assert_not_called()
