from datetime import datetime, timezone as datetime_timezone

from django.test import TestCase, override_settings

from companies.models import Company
from inventory.models import Warehouse

from messaging.meta_webhook import (
    MetaWebhookPayloadError,
    process_meta_inbound_message,
    process_meta_message_status,
)
from messaging.models import (
    Conversation,
    Message,
    MessagingCustomer,
    WebhookEvent,
    WhatsAppNumber,
)


# Estas pruebas cubren numeros que YA son de Meta directo (como el de
# Jazmin hasta su migracion a Twilio). Dar de alta numeros nuevos por Meta
# esta apagado desde el 25-sep-2026 (messaging/provider_policy.py), asi que
# los fixtures lo encienden para poder crear esos numeros.
@override_settings(WHATSAPP_META_DIRECT_ENABLED=True)
class MetaWebhookProcessingTests(TestCase):
    """
    Procesamiento real sobre DB temporal.

    NO usa:
    - Meta real;
    - Twilio real;
    - Internet;
    - tokens reales;
    - base de producción.
    """

    def setUp(self):
        self.company_a = Company.objects.create(
            name="Meta Processing Company A",
            slug="meta-processing-company-a",
            is_active=True,
        )

        self.warehouse_a = Warehouse.objects.create(
            company=self.company_a,
            name="Meta Processing Bodega A",
            code="MP-A",
            is_active=True,
        )

        self.channel_a = self.create_meta_channel(
            company=self.company_a,
            warehouse=self.warehouse_a,
            display_number="+525500010001",
            channel_name="Meta Processing A",
            phone_number_id="111111111111111",
        )

        self.company_b = Company.objects.create(
            name="Meta Processing Company B",
            slug="meta-processing-company-b",
            is_active=True,
        )

        self.warehouse_b = Warehouse.objects.create(
            company=self.company_b,
            name="Meta Processing Bodega B",
            code="MP-B",
            is_active=True,
        )

        self.channel_b = self.create_meta_channel(
            company=self.company_b,
            warehouse=self.warehouse_b,
            display_number="+525500020001",
            channel_name="Meta Processing B",
            phone_number_id="222222222222222",
        )

    # ==========================================================
    # HELPERS
    # ==========================================================

    def create_meta_channel(
        self,
        *,
        company,
        warehouse,
        display_number,
        channel_name,
        phone_number_id,
    ):
        channel = WhatsAppNumber.objects.create(
            company=company,
            warehouse=warehouse,
            display_number=display_number,
            channel_name=channel_name,
            provider=WhatsAppNumber.PROVIDER_META,
            phone_number_id=phone_number_id,
            activation_status=(
                WhatsAppNumber.STATUS_DISCONNECTED
            ),
            twilio_enabled=False,
            meta_enabled=False,
        )

        WhatsAppNumber.objects.filter(
            pk=channel.pk,
            company=company,
        ).update(
            activation_status=(
                WhatsAppNumber.STATUS_ACTIVE
            ),
            is_active=True,
            meta_enabled=True,
        )

        channel.refresh_from_db()

        return channel

    def inbound_message(
        self,
        *,
        message_id="wamid.IN001",
        from_phone="525511111111",
        timestamp="1780000000",
        message_type="text",
        body="Hola WMS360",
    ):
        message = {
            "id": message_id,
            "from": from_phone,
            "timestamp": timestamp,
            "type": message_type,
        }

        if message_type == "text":
            message["text"] = {
                "body": body,
            }

        return message

    def inbound_value(
        self,
        *,
        phone="525511111111",
        name="Cliente Meta",
    ):
        return {
            "contacts": [
                {
                    "wa_id": phone,
                    "profile": {
                        "name": name,
                    },
                }
            ],
        }

    def create_outbound_message(
        self,
        *,
        company=None,
        channel=None,
        phone="525599999999",
        provider_message_id="wamid.OUT001",
        status=Message.STATUS_SENT,
    ):
        company = company or self.company_a
        channel = channel or self.channel_a

        customer = MessagingCustomer.objects.create(
            company=company,
            name="Cliente Outbound",
            phone=phone,
        )

        conversation = Conversation.objects.create(
            company=company,
            whatsapp_number=channel,
            customer=customer,
            status=Conversation.STATUS_OPEN,
        )

        return Message.objects.create(
            conversation=conversation,
            company=company,
            whatsapp_number=channel,
            provider_message_id=(
                provider_message_id
            ),
            direction=Message.DIRECTION_OUTBOUND,
            sender_type=Message.SENDER_SYSTEM,
            message_type=Message.TYPE_TEXT,
            body="Mensaje saliente",
            status=status,
        )

    # ==========================================================
    # 1. INBOUND CREA TODO EN COMPANY CORRECTA
    # ==========================================================

    def test_inbound_text_creates_customer_conversation_message_and_event(self):
        message = process_meta_inbound_message(
            channel=self.channel_a,
            message=self.inbound_message(),
            value=self.inbound_value(),
        )

        self.assertEqual(
            message.company_id,
            self.company_a.pk,
        )

        self.assertEqual(
            message.whatsapp_number_id,
            self.channel_a.pk,
        )

        self.assertEqual(
            message.direction,
            Message.DIRECTION_INBOUND,
        )

        self.assertEqual(
            message.status,
            Message.STATUS_RECEIVED,
        )

        self.assertEqual(
            message.body,
            "Hola WMS360",
        )

        self.assertEqual(
            message.conversation.company_id,
            self.company_a.pk,
        )

        self.assertEqual(
            message.conversation.customer.company_id,
            self.company_a.pk,
        )

        self.assertEqual(
            MessagingCustomer.objects.filter(
                company=self.company_a,
            ).count(),
            1,
        )

        event = WebhookEvent.objects.get(
            company=self.company_a,
            whatsapp_number=self.channel_a,
            event_type="meta_whatsapp_inbound",
            provider_message_id="wamid.IN001",
        )

        self.assertTrue(
            event.is_processed
        )

    # ==========================================================
    # 2. INBOUND IDEMPOTENTE
    # ==========================================================

    def test_duplicate_inbound_message_is_idempotent(self):
        payload = self.inbound_message()

        first = process_meta_inbound_message(
            channel=self.channel_a,
            message=payload,
            value=self.inbound_value(),
        )

        second = process_meta_inbound_message(
            channel=self.channel_a,
            message=payload,
            value=self.inbound_value(),
        )

        self.assertEqual(
            first.pk,
            second.pk,
        )

        self.assertEqual(
            Message.objects.filter(
                provider_message_id="wamid.IN001",
            ).count(),
            1,
        )

        self.assertEqual(
            WebhookEvent.objects.filter(
                event_type="meta_whatsapp_inbound",
                provider_message_id="wamid.IN001",
            ).count(),
            1,
        )

    # ==========================================================
    # 3. MISMO WAMID NO PUEDE CRUZAR COMPANY
    # ==========================================================

    def test_same_provider_message_id_cannot_cross_company(self):
        payload_a = self.inbound_message(
            message_id="wamid.CROSS001",
            from_phone="525511111111",
        )

        message_a = process_meta_inbound_message(
            channel=self.channel_a,
            message=payload_a,
            value=self.inbound_value(
                phone="525511111111",
            ),
        )

        payload_b = self.inbound_message(
            message_id="wamid.CROSS001",
            from_phone="525522222222",
        )

        with self.assertRaises(Exception):
            process_meta_inbound_message(
                channel=self.channel_b,
                message=payload_b,
                value=self.inbound_value(
                    phone="525522222222",
                ),
            )

        message_a.refresh_from_db()

        self.assertEqual(
            message_a.company_id,
            self.company_a.pk,
        )

        self.assertFalse(
            Message.objects.filter(
                company=self.company_b,
                provider_message_id="wamid.CROSS001",
            ).exists()
        )

    # ==========================================================
    # 4. CONVERSACIÓN CERRADA SE REABRE
    # ==========================================================

    def test_inbound_reopens_closed_conversation(self):
        customer = MessagingCustomer.objects.create(
            company=self.company_a,
            name="Cliente Cerrado",
            phone="525511111111",
        )

        conversation = Conversation.objects.create(
            company=self.company_a,
            whatsapp_number=self.channel_a,
            customer=customer,
            status=Conversation.STATUS_CLOSED,
        )

        process_meta_inbound_message(
            channel=self.channel_a,
            message=self.inbound_message(),
            value=self.inbound_value(),
        )

        conversation.refresh_from_db()

        self.assertEqual(
            conversation.status,
            Conversation.STATUS_OPEN,
        )

    # ==========================================================
    # 5. NOMBRE DEL CONTACTO
    # ==========================================================

    def test_contact_profile_name_is_saved(self):
        process_meta_inbound_message(
            channel=self.channel_a,
            message=self.inbound_message(),
            value=self.inbound_value(
                name="José Cliente Meta",
            ),
        )

        customer = MessagingCustomer.objects.get(
            company=self.company_a,
            phone="525511111111",
        )

        self.assertEqual(
            customer.name,
            "José Cliente Meta",
        )

    # ==========================================================
    # 6. TIPO DESCONOCIDO
    # ==========================================================

    def test_unknown_message_type_is_stored_as_unknown(self):
        message = process_meta_inbound_message(
            channel=self.channel_a,
            message=self.inbound_message(
                message_type="reaction",
            ),
            value=self.inbound_value(),
        )

        self.assertEqual(
            message.message_type,
            Message.TYPE_UNKNOWN,
        )

    # ==========================================================
    # 7. REMITENTE INVÁLIDO
    # ==========================================================

    def test_invalid_sender_is_rejected(self):
        with self.assertRaises(
            MetaWebhookPayloadError
        ):
            process_meta_inbound_message(
                channel=self.channel_a,
                message=self.inbound_message(
                    from_phone="123",
                ),
                value={},
            )

        self.assertEqual(
            Message.objects.count(),
            0,
        )

    # ==========================================================
    # 8. SENT -> DELIVERED
    # ==========================================================

    def test_delivered_status_advances_sent_message(self):
        message = self.create_outbound_message(
            provider_message_id="wamid.OUT_DELIVERED",
            status=Message.STATUS_SENT,
        )

        result = process_meta_message_status(
            channel=self.channel_a,
            status_payload={
                "id": "wamid.OUT_DELIVERED",
                "status": "delivered",
                "timestamp": "1780000010",
            },
        )

        message.refresh_from_db()

        self.assertEqual(
            result.pk,
            message.pk,
        )

        self.assertEqual(
            message.status,
            Message.STATUS_DELIVERED,
        )

        event = WebhookEvent.objects.get(
            company=self.company_a,
            event_type="meta_status_delivered",
            provider_message_id="wamid.OUT_DELIVERED",
        )

        self.assertTrue(
            event.is_processed
        )

    # ==========================================================
    # 9. DELIVERED -> READ
    # ==========================================================

    def test_read_status_advances_delivered_message(self):
        message = self.create_outbound_message(
            provider_message_id="wamid.OUT_READ",
            status=Message.STATUS_DELIVERED,
        )

        process_meta_message_status(
            channel=self.channel_a,
            status_payload={
                "id": "wamid.OUT_READ",
                "status": "read",
                "timestamp": "1780000020",
            },
        )

        message.refresh_from_db()

        self.assertEqual(
            message.status,
            Message.STATUS_READ,
        )

    # ==========================================================
    # 10. READ NO REGRESA A SENT
    # ==========================================================

    def test_late_sent_does_not_downgrade_read(self):
        message = self.create_outbound_message(
            provider_message_id="wamid.OUT_LATE_SENT",
            status=Message.STATUS_READ,
        )

        process_meta_message_status(
            channel=self.channel_a,
            status_payload={
                "id": "wamid.OUT_LATE_SENT",
                "status": "sent",
                "timestamp": "1780000030",
            },
        )

        message.refresh_from_db()

        self.assertEqual(
            message.status,
            Message.STATUS_READ,
        )

    # ==========================================================
    # 11. SENT -> FAILED
    # ==========================================================

    def test_failed_status_can_fail_sent_message(self):
        message = self.create_outbound_message(
            provider_message_id="wamid.OUT_FAILED",
            status=Message.STATUS_SENT,
        )

        process_meta_message_status(
            channel=self.channel_a,
            status_payload={
                "id": "wamid.OUT_FAILED",
                "status": "failed",
                "timestamp": "1780000040",
                "errors": [
                    {
                        "code": 131000,
                        "title": "Test error",
                    }
                ],
            },
        )

        message.refresh_from_db()

        self.assertEqual(
            message.status,
            Message.STATUS_FAILED,
        )

    # ==========================================================
    # 12. DELIVERED NO REGRESA A FAILED
    # ==========================================================

    def test_failed_does_not_downgrade_delivered_message(self):
        message = self.create_outbound_message(
            provider_message_id="wamid.OUT_NOFAIL",
            status=Message.STATUS_DELIVERED,
        )

        process_meta_message_status(
            channel=self.channel_a,
            status_payload={
                "id": "wamid.OUT_NOFAIL",
                "status": "failed",
                "timestamp": "1780000050",
            },
        )

        message.refresh_from_db()

        self.assertEqual(
            message.status,
            Message.STATUS_DELIVERED,
        )

    # ==========================================================
    # 13. STATUS DUPLICADO ES IDEMPOTENTE
    # ==========================================================

    def test_duplicate_status_event_is_idempotent(self):
        self.create_outbound_message(
            provider_message_id="wamid.OUT_DUP",
            status=Message.STATUS_SENT,
        )

        payload = {
            "id": "wamid.OUT_DUP",
            "status": "delivered",
            "timestamp": "1780000060",
        }

        process_meta_message_status(
            channel=self.channel_a,
            status_payload=payload,
        )

        process_meta_message_status(
            channel=self.channel_a,
            status_payload=payload,
        )

        self.assertEqual(
            WebhookEvent.objects.filter(
                company=self.company_a,
                whatsapp_number=self.channel_a,
                event_type="meta_status_delivered",
                provider_message_id="wamid.OUT_DUP",
            ).count(),
            1,
        )

    # ==========================================================
    # 14. STATUS DE COMPANY B NO TOCA MESSAGE DE A
    # ==========================================================

    def test_cross_company_status_cannot_modify_message(self):
        message_a = self.create_outbound_message(
            provider_message_id="wamid.COMPANY_A_ONLY",
            status=Message.STATUS_SENT,
        )

        result = process_meta_message_status(
            channel=self.channel_b,
            status_payload={
                "id": "wamid.COMPANY_A_ONLY",
                "status": "read",
                "timestamp": "1780000070",
            },
        )

        self.assertIsNone(
            result
        )

        message_a.refresh_from_db()

        self.assertEqual(
            message_a.status,
            Message.STATUS_SENT,
        )

        event_b = WebhookEvent.objects.get(
            company=self.company_b,
            whatsapp_number=self.channel_b,
            event_type="meta_status_read",
            provider_message_id="wamid.COMPANY_A_ONLY",
        )

        self.assertFalse(
            event_b.is_processed
        )

        self.assertTrue(
            bool(event_b.error_log)
        )

    # ==========================================================
    # 15. STATUS DESCONOCIDO
    # ==========================================================

    def test_unknown_status_is_rejected(self):
        message = self.create_outbound_message(
            provider_message_id="wamid.UNKNOWN_STATUS",
            status=Message.STATUS_SENT,
        )

        with self.assertRaises(
            MetaWebhookPayloadError
        ):
            process_meta_message_status(
                channel=self.channel_a,
                status_payload={
                    "id": "wamid.UNKNOWN_STATUS",
                    "status": "banana",
                    "timestamp": "1780000080",
                },
            )

        message.refresh_from_db()

        self.assertEqual(
            message.status,
            Message.STATUS_SENT,
        )

    # ==========================================================
    # 16. TIMESTAMP DEL PROVEEDOR
    # ==========================================================

    def test_status_updates_provider_timestamp(self):
        message = self.create_outbound_message(
            provider_message_id="wamid.TIMESTAMP",
            status=Message.STATUS_SENT,
        )

        process_meta_message_status(
            channel=self.channel_a,
            status_payload={
                "id": "wamid.TIMESTAMP",
                "status": "delivered",
                "timestamp": "1780000090",
            },
        )

        message.refresh_from_db()

        expected = datetime.fromtimestamp(
            1780000090,
            tz=datetime_timezone.utc,
        )

        self.assertEqual(
            message.provider_timestamp,
            expected,
        )
