from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from accounts.models import (
    Capability,
    EmployeeCapabilityAssignment,
    UserProfile,
    UserWarehouseAccess,
)
from messaging.models import (
    ChannelAgentAssignment,
    Conversation,
    Message,
    MessagingCustomer,
    WhatsAppNumber,
)
from companies.models import Company
from inventory.models import Warehouse
from subscriptions.models import Plan, Subscription


from messaging.whatsapp_gateway import (
    WhatsAppInsufficientCredits,
    WhatsAppProviderError,
)


User = get_user_model()


class ChatReplyGatewayTests(TestCase):
    """
    Integración segura de chat_reply con WhatsApp Gateway.

    Estas pruebas:
    - NO llaman a Twilio.
    - NO llaman a Meta.
    - NO gastan créditos reales.
    - NO utilizan la base de datos real.
    - Utilizan una base temporal de Django.

    Aquí probamos:

        Usuario autenticado
               ↓
        Premium válido
               ↓
        Company del usuario
               ↓
        Conversación de esa company
               ↓
        Canal autorizado
               ↓
        Bodega válida
               ↓
        WhatsApp Gateway
    """

    def setUp(self):
        # ======================================================
        # 1. PLAN PREMIUM VÁLIDO DE PRUEBA
        # ======================================================

        self.plan = Plan.objects.create(
            name="WMS360 Premium Chat Reply Test",
            slug="wms360-premium-chat-reply-test",
            price="4500.00",
            billing_period="monthly",
            stripe_price_id="price_test_chat_reply_gateway",
            max_users=50,
            max_products=10000,
            max_warehouses=2,
            has_whatsapp=True,
            max_whatsapp_numbers=4,
            max_whatsapp_agents=16,
            max_whatsapp_numbers_per_warehouse=2,
            max_whatsapp_agents_per_number=4,
            can_create_orders_from_chat=True,
            can_supervise_whatsapp=True,
            has_multiwarehouse=True,
            is_active=True,
        )

        # ======================================================
        # 2. COMPANY A
        # ======================================================

        self.company_a = Company.objects.create(
            name="Gateway View Company A",
            slug="gateway-view-company-a",
            is_active=True,
        )

        # ======================================================
        # 3. USUARIO DE COMPANY A
        # ======================================================

        self.user = User.objects.create_user(
            username="gateway-view@test.local",
            email="gateway-view@test.local",
            password="Test123456!",
            is_active=True,
        )

        # update_or_create evita problemas si existe un signal
        # que crea automáticamente el perfil.
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={
                "company": self.company_a,
                "role": "warehouse",
                "is_active": True,
            },
        )

        # ======================================================
        # 4. SUSCRIPCIÓN PREMIUM REALMENTE VÁLIDA
        # ======================================================
        #
        # Los datos Stripe son ficticios y existen solamente
        # dentro de la DB temporal del test.
        # ======================================================

        today = timezone.localdate()

        self.subscription_a = Subscription.objects.create(
            company=self.company_a,
            plan=self.plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=today,
            end_date=today + timedelta(days=30),
            next_billing_date=today + timedelta(days=30),
            auto_renew=True,
            stripe_customer_id="cus_test_chat_reply_a",
            stripe_subscription_id="sub_test_chat_reply_a",
            stripe_verified_at=timezone.now(),
            stripe_last_paid_event_id="evt_test_chat_reply_a",
            stripe_verified_price_id=(
                "price_test_chat_reply_gateway"
            ),
        )

        # Login después de tener perfil + company + Premium.
        self.client.force_login(
            self.user
        )

        # ======================================================
        # 5. BODEGA COMPANY A
        # ======================================================

        self.warehouse_a = Warehouse.objects.create(
            company=self.company_a,
            name="Gateway Bodega A",
            code="GW-A",
            is_active=True,
        )
        # ======================================================
        # CAPABILITY REAL DE WHATSAPP
        # ======================================================
        #
        # La URL chat_reply está protegida por:
        #
        # capability_required("messaging.operate")
        #
        # Por eso el empleado temporal debe tener exactamente
        # la misma autorización que tendría un empleado real.
        # ======================================================

        self.messaging_capability, _ = Capability.objects.get_or_create(
            code="messaging.operate",
            defaults={
                "name": "Atender WhatsApp",
                "description": ("Permite atender conversaciones " "de WhatsApp."),
                "area_code": "messaging",
                "is_active": True,
            },
        )

        EmployeeCapabilityAssignment.objects.create(
            company=self.company_a,
            user=self.user,
            capability=self.messaging_capability,
            is_active=True,
        )

        # ======================================================
        # ACCESO OPERATIVO A LA BODEGA
        # ======================================================
        #
        # Aunque el capability_required de esta URL no exige
        # una bodega concreta, reproducimos un empleado real
        # con acceso operativo a su bodega.
        # ======================================================

        UserWarehouseAccess.objects.create(
            company=self.company_a,
            user=self.user,
            warehouse=self.warehouse_a,
            access_level=UserWarehouseAccess.LEVEL_OPERATE,
            is_active=True,
        )

        # ======================================================
        # 6. CANAL A1
        # ======================================================
        #
        # Lo creamos disconnected para no requerir una
        # configuración Twilio real durante full_clean().
        #
        # Después lo activamos directamente SOLO en la DB
        # temporal para simular un canal operacional.
        # ======================================================

        self.channel_a = WhatsAppNumber.objects.create(
            company=self.company_a,
            warehouse=self.warehouse_a,
            display_number="+525500010001",
            channel_name="Gateway Ventas A",
            provider=WhatsAppNumber.PROVIDER_TWILIO,
            activation_status=(
                WhatsAppNumber.STATUS_DISCONNECTED
            ),
            twilio_enabled=False,
            meta_enabled=False,
        )

        WhatsAppNumber.objects.filter(
            pk=self.channel_a.pk,
            company=self.company_a,
        ).update(
            activation_status=(
                WhatsAppNumber.STATUS_ACTIVE
            ),
            is_active=True,
        )

        self.channel_a.refresh_from_db()
        # ======================================================
        # AGENTE AUTORIZADO PARA RESPONDER EN ESTE NÚMERO
        # ======================================================
        #
        # messaging.operate autoriza entrar al módulo.
        #
        # ChannelAgentAssignment autoriza específicamente
        # trabajar con ESTE número de WhatsApp.
        #
        # Message.full_clean() valida también esta barrera.
        # ======================================================

        self.channel_agent_assignment = ChannelAgentAssignment.objects.create(
            company=self.company_a,
            whatsapp_number=self.channel_a,
            agent=self.user,
            can_view=True,
            can_reply=True,
            can_create_order=False,
            is_supervisor=False,
            is_active=True,
        )

        # ======================================================
        # 7. CLIENTE + CONVERSACIÓN A1
        # ======================================================

        self.customer_a = MessagingCustomer.objects.create(
            company=self.company_a,
            name="Cliente Gateway A",
            phone="+525511111111",
        )

        self.conversation_a = Conversation.objects.create(
            company=self.company_a,
            whatsapp_number=self.channel_a,
            customer=self.customer_a,
            status=Conversation.STATUS_OPEN,
        )

        # ======================================================
        # 8. SEGUNDO CANAL DE COMPANY A
        # ======================================================

        self.channel_a2 = WhatsAppNumber.objects.create(
            company=self.company_a,
            warehouse=self.warehouse_a,
            display_number="+525500010002",
            channel_name="Gateway Atención A",
            provider=WhatsAppNumber.PROVIDER_TWILIO,
            activation_status=(
                WhatsAppNumber.STATUS_DISCONNECTED
            ),
            twilio_enabled=False,
            meta_enabled=False,
        )

        WhatsAppNumber.objects.filter(
            pk=self.channel_a2.pk,
            company=self.company_a,
        ).update(
            activation_status=(
                WhatsAppNumber.STATUS_ACTIVE
            ),
            is_active=True,
        )

        self.channel_a2.refresh_from_db()

        self.customer_a2 = MessagingCustomer.objects.create(
            company=self.company_a,
            name="Cliente Gateway A2",
            phone="+525522222222",
        )

        self.conversation_a2 = Conversation.objects.create(
            company=self.company_a,
            whatsapp_number=self.channel_a2,
            customer=self.customer_a2,
            status=Conversation.STATUS_OPEN,
        )

        # ======================================================
        # 9. COMPANY B
        # ======================================================

        self.company_b = Company.objects.create(
            name="Gateway View Company B",
            slug="gateway-view-company-b",
            is_active=True,
        )

        self.warehouse_b = Warehouse.objects.create(
            company=self.company_b,
            name="Gateway Bodega B",
            code="GW-B",
            is_active=True,
        )

        # ======================================================
        # 10. CANAL DE COMPANY B
        # ======================================================

        self.channel_b = WhatsAppNumber.objects.create(
            company=self.company_b,
            warehouse=self.warehouse_b,
            display_number="+525500020001",
            channel_name="Gateway Ventas B",
            provider=WhatsAppNumber.PROVIDER_TWILIO,
            activation_status=(
                WhatsAppNumber.STATUS_DISCONNECTED
            ),
            twilio_enabled=False,
            meta_enabled=False,
        )

        WhatsAppNumber.objects.filter(
            pk=self.channel_b.pk,
            company=self.company_b,
        ).update(
            activation_status=(
                WhatsAppNumber.STATUS_ACTIVE
            ),
            is_active=True,
        )

        self.channel_b.refresh_from_db()

        self.customer_b = MessagingCustomer.objects.create(
            company=self.company_b,
            name="Cliente Gateway B",
            phone="+525533333333",
        )

        self.conversation_b = Conversation.objects.create(
            company=self.company_b,
            whatsapp_number=self.channel_b,
            customer=self.customer_b,
            status=Conversation.STATUS_OPEN,
        )

    # ==========================================================
    # HELPER
    # ==========================================================

    def reply_url(self, conversation):
        return reverse(
            "messaging:chat_reply",
            args=[
                conversation.pk,
            ],
        )

    # ==========================================================
    # TEST 1
    # CHAT_REPLY REALMENTE LLEGA AL GATEWAY
    # ==========================================================

    @patch(
        "messaging.views.send_whatsapp_text"
    )
    @patch(
        "messaging.views.get_user_allowed_channel_ids"
    )
    def test_chat_reply_uses_gateway(
        self,
        mock_allowed_channels,
        mock_gateway,
    ):
        mock_allowed_channels.return_value = [
            self.channel_a.id,
        ]

        mock_gateway.return_value = {
            "provider": "twilio",
            "provider_message_id": (
                "SM_GATEWAY_VIEW_001"
            ),
            "status": "queued",
            "raw_response": {
                "sid": "SM_GATEWAY_VIEW_001",
                "status": "queued",
            },
        }

        response = self.client.post(
            self.reply_url(
                self.conversation_a
            ),
            {
                "body": "Hola desde el Gateway",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        mock_gateway.assert_called_once()

        kwargs = (
            mock_gateway.call_args.kwargs
        )

        self.assertEqual(
            kwargs["whatsapp_number"].id,
            self.channel_a.id,
        )

        self.assertEqual(
            kwargs["to_phone"],
            self.customer_a.phone,
        )

        self.assertEqual(
            kwargs["body"],
            "Hola desde el Gateway",
        )

    # ==========================================================
    # TEST 2
    # GUARDA PROVIDER_MESSAGE_ID Y AUDITORÍA NEUTRAL
    # ==========================================================

    @patch(
        "messaging.views.send_whatsapp_text"
    )
    @patch(
        "messaging.views.get_user_allowed_channel_ids"
    )
    def test_successful_reply_saves_provider_data(
        self,
        mock_allowed_channels,
        mock_gateway,
    ):
        mock_allowed_channels.return_value = [
            self.channel_a.id,
        ]

        mock_gateway.return_value = {
            "provider": "twilio",
            "provider_message_id": (
                "SM_GATEWAY_VIEW_002"
            ),
            "status": "queued",
            "raw_response": {
                "sid": "SM_GATEWAY_VIEW_002",
            },
        }

        response = self.client.post(
            self.reply_url(
                self.conversation_a
            ),
            {
                "body": "Mensaje auditado",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        mock_gateway.assert_called_once()

        message = Message.objects.get(
            conversation=self.conversation_a,
            direction=Message.DIRECTION_OUTBOUND,
        )

        self.assertEqual(
            message.agent_id,
            self.user.id,
        )

        self.assertEqual(
            message.company_id,
            self.company_a.id,
        )

        self.assertEqual(
            message.whatsapp_number_id,
            self.channel_a.id,
        )

        self.assertEqual(
            message.provider_message_id,
            "SM_GATEWAY_VIEW_002",
        )

        self.assertEqual(
            message.status,
            Message.STATUS_SENT,
        )

        payload = (
            message.raw_payload
            or {}
        )

        self.assertEqual(
            payload.get("source"),
            "agent_reply_whatsapp_gateway",
        )

        self.assertEqual(
            payload.get("provider"),
            "twilio",
        )

        self.assertEqual(
            payload.get("provider_status"),
            "queued",
        )

        self.assertEqual(
            payload.get("provider_message_id"),
            "SM_GATEWAY_VIEW_002",
        )

        self.assertEqual(
            payload.get("company_id"),
            self.company_a.id,
        )

        self.assertEqual(
            payload.get("warehouse_id"),
            self.warehouse_a.id,
        )

    # ==========================================================
    # TEST 3
    # ERROR DEL GATEWAY QUEDA AUDITADO COMO FAILED
    # ==========================================================

    @patch(
        "messaging.views.send_whatsapp_text"
    )
    @patch(
        "messaging.views.get_user_allowed_channel_ids"
    )
    def test_gateway_error_marks_message_failed(
        self,
        mock_allowed_channels,
        mock_gateway,
    ):
        mock_allowed_channels.return_value = [
            self.channel_a.id,
        ]

        mock_gateway.side_effect = (
            WhatsAppProviderError(
                "Error controlado de proveedor."
            )
        )

        response = self.client.post(
            self.reply_url(
                self.conversation_a
            ),
            {
                "body": "Este envío fallará",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        mock_gateway.assert_called_once()

        message = Message.objects.get(
            conversation=self.conversation_a,
            direction=Message.DIRECTION_OUTBOUND,
        )

        self.assertEqual(
            message.status,
            Message.STATUS_FAILED,
        )

        payload = (
            message.raw_payload
            or {}
        )

        self.assertEqual(
            payload.get("provider_status"),
            "failed",
        )

        self.assertIn(
            "Error controlado",
            payload.get(
                "provider_error",
                "",
            ),
        )

    # ==========================================================
    # TEST 4
    # CRÉDITOS INSUFICIENTES QUEDAN AUDITADOS
    # ==========================================================

    @patch(
        "messaging.views.send_whatsapp_text"
    )
    @patch(
        "messaging.views.get_user_allowed_channel_ids"
    )
    def test_insufficient_credits_marks_message_failed(
        self,
        mock_allowed_channels,
        mock_gateway,
    ):
        mock_allowed_channels.return_value = [
            self.channel_a.id,
        ]

        mock_gateway.side_effect = (
            WhatsAppInsufficientCredits(
                "Créditos insuficientes."
            )
        )

        response = self.client.post(
            self.reply_url(
                self.conversation_a
            ),
            {
                "body": "Mensaje sin crédito",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        mock_gateway.assert_called_once()

        message = Message.objects.get(
            conversation=self.conversation_a,
            direction=Message.DIRECTION_OUTBOUND,
        )

        self.assertEqual(
            message.status,
            Message.STATUS_FAILED,
        )

        payload = (
            message.raw_payload
            or {}
        )

        self.assertEqual(
            payload.get("provider_status"),
            "failed",
        )

    # ==========================================================
    # TEST 5
    # CANAL NO AUTORIZADO DE LA MISMA COMPANY = 404
    # ==========================================================

    @patch(
        "messaging.views.send_whatsapp_text"
    )
    @patch(
        "messaging.views.get_user_allowed_channel_ids"
    )
    def test_unallowed_channel_returns_404(
        self,
        mock_allowed_channels,
        mock_gateway,
    ):
        # Solamente autorizamos channel_a.
        mock_allowed_channels.return_value = [
            self.channel_a.id,
        ]

        response = self.client.post(
            self.reply_url(
                self.conversation_a2
            ),
            {
                "body": "No debe enviarse",
            },
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        mock_gateway.assert_not_called()

        self.assertFalse(
            Message.objects.filter(
                conversation=self.conversation_a2,
                direction=Message.DIRECTION_OUTBOUND,
            ).exists()
        )

    # ==========================================================
    # TEST 6
    # ATAQUE CROSS-COMPANY = 404
    # ==========================================================

    @patch(
        "messaging.views.send_whatsapp_text"
    )
    @patch(
        "messaging.views.get_user_allowed_channel_ids"
    )
    def test_cross_company_conversation_returns_404(
        self,
        mock_allowed_channels,
        mock_gateway,
    ):
        # Incluimos deliberadamente el canal extranjero.
        #
        # Aun así:
        #
        #     company=self.company_a
        #
        # dentro de chat_reply debe impedir acceder a
        # conversation_b.
        mock_allowed_channels.return_value = [
            self.channel_a.id,
            self.channel_b.id,
        ]

        response = self.client.post(
            self.reply_url(
                self.conversation_b
            ),
            {
                "body": "Ataque cross-company",
            },
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        mock_gateway.assert_not_called()

        self.assertFalse(
            Message.objects.filter(
                conversation=self.conversation_b,
                direction=Message.DIRECTION_OUTBOUND,
            ).exists()
        )

    # ==========================================================
    # TEST 7
    # CANAL OPERATIVO SIN BODEGA
    # ==========================================================

    @patch(
        "messaging.views.send_whatsapp_text"
    )
    @patch(
        "messaging.views.get_user_allowed_channel_ids"
    )
    def test_channel_without_warehouse_is_blocked(
        self,
        mock_allowed_channels,
        mock_gateway,
    ):
        # Crear como disconnected es válido.
        no_warehouse_channel = (
            WhatsAppNumber.objects.create(
                company=self.company_a,
                warehouse=None,
                display_number="+525500010099",
                channel_name="Histórico Sin Bodega",
                provider=(
                    WhatsAppNumber.PROVIDER_TWILIO
                ),
                activation_status=(
                    WhatsAppNumber.STATUS_DISCONNECTED
                ),
                twilio_enabled=False,
                meta_enabled=False,
            )
        )

        # Simulamos un registro histórico corrupto/antiguo:
        # active pero sin warehouse.
        #
        # Esto SOLO sucede en la base temporal.
        WhatsAppNumber.objects.filter(
            pk=no_warehouse_channel.pk,
            company=self.company_a,
        ).update(
            activation_status=(
                WhatsAppNumber.STATUS_ACTIVE
            ),
            is_active=True,
        )

        no_warehouse_channel.refresh_from_db()

        customer = MessagingCustomer.objects.create(
            company=self.company_a,
            name="Cliente Sin Bodega",
            phone="+525544444444",
        )

        conversation = Conversation.objects.create(
            company=self.company_a,
            whatsapp_number=no_warehouse_channel,
            customer=customer,
            status=Conversation.STATUS_OPEN,
        )

        mock_allowed_channels.return_value = [
            no_warehouse_channel.id,
        ]

        response = self.client.post(
            self.reply_url(
                conversation
            ),
            {
                "body": "No debe salir",
            },
        )

        # chat_reply detecta la falta de bodega y regresa
        # al detalle del chat.
        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response.url,
            reverse(
                "messaging:chat_detail",
                args=[
                    conversation.pk,
                ],
            ),
        )

        mock_gateway.assert_not_called()

        self.assertFalse(
            Message.objects.filter(
                conversation=conversation,
                direction=Message.DIRECTION_OUTBOUND,
            ).exists()
        )
