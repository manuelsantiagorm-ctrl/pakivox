from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from messaging.meta_api import (
    MetaWhatsAppHTTPError,
)

from messaging.whatsapp_gateway import (
    WhatsAppProviderError,
    _normalize_provider_result,
    clean_whatsapp_exception_message,
    is_whatsapp_channel_ready,
    send_whatsapp_text,
)


class WhatsAppGatewayMetaEnabledTests(SimpleTestCase):
    """
    Integración aislada:

        whatsapp_gateway.py
                ↓
            meta_api.py

    No realiza llamadas reales a Internet.
    """

    def make_meta_channel(
        self,
        *,
        is_meta_ready=True,
        is_active=True,
        company_id=1,
        warehouse_id=10,
    ):
        return SimpleNamespace(
            company_id=company_id,
            warehouse_id=warehouse_id,
            provider="meta",
            is_active=is_active,
            is_meta_ready=is_meta_ready,
            is_twilio_ready=False,
            PROVIDER_META="meta",
            PROVIDER_TWILIO="twilio",
        )

    def make_twilio_channel(self):
        return SimpleNamespace(
            company_id=1,
            warehouse_id=10,
            provider="twilio",
            is_active=True,
            is_meta_ready=False,
            is_twilio_ready=True,
            PROVIDER_META="meta",
            PROVIDER_TWILIO="twilio",
        )

    # ======================================================
    # 1. META READY
    # ======================================================

    def test_ready_meta_channel_is_ready(self):
        channel = self.make_meta_channel(
            is_meta_ready=True,
        )

        self.assertTrue(
            is_whatsapp_channel_ready(
                channel
            )
        )

    # ======================================================
    # 2. META INCOMPLETO
    # ======================================================

    def test_incomplete_meta_channel_is_not_ready(self):
        channel = self.make_meta_channel(
            is_meta_ready=False,
        )

        self.assertFalse(
            is_whatsapp_channel_ready(
                channel
            )
        )

    # ======================================================
    # 3. RUTA META
    # ======================================================

    @patch(
        "messaging.meta_api.send_meta_whatsapp_text"
    )
    def test_meta_routes_through_meta_api(
        self,
        mock_send,
    ):
        channel = self.make_meta_channel()

        mock_send.return_value = {
            "provider": "meta",
            "provider_message_id": "wamid.GATEWAY001",
            "status": "sent",
            "raw_response": {
                "messaging_product": "whatsapp",
                "messages": [
                    {
                        "id": "wamid.GATEWAY001",
                    }
                ],
            },
        }

        result = send_whatsapp_text(
            whatsapp_number=channel,
            to_phone="525512345678",
            body="Hola desde Gateway",
        )

        mock_send.assert_called_once_with(
            whatsapp_number=channel,
            to_phone="525512345678",
            body="Hola desde Gateway",
        )

        self.assertEqual(
            result["provider"],
            "meta",
        )

        self.assertEqual(
            result["provider_message_id"],
            "wamid.GATEWAY001",
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
                        "id": "wamid.GATEWAY001",
                    }
                ],
            },
        )

    # ======================================================
    # 4. META INCOMPLETO NO LLEGA A HTTP
    # ======================================================

    @patch(
        "messaging.meta_api.send_meta_whatsapp_text"
    )
    def test_incomplete_meta_channel_is_blocked_before_api(
        self,
        mock_send,
    ):
        channel = self.make_meta_channel(
            is_meta_ready=False,
        )

        with self.assertRaises(
            WhatsAppProviderError
        ):
            send_whatsapp_text(
                whatsapp_number=channel,
                to_phone="525512345678",
                body="Hola",
            )

        mock_send.assert_not_called()

    # ======================================================
    # 5. ERROR META SE TRADUCE AL CONTRATO GENERAL
    # ======================================================

    @patch(
        "messaging.meta_api.send_meta_whatsapp_text"
    )
    def test_meta_error_is_wrapped_and_token_redacted(
        self,
        mock_send,
    ):
        channel = self.make_meta_channel()

        secret = (
            "EAA_SUPER_SECRET_TOKEN_123456789"
        )

        mock_send.side_effect = (
            MetaWhatsAppHTTPError(
                f"Authorization: Bearer {secret}"
            )
        )

        with self.assertRaises(
            WhatsAppProviderError
        ) as context:
            send_whatsapp_text(
                whatsapp_number=channel,
                to_phone="525512345678",
                body="Hola",
            )

        error_text = str(
            context.exception
        )

        self.assertNotIn(
            secret,
            error_text,
        )

        self.assertIn(
            "[REDACTED]",
            error_text,
        )

    # ======================================================
    # 6. ERROR INESPERADO TAMBIÉN REDACTA
    # ======================================================

    @patch(
        "messaging.meta_api.send_meta_whatsapp_text"
    )
    def test_unexpected_meta_error_is_safely_wrapped(
        self,
        mock_send,
    ):
        channel = self.make_meta_channel()

        secret = (
            "EAA_ANOTHER_SECRET_TOKEN_123456789"
        )

        mock_send.side_effect = RuntimeError(
            f"access_token={secret}"
        )

        with self.assertRaises(
            WhatsAppProviderError
        ) as context:
            send_whatsapp_text(
                whatsapp_number=channel,
                to_phone="525512345678",
                body="Hola",
            )

        error_text = str(
            context.exception
        )

        self.assertNotIn(
            secret,
            error_text,
        )

    # ======================================================
    # 7. TWILIO SIGUE ENRUTANDO IGUAL
    # ======================================================

    @patch(
        "messaging.whatsapp_gateway._send_with_twilio"
    )
    def test_twilio_routing_remains_unchanged(
        self,
        mock_twilio,
    ):
        channel = self.make_twilio_channel()

        mock_twilio.return_value = {
            "provider": "twilio",
            "provider_message_id": "SM_TEST",
            "status": "sent",
            "raw_response": {},
        }

        result = send_whatsapp_text(
            whatsapp_number=channel,
            to_phone="525512345678",
            body="Hola Twilio",
        )

        mock_twilio.assert_called_once_with(
            whatsapp_number=channel,
            to_phone="525512345678",
            body="Hola Twilio",
        )

        self.assertEqual(
            result["provider"],
            "twilio",
        )

    # ======================================================
    # 8. NO ANIDAR RESPUESTA NEUTRAL
    # ======================================================

    def test_neutral_result_keeps_safe_raw_response(self):
        result = _normalize_provider_result(
            provider="meta",
            result={
                "provider": "meta",
                "provider_message_id": "wamid.SAFE001",
                "status": "sent",
                "raw_response": {
                    "messages": [
                        {
                            "id": "wamid.SAFE001",
                        }
                    ]
                },
            },
        )

        self.assertEqual(
            result["provider_message_id"],
            "wamid.SAFE001",
        )

        self.assertEqual(
            result["raw_response"],
            {
                "messages": [
                    {
                        "id": "wamid.SAFE001",
                    }
                ]
            },
        )

        self.assertNotIn(
            "raw_response",
            result["raw_response"],
        )

    # ======================================================
    # 9. SANITIZADOR GENERAL
    # ======================================================

    def test_generic_gateway_cleaner_redacts_bearer_token(self):
        secret = (
            "EAA_GENERIC_SECRET_TOKEN_123456789"
        )

        cleaned = clean_whatsapp_exception_message(
            f"Bearer {secret}"
        )

        self.assertNotIn(
            secret,
            cleaned,
        )

        self.assertIn(
            "[REDACTED]",
            cleaned,
        )
