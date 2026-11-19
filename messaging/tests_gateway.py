from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from messaging.twilio_api import (
    InsufficientWhatsAppCredits,
    TwilioWhatsAppError,
)

from messaging.whatsapp_gateway import (
    WhatsAppInsufficientCredits,
    WhatsAppProviderError,
    WhatsAppProviderNotSupported,
    extract_provider_message_id,
    send_whatsapp_text,
)


class WhatsAppGatewayTests(SimpleTestCase):
    """
    Pruebas del Gateway neutral de WhatsApp.

    IMPORTANTE:
    - No se conecta a Twilio.
    - No se conecta a Meta.
    - No usa números reales.
    - No consume créditos.
    - No modifica la base de datos.

    Objetivo:
        views.py / services.py
                  |
                  v
          whatsapp_gateway.py
                  |
            +-----+-----+
            |           |
          Twilio       Meta
    """

    # ==========================================================
    # HELPERS
    # ==========================================================

    def make_channel(
        self,
        *,
        provider="twilio",
        is_active=True,
        company_id=100,
        warehouse_id=200,
    ):
        """
        Crea un objeto simulado con solamente los campos
        que necesita el Gateway.

        No crea registros en base de datos.
        """

        return SimpleNamespace(
            id=999,
            company_id=company_id,
            warehouse_id=warehouse_id,
            provider=provider,
            is_active=is_active,
            PROVIDER_TWILIO="twilio",
            PROVIDER_META="meta",
        )

    # ==========================================================
    # TEST 1
    # EXTRAER ID DE RESPUESTA NORMALIZADA
    # ==========================================================

    def test_extract_provider_message_id(self):
        result = {
            "provider_message_id": "MSG-001",
        }

        self.assertEqual(
            extract_provider_message_id(result),
            "MSG-001",
        )

    # ==========================================================
    # TEST 2
    # COMPATIBILIDAD CON SID LEGACY DE TWILIO
    # ==========================================================

    def test_extract_provider_message_id_from_twilio_sid(self):
        result = {
            "sid": "SM123456789",
        }

        self.assertEqual(
            extract_provider_message_id(result),
            "SM123456789",
        )

    # ==========================================================
    # TEST 3
    # TWILIO SE ENRUTA CORRECTAMENTE
    # ==========================================================

    @patch(
        "messaging.twilio_api.send_twilio_whatsapp_text"
    )
    def test_twilio_channel_routes_through_twilio(
        self,
        mock_send,
    ):
        channel = self.make_channel(
            provider="twilio",
        )

        mock_send.return_value = {
            "sid": "SM_TEST_001",
            "status": "queued",
        }

        result = send_whatsapp_text(
            whatsapp_number=channel,
            to_phone="+525500000001",
            body="Mensaje de prueba",
        )

        mock_send.assert_called_once_with(
            whatsapp_number=channel,
            to_phone="+525500000001",
            body="Mensaje de prueba",
        )

        self.assertEqual(
            result["provider"],
            "twilio",
        )

        self.assertEqual(
            result["provider_message_id"],
            "SM_TEST_001",
        )

        self.assertEqual(
            result["status"],
            "queued",
        )

    # ==========================================================
    # TEST 4
    # RESPUESTA TWILIO SE NORMALIZA
    # ==========================================================

    @patch(
        "messaging.twilio_api.send_twilio_whatsapp_text"
    )
    def test_twilio_result_is_normalized(
        self,
        mock_send,
    ):
        channel = self.make_channel()

        mock_send.return_value = {
            "message_sid": "SM_TEST_002",
        }

        result = send_whatsapp_text(
            whatsapp_number=channel,
            to_phone="+525500000002",
            body="Hola",
        )

        self.assertEqual(
            result,
            {
                "provider": "twilio",
                "provider_message_id": "SM_TEST_002",
                "status": "sent",
                "raw_response": {
                    "message_sid": "SM_TEST_002",
                },
            },
        )

    # ==========================================================
    # TEST 5
    # CRÉDITOS INSUFICIENTES SE VUELVEN ERROR NEUTRAL
    # ==========================================================

    @patch(
        "messaging.twilio_api.send_twilio_whatsapp_text"
    )
    def test_twilio_insufficient_credits_is_wrapped(
        self,
        mock_send,
    ):
        channel = self.make_channel()

        mock_send.side_effect = (
            InsufficientWhatsAppCredits(
                "Sin créditos disponibles."
            )
        )

        with self.assertRaises(
            WhatsAppInsufficientCredits
        ):
            send_whatsapp_text(
                whatsapp_number=channel,
                to_phone="+525500000003",
                body="Hola",
            )

    # ==========================================================
    # TEST 6
    # ERROR TWILIO NO ESCAPA HACIA EL RESTO DEL SISTEMA
    # ==========================================================

    @patch(
        "messaging.twilio_api.send_twilio_whatsapp_text"
    )
    def test_twilio_error_is_wrapped(
        self,
        mock_send,
    ):
        channel = self.make_channel()

        mock_send.side_effect = TwilioWhatsAppError(
            "Error controlado Twilio."
        )

        with self.assertRaises(
            WhatsAppProviderError
        ):
            send_whatsapp_text(
                whatsapp_number=channel,
                to_phone="+525500000004",
                body="Hola",
            )

    # ==========================================================
    # TEST 7
    # ERROR INESPERADO DEL SDK TAMPOCO ESCAPA
    # ==========================================================

    @patch(
        "messaging.twilio_api.send_twilio_whatsapp_text"
    )
    def test_unexpected_provider_error_is_wrapped(
        self,
        mock_send,
    ):
        channel = self.make_channel()

        mock_send.side_effect = RuntimeError(
            "Fallo inesperado del SDK."
        )

        with self.assertRaises(
            WhatsAppProviderError
        ):
            send_whatsapp_text(
                whatsapp_number=channel,
                to_phone="+525500000005",
                body="Hola",
            )

    # ==========================================================
    # TEST 8
    # META PERMANECE BLOQUEADO HASTA IMPLEMENTACIÓN REAL
    # ==========================================================

    def test_meta_is_safely_blocked_for_now(self):
        channel = self.make_channel(
            provider="meta",
        )

        with self.assertRaises(
            WhatsAppProviderError
        ) as context:
            send_whatsapp_text(
                whatsapp_number=channel,
                to_phone="+525500000006",
                body="Hola Meta",
            )

        self.assertIn(
            "Meta",
            str(context.exception),
        )

    # ==========================================================
    # TEST 9
    # PROVEEDOR DESCONOCIDO ES RECHAZADO
    # ==========================================================

    def test_unknown_provider_is_rejected(self):
        channel = self.make_channel(
            provider="proveedor_inventado",
        )

        with self.assertRaises(
            WhatsAppProviderNotSupported
        ):
            send_whatsapp_text(
                whatsapp_number=channel,
                to_phone="+525500000007",
                body="Hola",
            )

    # ==========================================================
    # TEST 10
    # CANAL INACTIVO NO PUEDE ENVIAR
    # ==========================================================

    @patch(
        "messaging.twilio_api.send_twilio_whatsapp_text"
    )
    def test_inactive_channel_is_rejected_before_provider(
        self,
        mock_send,
    ):
        channel = self.make_channel(
            is_active=False,
        )

        with self.assertRaises(
            WhatsAppProviderError
        ):
            send_whatsapp_text(
                whatsapp_number=channel,
                to_phone="+525500000008",
                body="No debe salir",
            )

        mock_send.assert_not_called()

    # ==========================================================
    # TEST 11
    # CANAL SIN COMPANY ES RECHAZADO
    # ==========================================================

    @patch(
        "messaging.twilio_api.send_twilio_whatsapp_text"
    )
    def test_channel_without_company_is_rejected(
        self,
        mock_send,
    ):
        channel = self.make_channel(
            company_id=None,
        )

        with self.assertRaises(
            WhatsAppProviderError
        ):
            send_whatsapp_text(
                whatsapp_number=channel,
                to_phone="+525500000009",
                body="No debe salir",
            )

        mock_send.assert_not_called()

    # ==========================================================
    # TEST 12
    # CANAL SIN BODEGA ES RECHAZADO
    # ==========================================================

    @patch(
        "messaging.twilio_api.send_twilio_whatsapp_text"
    )
    def test_channel_without_warehouse_is_rejected(
        self,
        mock_send,
    ):
        channel = self.make_channel(
            warehouse_id=None,
        )

        with self.assertRaises(
            WhatsAppProviderError
        ):
            send_whatsapp_text(
                whatsapp_number=channel,
                to_phone="+525500000010",
                body="No debe salir",
            )

        mock_send.assert_not_called()

    # ==========================================================
    # TEST 13
    # MENSAJE VACÍO ES RECHAZADO
    # ==========================================================

    @patch(
        "messaging.twilio_api.send_twilio_whatsapp_text"
    )
    def test_empty_message_is_rejected(
        self,
        mock_send,
    ):
        channel = self.make_channel()

        with self.assertRaises(
            WhatsAppProviderError
        ):
            send_whatsapp_text(
                whatsapp_number=channel,
                to_phone="+525500000011",
                body="   ",
            )

        mock_send.assert_not_called()

    # ==========================================================
    # TEST 14
    # DESTINO VACÍO ES RECHAZADO
    # ==========================================================

    @patch(
        "messaging.twilio_api.send_twilio_whatsapp_text"
    )
    def test_empty_destination_is_rejected(
        self,
        mock_send,
    ):
        channel = self.make_channel()

        with self.assertRaises(
            WhatsAppProviderError
        ):
            send_whatsapp_text(
                whatsapp_number=channel,
                to_phone="",
                body="Hola",
            )

        mock_send.assert_not_called()

    # ==========================================================
    # TEST 15
    # NO SE RECIBIÓ CANAL
    # ==========================================================

    @patch(
        "messaging.twilio_api.send_twilio_whatsapp_text"
    )
    def test_missing_channel_is_rejected(
        self,
        mock_send,
    ):
        with self.assertRaises(
            WhatsAppProviderError
        ):
            send_whatsapp_text(
                whatsapp_number=None,
                to_phone="+525500000012",
                body="Hola",
            )

        mock_send.assert_not_called()
