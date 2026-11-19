import hashlib
import hmac
import json

from django.test import (
    TestCase,
    override_settings,
)
from django.urls import reverse

from companies.models import Company
from inventory.models import Warehouse

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
@override_settings(
    META_WEBHOOK_VERIFY_TOKEN="VERIFY_META_TEST_123",
    META_APP_SECRET="APP_SECRET_META_TEST_123",
    WHATSAPP_META_DIRECT_ENABLED=True,
)
class MetaWebhookViewTests(TestCase):
    """
    Endpoint Meta completo usando Django Test Client.

    NO usa:
    - Internet;
    - Meta real;
    - Twilio;
    - secretos reales;
    - base de producción.
    """

    def setUp(self):
        self.url = reverse(
            "messaging:meta_whatsapp_webhook"
        )

        # ======================================================
        # COMPANY A
        # ======================================================

        self.company_a = Company.objects.create(
            name="Meta View Company A",
            slug="meta-view-company-a",
            is_active=True,
        )

        self.warehouse_a = Warehouse.objects.create(
            company=self.company_a,
            name="Meta View Bodega A",
            code="MV-A",
            is_active=True,
        )

        self.channel_a = self.create_meta_channel(
            company=self.company_a,
            warehouse=self.warehouse_a,
            display_number="+525500010001",
            channel_name="Meta View A",
            phone_number_id="111111111111111",
        )

        # ======================================================
        # COMPANY B
        # ======================================================

        self.company_b = Company.objects.create(
            name="Meta View Company B",
            slug="meta-view-company-b",
            is_active=True,
        )

        self.warehouse_b = Warehouse.objects.create(
            company=self.company_b,
            name="Meta View Bodega B",
            code="MV-B",
            is_active=True,
        )

        self.channel_b = self.create_meta_channel(
            company=self.company_b,
            warehouse=self.warehouse_b,
            display_number="+525500020001",
            channel_name="Meta View B",
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

    def signature_for(self, raw_body):
        digest = hmac.new(
            b"APP_SECRET_META_TEST_123",
            raw_body,
            hashlib.sha256,
        ).hexdigest()

        return f"sha256={digest}"

    def post_raw(
        self,
        payload,
        *,
        signature=None,
    ):
        raw_body = json.dumps(
            payload,
            separators=(",", ":"),
        ).encode("utf-8")

        if signature is None:
            signature = self.signature_for(
                raw_body
            )

        return self.client.generic(
            "POST",
            self.url,
            data=raw_body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=signature,
        )

    def message_payload(
        self,
        *,
        phone_number_id="111111111111111",
        message_id="wamid.VIEW_IN_001",
        from_phone="525511111111",
        body="Hola desde Meta",
    ):
        return {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA_TEST_A",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": (
                                        "525500010001"
                                    ),
                                    "phone_number_id": (
                                        phone_number_id
                                    ),
                                },
                                "contacts": [
                                    {
                                        "wa_id": from_phone,
                                        "profile": {
                                            "name": "Cliente View",
                                        },
                                    }
                                ],
                                "messages": [
                                    {
                                        "id": message_id,
                                        "from": from_phone,
                                        "timestamp": "1780000000",
                                        "type": "text",
                                        "text": {
                                            "body": body,
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }

    def status_payload(
        self,
        *,
        phone_number_id="111111111111111",
        message_id="wamid.VIEW_OUT_001",
        status="delivered",
    ):
        return {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA_TEST_A",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": (
                                        "525500010001"
                                    ),
                                    "phone_number_id": (
                                        phone_number_id
                                    ),
                                },
                                "statuses": [
                                    {
                                        "id": message_id,
                                        "status": status,
                                        "timestamp": "1780000010",
                                        "recipient_id": (
                                            "525511111111"
                                        ),
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }

    def create_outbound(
        self,
        *,
        company=None,
        channel=None,
        message_id="wamid.VIEW_OUT_001",
    ):
        company = company or self.company_a
        channel = channel or self.channel_a

        customer = MessagingCustomer.objects.create(
            company=company,
            name="Cliente Out",
            phone="525599999999",
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
            provider_message_id=message_id,
            direction=Message.DIRECTION_OUTBOUND,
            sender_type=Message.SENDER_SYSTEM,
            message_type=Message.TYPE_TEXT,
            body="Mensaje salida",
            status=Message.STATUS_SENT,
        )

    # ==========================================================
    # 1. GET VÁLIDO
    # ==========================================================

    def test_valid_get_challenge_returns_challenge(self):
        response = self.client.get(
            self.url,
            {
                "hub.mode": "subscribe",
                "hub.verify_token": (
                    "VERIFY_META_TEST_123"
                ),
                "hub.challenge": "987654321",
            },
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.content.decode(),
            "987654321",
        )

    # ==========================================================
    # 2. GET TOKEN INCORRECTO
    # ==========================================================

    def test_invalid_get_token_is_forbidden(self):
        response = self.client.get(
            self.url,
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "WRONG",
                "hub.challenge": "123",
            },
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    # ==========================================================
    # 3. SIN APP SECRET
    # ==========================================================

    @override_settings(
        META_APP_SECRET="",
    )
    def test_post_is_blocked_without_app_secret(self):
        payload = self.message_payload()

        raw_body = json.dumps(
            payload,
            separators=(",", ":"),
        ).encode("utf-8")

        response = self.client.generic(
            "POST",
            self.url,
            data=raw_body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=(
                "sha256="
                + ("0" * 64)
            ),
        )

        self.assertEqual(
            response.status_code,
            503,
        )

        self.assertEqual(
            Message.objects.count(),
            0,
        )

    # ==========================================================
    # 4. FIRMA AUSENTE
    # ==========================================================

    def test_missing_signature_is_forbidden(self):
        payload = self.message_payload()

        raw_body = json.dumps(
            payload
        ).encode("utf-8")

        response = self.client.generic(
            "POST",
            self.url,
            data=raw_body,
            content_type="application/json",
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        self.assertEqual(
            Message.objects.count(),
            0,
        )

    # ==========================================================
    # 5. FIRMA INCORRECTA
    # ==========================================================

    def test_invalid_signature_is_forbidden(self):
        response = self.post_raw(
            self.message_payload(),
            signature=(
                "sha256="
                + ("0" * 64)
            ),
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        self.assertEqual(
            Message.objects.count(),
            0,
        )

    # ==========================================================
    # 6. JSON INVÁLIDO PERO BIEN FIRMADO
    # ==========================================================

    def test_signed_invalid_json_is_bad_request(self):
        raw_body = b"{invalid-json"

        response = self.client.generic(
            "POST",
            self.url,
            data=raw_body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=(
                self.signature_for(
                    raw_body
                )
            ),
        )

        self.assertEqual(
            response.status_code,
            400,
        )

    # ==========================================================
    # 7. OBJECT INCORRECTO
    # ==========================================================

    def test_wrong_object_is_bad_request(self):
        response = self.post_raw(
            {
                "object": "page",
                "entry": [],
            }
        )

        self.assertEqual(
            response.status_code,
            400,
        )

    # ==========================================================
    # 8. INBOUND END-TO-END
    # ==========================================================

    def test_signed_inbound_creates_message_in_correct_company(self):
        response = self.post_raw(
            self.message_payload()
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            response.content.decode(),
            "EVENT_RECEIVED",
        )

        message = Message.objects.get(
            provider_message_id=(
                "wamid.VIEW_IN_001"
            )
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
            message.body,
            "Hola desde Meta",
        )

        self.assertFalse(
            Message.objects.filter(
                company=self.company_b,
                provider_message_id=(
                    "wamid.VIEW_IN_001"
                ),
            ).exists()
        )

    # ==========================================================
    # 9. PHONE NUMBER ID DESCONOCIDO
    # ==========================================================

    def test_unknown_phone_number_id_is_not_accepted(self):
        response = self.post_raw(
            self.message_payload(
                phone_number_id=(
                    "999999999999999"
                ),
            )
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        self.assertEqual(
            Message.objects.count(),
            0,
        )

    # ==========================================================
    # 10. STATUS END-TO-END
    # ==========================================================

    def test_signed_status_updates_exact_outbound_message(self):
        message = self.create_outbound(
            message_id="wamid.VIEW_OUT_001",
        )

        response = self.post_raw(
            self.status_payload(
                message_id=(
                    "wamid.VIEW_OUT_001"
                ),
                status="delivered",
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        message.refresh_from_db()

        self.assertEqual(
            message.status,
            Message.STATUS_DELIVERED,
        )

    # ==========================================================
    # 11. STATUS NO SOPORTADO NO ROMPE ENDPOINT
    # ==========================================================

    def test_unsupported_status_is_ignored_safely(self):
        message = self.create_outbound(
            message_id="wamid.VIEW_DELETED",
        )

        response = self.post_raw(
            self.status_payload(
                message_id=(
                    "wamid.VIEW_DELETED"
                ),
                status="deleted",
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        message.refresh_from_db()

        self.assertEqual(
            message.status,
            Message.STATUS_SENT,
        )

    # ==========================================================
    # 12. FIELD NO MESSAGES
    # ==========================================================

    def test_unrelated_webhook_field_is_acknowledged(self):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WABA_TEST_A",
                    "changes": [
                        {
                            "field": (
                                "message_template_status_update"
                            ),
                            "value": {},
                        }
                    ],
                }
            ],
        }

        response = self.post_raw(
            payload
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertEqual(
            Message.objects.count(),
            0,
        )

    # ==========================================================
    # 13. COMPANY B NO MODIFICA MESSAGE DE COMPANY A
    # ==========================================================

    def test_company_b_status_cannot_modify_company_a_message(self):
        message_a = self.create_outbound(
            company=self.company_a,
            channel=self.channel_a,
            message_id="wamid.COMPANY_A_VIEW",
        )

        response = self.post_raw(
            self.status_payload(
                phone_number_id=(
                    self.channel_b.phone_number_id
                ),
                message_id=(
                    "wamid.COMPANY_A_VIEW"
                ),
                status="read",
            )
        )

        # El webhook es auténtico y se reconoce Company B,
        # pero el mensaje no pertenece a esa empresa.
        #
        # Se acepta el webhook para evitar loops de retry,
        # mientras queda WebhookEvent fallido para conciliación.
        self.assertEqual(
            response.status_code,
            200,
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
            provider_message_id=(
                "wamid.COMPANY_A_VIEW"
            ),
        )

        self.assertFalse(
            event_b.is_processed
        )

        self.assertTrue(
            bool(event_b.error_log)
        )
