from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from messaging.models import (
    Message,
    WhatsAppNotificationLog,
)

from messaging.services import (
    crear_notificacion_whatsapp_pedido,
)

from messaging.whatsapp_gateway import (
    WhatsAppInsufficientCredits,
    WhatsAppProviderError,
)


class WhatsAppNotificationGatewayTests(SimpleTestCase):
    """
    Pruebas del motor central:

        crear_notificacion_whatsapp_pedido()

    IMPORTANTE:
    - No usa base de datos.
    - No llama a Twilio.
    - No llama a Meta.
    - No consume créditos.
    - No crea pedidos reales.

    Se prueban las decisiones de negocio y el Gateway.
    """

    def setUp(self):
        # ======================================================
        # COMPANY / ORDER SIMULADOS
        # ======================================================

        self.company = SimpleNamespace(
            id=100,
            pk=100,
            name="WMS360 Test Company",
        )

        self.order = SimpleNamespace(
            id=500,
            pk=500,
            folio="TEST-500",
            company=self.company,
        )

        # ======================================================
        # CANAL SIMULADO
        # ======================================================

        self.whatsapp_number = SimpleNamespace(
            id=200,
            pk=200,
            company_id=100,
            warehouse_id=300,
            provider="twilio",
            is_active=True,
            PROVIDER_TWILIO="twilio",
            PROVIDER_META="meta",
        )

        # ======================================================
        # CLIENTE / CONVERSACIÓN
        # ======================================================

        self.customer = SimpleNamespace(
            id=400,
            pk=400,
            phone="+525511111111",
        )

        self.conversation = SimpleNamespace(
            id=600,
            pk=600,
            whatsapp_number=self.whatsapp_number,
            customer=self.customer,
        )

    # ==========================================================
    # HELPERS
    # ==========================================================

    def make_message(self):
        message = SimpleNamespace(
            provider_message_id=None,
            status=Message.STATUS_SENT,
            raw_payload={
                "source": "test_source",
                "event": "payment_approved",
                "order_id": self.order.id,
            },
            provider_timestamp=None,
            save=MagicMock(),
        )

        return message

    def make_log(self):
        log = SimpleNamespace(
            status=WhatsAppNotificationLog.STATUS_CREATED,
            provider="local",
            provider_message_id=None,
            error_message="",
            raw_payload={
                "source": "test_source",
                "event": "payment_approved",
                "order_id": self.order.id,
            },
            save=MagicMock(),
        )

        return log

    # ==========================================================
    # TEST 1
    # EVENTO INTERNO NUNCA LLAMA AL GATEWAY
    # ==========================================================

    @patch(
        "messaging.services.send_whatsapp_text"
    )
    @patch(
        "messaging.services.is_whatsapp_channel_ready"
    )
    @patch(
        "messaging.models.WhatsAppNotificationLog.objects.create"
    )
    @patch(
        "messaging.models.Message.objects.create"
    )
    @patch(
        "messaging.services.notification_log_already_exists",
        return_value=False,
    )
    @patch(
        "messaging.services.message_already_exists",
        return_value=False,
    )
    @patch(
        "messaging.services.obtener_conversacion_desde_pedido"
    )
    def test_internal_event_never_calls_gateway(
        self,
        mock_conversation,
        mock_message_exists,
        mock_log_exists,
        mock_message_create,
        mock_log_create,
        mock_ready,
        mock_send,
    ):
        mock_conversation.return_value = self.conversation

        message = self.make_message()
        log = self.make_log()

        mock_message_create.return_value = message
        mock_log_create.return_value = log

        result = crear_notificacion_whatsapp_pedido(
            order=self.order,
            body="Picking generado",
            source="warehouse",
            event="picking_generated",
            send_real=False,
        )

        self.assertIs(
            result,
            message,
        )

        mock_ready.assert_not_called()
        mock_send.assert_not_called()

        create_kwargs = (
            mock_message_create.call_args.kwargs
        )

        self.assertEqual(
            create_kwargs["direction"],
            Message.DIRECTION_SYSTEM,
        )

        log_kwargs = (
            mock_log_create.call_args.kwargs
        )

        self.assertEqual(
            log_kwargs["status"],
            WhatsAppNotificationLog.STATUS_SKIPPED,
        )

    # ==========================================================
    # TEST 2
    # EVENTO NO AUTORIZADO FUERZA SEND_REAL FALSE
    # ==========================================================

    @patch(
        "messaging.services.send_whatsapp_text"
    )
    @patch(
        "messaging.services.is_whatsapp_channel_ready"
    )
    @patch(
        "messaging.models.WhatsAppNotificationLog.objects.create"
    )
    @patch(
        "messaging.models.Message.objects.create"
    )
    @patch(
        "messaging.services.notification_log_already_exists",
        return_value=False,
    )
    @patch(
        "messaging.services.message_already_exists",
        return_value=False,
    )
    @patch(
        "messaging.services.obtener_conversacion_desde_pedido"
    )
    def test_non_allowed_event_forces_internal_mode(
        self,
        mock_conversation,
        mock_message_exists,
        mock_log_exists,
        mock_message_create,
        mock_log_create,
        mock_ready,
        mock_send,
    ):
        mock_conversation.return_value = self.conversation

        message = self.make_message()
        log = self.make_log()

        mock_message_create.return_value = message
        mock_log_create.return_value = log

        crear_notificacion_whatsapp_pedido(
            order=self.order,
            body="Evento no autorizado",
            source="test",
            event="evento_inventado",
            send_real=True,
        )

        mock_ready.assert_not_called()
        mock_send.assert_not_called()

        message_kwargs = (
            mock_message_create.call_args.kwargs
        )

        self.assertEqual(
            message_kwargs["direction"],
            Message.DIRECTION_SYSTEM,
        )

        self.assertFalse(
            message_kwargs["raw_payload"]["send_real"]
        )

    # ==========================================================
    # TEST 3
    # PAYMENT_APPROVED PUEDE SALIR REAL
    # ==========================================================

    @patch(
        "messaging.services.send_whatsapp_text"
    )
    @patch(
        "messaging.services.is_whatsapp_channel_ready",
        return_value=True,
    )
    @patch(
        "messaging.models.WhatsAppNotificationLog.objects.create"
    )
    @patch(
        "messaging.models.Message.objects.create"
    )
    @patch(
        "messaging.services.notification_log_already_exists",
        return_value=False,
    )
    @patch(
        "messaging.services.message_already_exists",
        return_value=False,
    )
    @patch(
        "messaging.services.obtener_conversacion_desde_pedido"
    )
    @patch(
        "messaging.services.reserve_whatsapp_message"
    )
    def test_payment_approved_uses_gateway(
        self,
        mock_reserve,
        mock_conversation,
        mock_message_exists,
        mock_log_exists,
        mock_message_create,
        mock_log_create,
        mock_ready,
        mock_send,
    ):
        mock_conversation.return_value = self.conversation

        message = self.make_message()
        log = self.make_log()

        mock_message_create.return_value = message
        mock_log_create.return_value = log

        mock_send.return_value = {
            "provider": "twilio",
            "provider_message_id": "SM_PAYMENT_001",
            "status": "queued",
            "raw_response": {
                "sid": "SM_PAYMENT_001",
            },
        }

        crear_notificacion_whatsapp_pedido(
            order=self.order,
            body="Pago aprobado",
            source="payments",
            event="payment_approved",
            send_real=True,
        )

        mock_ready.assert_called_once_with(
            self.whatsapp_number
        )
        mock_send.assert_called_once_with(
            whatsapp_number=self.whatsapp_number,
            to_phone="525511111111",
            body="Pago aprobado",
        )

    # ==========================================================
    # TEST 4
    # ORDER_SHIPPED PUEDE SALIR REAL
    # ==========================================================

    @patch(
        "messaging.services.send_whatsapp_text"
    )
    @patch(
        "messaging.services.is_whatsapp_channel_ready",
        return_value=True,
    )
    @patch(
        "messaging.models.WhatsAppNotificationLog.objects.create"
    )
    @patch(
        "messaging.models.Message.objects.create"
    )
    @patch(
        "messaging.services.notification_log_already_exists",
        return_value=False,
    )
    @patch(
        "messaging.services.message_already_exists",
        return_value=False,
    )
    @patch(
        "messaging.services.obtener_conversacion_desde_pedido"
    )
    @patch(
        "messaging.services.reserve_whatsapp_message"
    )
    def test_order_shipped_uses_gateway(
        self,
        mock_reserve,
        mock_conversation,
        mock_message_exists,
        mock_log_exists,
        mock_message_create,
        mock_log_create,
        mock_ready,
        mock_send,
    ):
        mock_conversation.return_value = self.conversation

        mock_message_create.return_value = (
            self.make_message()
        )

        mock_log_create.return_value = (
            self.make_log()
        )

        mock_send.return_value = {
            "provider": "twilio",
            "provider_message_id": "SM_SHIP_001",
            "status": "queued",
            "raw_response": {},
        }

        crear_notificacion_whatsapp_pedido(
            order=self.order,
            body="Tu pedido fue enviado",
            source="warehouse",
            event="order_shipped",
            send_real=True,
        )

        mock_send.assert_called_once()

    # ==========================================================
    # TEST 5
    # ORDER_INITIAL_MESSAGE PUEDE SALIR REAL
    # ==========================================================

    @patch(
        "messaging.services.send_whatsapp_text"
    )
    @patch(
        "messaging.services.is_whatsapp_channel_ready",
        return_value=True,
    )
    @patch(
        "messaging.models.WhatsAppNotificationLog.objects.create"
    )
    @patch(
        "messaging.models.Message.objects.create"
    )
    @patch(
        "messaging.services.notification_log_already_exists",
        return_value=False,
    )
    @patch(
        "messaging.services.message_already_exists",
        return_value=False,
    )
    @patch(
        "messaging.services.obtener_conversacion_desde_pedido"
    )
    @patch(
        "messaging.services.reserve_whatsapp_message"
    )
    def test_manual_initial_message_uses_gateway(
        self,
        mock_reserve,
        mock_conversation,
        mock_message_exists,
        mock_log_exists,
        mock_message_create,
        mock_log_create,
        mock_ready,
        mock_send,
    ):
        mock_conversation.return_value = self.conversation

        mock_message_create.return_value = (
            self.make_message()
        )

        mock_log_create.return_value = (
            self.make_log()
        )

        mock_send.return_value = {
            "provider": "twilio",
            "provider_message_id": "SM_INITIAL_001",
            "status": "queued",
            "raw_response": {},
        }

        crear_notificacion_whatsapp_pedido(
            order=self.order,
            body="Mensaje inicial",
            source="sales_order_initial_message",
            event="order_initial_message",
            send_real=True,
        )

        mock_send.assert_called_once()

    # ==========================================================
    # TEST 6
    # PROVEEDOR NO LISTO = DEMO / LOCAL
    # ==========================================================

    @patch(
        "messaging.services.send_whatsapp_text"
    )
    @patch(
        "messaging.services.is_whatsapp_channel_ready",
        return_value=False,
    )
    @patch(
        "messaging.models.WhatsAppNotificationLog.objects.create"
    )
    @patch(
        "messaging.models.Message.objects.create"
    )
    @patch(
        "messaging.services.notification_log_already_exists",
        return_value=False,
    )
    @patch(
        "messaging.services.message_already_exists",
        return_value=False,
    )
    @patch(
        "messaging.services.obtener_conversacion_desde_pedido"
    )
    def test_provider_not_ready_stays_demo_local(
        self,
        mock_conversation,
        mock_message_exists,
        mock_log_exists,
        mock_message_create,
        mock_log_create,
        mock_ready,
        mock_send,
    ):
        mock_conversation.return_value = self.conversation

        message = self.make_message()
        log = self.make_log()

        mock_message_create.return_value = message
        mock_log_create.return_value = log

        result = crear_notificacion_whatsapp_pedido(
            order=self.order,
            body="Pago aprobado",
            source="payments",
            event="payment_approved",
            send_real=True,
        )

        self.assertIs(
            result,
            message,
        )

        mock_send.assert_not_called()

        self.assertEqual(
            message.raw_payload[
                "notification_status"
            ],
            "demo",
        )

        self.assertEqual(
            message.raw_payload["provider"],
            "local",
        )

        self.assertEqual(
            log.status,
            WhatsAppNotificationLog.STATUS_DEMO,
        )

        self.assertEqual(
            log.provider,
            "local",
        )

        message.save.assert_called_once()
        log.save.assert_called_once()

    # ==========================================================
    # TEST 7
    # ÉXITO GUARDA PROVIDER_MESSAGE_ID
    # ==========================================================

    @patch(
        "messaging.services.send_whatsapp_text"
    )
    @patch(
        "messaging.services.is_whatsapp_channel_ready",
        return_value=True,
    )
    @patch(
        "messaging.models.WhatsAppNotificationLog.objects.create"
    )
    @patch(
        "messaging.models.Message.objects.create"
    )
    @patch(
        "messaging.services.notification_log_already_exists",
        return_value=False,
    )
    @patch(
        "messaging.services.message_already_exists",
        return_value=False,
    )
    @patch(
        "messaging.services.obtener_conversacion_desde_pedido"
    )
    @patch(
        "messaging.services.reserve_whatsapp_message"
    )
    def test_success_saves_neutral_provider_data(
        self,
        mock_reserve,
        mock_conversation,
        mock_message_exists,
        mock_log_exists,
        mock_message_create,
        mock_log_create,
        mock_ready,
        mock_send,
    ):
        mock_conversation.return_value = self.conversation

        message = self.make_message()
        log = self.make_log()

        mock_message_create.return_value = message
        mock_log_create.return_value = log

        mock_send.return_value = {
            "provider": "twilio",
            "provider_message_id": "SM_NEUTRAL_001",
            "status": "queued",
            "raw_response": {
                "sid": "SM_NEUTRAL_001",
            },
        }

        crear_notificacion_whatsapp_pedido(
            order=self.order,
            body="Pago aprobado",
            source="payments",
            event="payment_approved",
            send_real=True,
        )

        self.assertEqual(
            message.provider_message_id,
            "SM_NEUTRAL_001",
        )

        self.assertEqual(
            message.raw_payload["provider"],
            "twilio",
        )

        self.assertEqual(
            message.raw_payload[
                "provider_status"
            ],
            "queued",
        )

        self.assertEqual(
            message.raw_payload[
                "provider_message_id"
            ],
            "SM_NEUTRAL_001",
        )

        self.assertIn(
            "provider_response",
            message.raw_payload,
        )

        self.assertEqual(
            log.provider,
            "twilio",
        )

        self.assertEqual(
            log.provider_message_id,
            "SM_NEUTRAL_001",
        )

        self.assertEqual(
            log.status,
            WhatsAppNotificationLog.STATUS_SENT,
        )

    # ==========================================================
    # TEST 8
    # CRÉDITOS INSUFICIENTES
    # ==========================================================

    @patch(
        "messaging.services.send_whatsapp_text"
    )
    @patch(
        "messaging.services.is_whatsapp_channel_ready",
        return_value=True,
    )
    @patch(
        "messaging.models.WhatsAppNotificationLog.objects.create"
    )
    @patch(
        "messaging.models.Message.objects.create"
    )
    @patch(
        "messaging.services.notification_log_already_exists",
        return_value=False,
    )
    @patch(
        "messaging.services.message_already_exists",
        return_value=False,
    )
    @patch(
        "messaging.services.obtener_conversacion_desde_pedido"
    )
    @patch(
        "messaging.services.reserve_whatsapp_message"
    )
    def test_insufficient_credits_marks_failed(
        self,
        mock_reserve,
        mock_conversation,
        mock_message_exists,
        mock_log_exists,
        mock_message_create,
        mock_log_create,
        mock_ready,
        mock_send,
    ):
        mock_conversation.return_value = self.conversation

        message = self.make_message()
        log = self.make_log()

        mock_message_create.return_value = message
        mock_log_create.return_value = log

        mock_send.side_effect = (
            WhatsAppInsufficientCredits(
                "Créditos insuficientes."
            )
        )

        crear_notificacion_whatsapp_pedido(
            order=self.order,
            body="Pago aprobado",
            source="payments",
            event="payment_approved",
            send_real=True,
        )

        self.assertEqual(
            message.status,
            Message.STATUS_FAILED,
        )

        self.assertEqual(
            message.raw_payload[
                "notification_status"
            ],
            "failed",
        )

        self.assertEqual(
            message.raw_payload[
                "provider_status"
            ],
            "failed",
        )

        self.assertEqual(
            log.status,
            WhatsAppNotificationLog.STATUS_FAILED,
        )

        self.assertIn(
            "Créditos insuficientes",
            log.error_message,
        )

    # ==========================================================
    # TEST 9
    # ERROR CONTROLADO DEL GATEWAY
    # ==========================================================

    @patch(
        "messaging.services.send_whatsapp_text"
    )
    @patch(
        "messaging.services.is_whatsapp_channel_ready",
        return_value=True,
    )
    @patch(
        "messaging.models.WhatsAppNotificationLog.objects.create"
    )
    @patch(
        "messaging.models.Message.objects.create"
    )
    @patch(
        "messaging.services.notification_log_already_exists",
        return_value=False,
    )
    @patch(
        "messaging.services.message_already_exists",
        return_value=False,
    )
    @patch(
        "messaging.services.obtener_conversacion_desde_pedido"
    )
    @patch(
        "messaging.services.reserve_whatsapp_message"
    )
    def test_provider_error_marks_failed(
        self,
        mock_reserve,
        mock_conversation,
        mock_message_exists,
        mock_log_exists,
        mock_message_create,
        mock_log_create,
        mock_ready,
        mock_send,
    ):
        mock_conversation.return_value = self.conversation

        message = self.make_message()
        log = self.make_log()

        mock_message_create.return_value = message
        mock_log_create.return_value = log

        mock_send.side_effect = (
            WhatsAppProviderError(
                "Proveedor temporalmente no disponible."
            )
        )

        crear_notificacion_whatsapp_pedido(
            order=self.order,
            body="Pedido enviado",
            source="warehouse",
            event="order_shipped",
            send_real=True,
        )

        self.assertEqual(
            message.status,
            Message.STATUS_FAILED,
        )

        self.assertEqual(
            log.status,
            WhatsAppNotificationLog.STATUS_FAILED,
        )

        self.assertIn(
            "Proveedor temporalmente",
            log.error_message,
        )

    # ==========================================================
    # TEST 10
    # MESSAGE DUPLICADO NO SE VUELVE A ENVIAR
    # ==========================================================

    @patch(
        "messaging.services.send_whatsapp_text"
    )
    @patch(
        "messaging.models.WhatsAppNotificationLog.objects.create"
    )
    @patch(
        "messaging.models.Message.objects.create"
    )
    @patch(
        "messaging.services.notification_log_already_exists",
        return_value=True,
    )
    @patch(
        "messaging.services.message_already_exists",
        return_value=True,
    )
    @patch(
        "messaging.services.obtener_conversacion_desde_pedido"
    )
    def test_duplicate_message_is_not_sent_again(
        self,
        mock_conversation,
        mock_message_exists,
        mock_log_exists,
        mock_message_create,
        mock_log_create,
        mock_send,
    ):
        mock_conversation.return_value = self.conversation

        result = crear_notificacion_whatsapp_pedido(
            order=self.order,
            body="Pago aprobado duplicado",
            source="payments",
            event="payment_approved",
            send_real=True,
        )

        self.assertIsNone(result)

        mock_send.assert_not_called()
        mock_message_create.assert_not_called()
        mock_log_create.assert_not_called()

    # ==========================================================
    # TEST 11
    # LOG DUPLICADO TAMPOCO GENERA OTRO ENVÍO
    # ==========================================================

    @patch(
        "messaging.services.send_whatsapp_text"
    )
    @patch(
        "messaging.models.WhatsAppNotificationLog.objects.create"
    )
    @patch(
        "messaging.models.Message.objects.create"
    )
    @patch(
        "messaging.services.notification_log_already_exists",
        return_value=True,
    )
    @patch(
        "messaging.services.message_already_exists",
        return_value=False,
    )
    @patch(
        "messaging.services.obtener_conversacion_desde_pedido"
    )
    def test_duplicate_log_is_not_sent_again(
        self,
        mock_conversation,
        mock_message_exists,
        mock_log_exists,
        mock_message_create,
        mock_log_create,
        mock_send,
    ):
        mock_conversation.return_value = self.conversation

        result = crear_notificacion_whatsapp_pedido(
            order=self.order,
            body="Pedido enviado duplicado",
            source="warehouse",
            event="order_shipped",
            send_real=True,
        )

        self.assertIsNone(result)

        mock_send.assert_not_called()
        mock_message_create.assert_not_called()
        mock_log_create.assert_not_called()

    # ==========================================================
    # TEST 12
    # SIN CONVERSACIÓN NO HAY ENVÍO NI REGISTROS
    # ==========================================================

    @patch(
        "messaging.services.send_whatsapp_text"
    )
    @patch(
        "messaging.models.WhatsAppNotificationLog.objects.create"
    )
    @patch(
        "messaging.models.Message.objects.create"
    )
    @patch(
        "messaging.services.obtener_conversacion_desde_pedido",
        return_value=None,
    )
    def test_without_conversation_stops_safely(
        self,
        mock_conversation,
        mock_message_create,
        mock_log_create,
        mock_send,
    ):
        result = crear_notificacion_whatsapp_pedido(
            order=self.order,
            body="No hay conversación",
            source="payments",
            event="payment_approved",
            send_real=True,
        )

        self.assertIsNone(result)

        mock_send.assert_not_called()
        mock_message_create.assert_not_called()
        mock_log_create.assert_not_called()
