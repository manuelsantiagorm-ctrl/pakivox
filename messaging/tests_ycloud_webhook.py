import hashlib
import hmac
import json
import time

from django.test import TestCase, override_settings
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
from messaging.ycloud_webhook import (
    YCloudWebhookError,
    parse_ycloud_payload,
    process_ycloud_event,
    verify_ycloud_signature,
)


@override_settings(
    YCLOUD_WEBHOOK_SECRET="ycloud_test_secret",
    YCLOUD_WEBHOOK_TOLERANCE_SECONDS=300,
)
class YCloudWebhookTests(TestCase):
    NOW = 1_800_000_000

    def setUp(self):
        self._outbound_customer_sequence = 0
        self.url = reverse("messaging:ycloud_whatsapp_webhook")
        self.company_a = Company.objects.create(
            name="YCloud Company A", slug="ycloud-company-a", is_active=True
        )
        self.warehouse_a = Warehouse.objects.create(
            company=self.company_a,
            name="YCloud Bodega A",
            code="YC-A",
            is_active=True,
        )
        self.channel_a = self.create_channel(
            company=self.company_a,
            warehouse=self.warehouse_a,
            display_number="+525500010001",
            phone_number_id="ycloud-channel-a",
        )

        self.company_b = Company.objects.create(
            name="YCloud Company B", slug="ycloud-company-b", is_active=True
        )
        self.warehouse_b = Warehouse.objects.create(
            company=self.company_b,
            name="YCloud Bodega B",
            code="YC-B",
            is_active=True,
        )
        self.channel_b = self.create_channel(
            company=self.company_b,
            warehouse=self.warehouse_b,
            display_number="+525500020001",
            phone_number_id="ycloud-channel-b",
        )

    def create_channel(self, *, company, warehouse, display_number, phone_number_id):
        channel = WhatsAppNumber.objects.create(
            company=company,
            warehouse=warehouse,
            display_number=display_number,
            channel_name=f"Canal {phone_number_id}",
            provider=WhatsAppNumber.PROVIDER_YCLOUD,
            phone_number_id=phone_number_id,
            activation_status=WhatsAppNumber.STATUS_DISCONNECTED,
            twilio_enabled=False,
            meta_enabled=False,
            ycloud_enabled=False,
        )
        WhatsAppNumber.objects.filter(pk=channel.pk).update(
            activation_status=WhatsAppNumber.STATUS_ACTIVE,
            is_active=True,
            ycloud_enabled=True,
        )
        channel.refresh_from_db()
        return channel

    def signature(self, raw_body, timestamp=None):
        timestamp = str(int(time.time()) if timestamp is None else timestamp)
        digest = hmac.new(
            b"ycloud_test_secret",
            timestamp.encode("ascii") + b"." + raw_body,
            hashlib.sha256,
        ).hexdigest()
        return f"t={timestamp},s={digest}"

    def inbound_payload(
        self,
        *,
        event_id="evt-in-1",
        message_id="wamid.yc.in.1",
        to="525500010001",
        sender="525511111111",
        body="Hola desde YCloud",
    ):
        return {
            "id": event_id,
            "apiVersion": "v2",
            "type": "whatsapp.inbound_message.received",
            "whatsappInboundMessage": {
                "id": message_id,
                "wamid": message_id,
                "from": sender,
                "to": to,
                "type": "text",
                "text": {"body": body},
                "customerProfile": {"name": "Cliente YCloud"},
            },
        }

    def status_payload(
        self,
        *,
        event_id="evt-status-1",
        message_id="wamid.yc.out.1",
        sender="525500010001",
        status="delivered",
    ):
        return {
            "id": event_id,
            "apiVersion": "v2",
            "type": "whatsapp.message.updated",
            "whatsappMessage": {
                "id": message_id,
                "wamid": message_id,
                "from": sender,
                "to": "525511111111",
                "status": status,
            },
        }

    def post_payload(self, payload, signature=None):
        raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return self.client.generic(
            "POST",
            self.url,
            data=raw_body,
            content_type="application/json",
            HTTP_YCLOUD_SIGNATURE=signature or self.signature(raw_body),
        )

    def create_outbound(self, *, channel=None, message_id="wamid.yc.out.1"):
        channel = channel or self.channel_a
        self._outbound_customer_sequence += 1
        customer = MessagingCustomer.objects.create(
            company=channel.company,
            name="Cliente saliente",
            phone=f"52559999{self._outbound_customer_sequence:04d}",
        )
        conversation = Conversation.objects.create(
            company=channel.company,
            whatsapp_number=channel,
            customer=customer,
        )
        return Message.objects.create(
            conversation=conversation,
            company=channel.company,
            whatsapp_number=channel,
            provider_message_id=message_id,
            direction=Message.DIRECTION_OUTBOUND,
            sender_type=Message.SENDER_SYSTEM,
            message_type=Message.TYPE_TEXT,
            body="Respuesta",
            status=Message.STATUS_SENT,
        )

    def test_valid_signature_is_accepted(self):
        raw = b'{"ok":true}'
        self.assertTrue(
            verify_ycloud_signature(
                raw,
                self.signature(raw, self.NOW),
                now=self.NOW,
            )
        )

    def test_invalid_missing_expired_and_duplicate_signatures_are_rejected(self):
        raw = b"{}"
        invalid_headers = [
            "",
            "t=1800000000,s=bad",
            self.signature(raw, self.NOW - 301),
            f"t={self.NOW},t={self.NOW},s={'0' * 64}",
        ]
        for header in invalid_headers:
            with self.subTest(header=header):
                with self.assertRaises(YCloudWebhookError):
                    verify_ycloud_signature(raw, header, now=self.NOW)

    def test_payload_parser_accepts_only_known_v2_events(self):
        valid = json.dumps(self.inbound_payload()).encode()
        self.assertEqual(parse_ycloud_payload(valid)["apiVersion"], "v2")
        for payload in [b"not-json", b"[]", b'{"apiVersion":"v1"}', b'{"apiVersion":"v2","id":"x","type":"unknown"}']:
            with self.subTest(payload=payload):
                with self.assertRaises(YCloudWebhookError):
                    parse_ycloud_payload(payload)

    def test_endpoint_allows_only_post(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Allow"], "POST")

    def test_endpoint_rejects_bad_signature_and_bad_json(self):
        response = self.client.generic(
            "POST",
            self.url,
            data=b"{}",
            content_type="application/json",
            HTTP_YCLOUD_SIGNATURE="bad",
        )
        self.assertEqual(response.status_code, 400)
        raw = b"not-json"
        response = self.client.generic(
            "POST",
            self.url,
            data=raw,
            content_type="application/json",
            HTTP_YCLOUD_SIGNATURE=self.signature(raw),
        )
        self.assertEqual(response.status_code, 400)

    def test_inbound_creates_tenant_scoped_records(self):
        response = self.post_payload(self.inbound_payload())
        self.assertEqual(response.status_code, 200)
        customer = MessagingCustomer.objects.get(company=self.company_a)
        conversation = Conversation.objects.get(company=self.company_a)
        message = Message.objects.get(company=self.company_a)
        event = WebhookEvent.objects.get(company=self.company_a)
        self.assertEqual(customer.phone, "+525511111111")
        self.assertEqual(conversation.whatsapp_number, self.channel_a)
        self.assertEqual(message.body, "Hola desde YCloud")
        self.assertEqual(message.status, Message.STATUS_RECEIVED)
        self.assertTrue(event.is_processed)
        self.assertFalse(MessagingCustomer.objects.filter(company=self.company_b).exists())

    def test_inbound_to_company_b_never_creates_company_a_records(self):
        payload = self.inbound_payload(
            event_id="evt-b",
            message_id="wamid.yc.in.b",
            to="525500020001",
            sender="525522222222",
        )
        process_ycloud_event(payload)
        self.assertTrue(Message.objects.filter(company=self.company_b).exists())
        self.assertFalse(Message.objects.filter(company=self.company_a).exists())

    def test_duplicate_inbound_is_idempotent(self):
        payload = self.inbound_payload()
        first = process_ycloud_event(payload)
        second = process_ycloud_event(payload)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(WebhookEvent.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 1)

    def test_closed_conversation_is_reopened(self):
        process_ycloud_event(self.inbound_payload())
        conversation = Conversation.objects.get()
        conversation.status = Conversation.STATUS_CLOSED
        conversation.save()
        process_ycloud_event(
            self.inbound_payload(event_id="evt-in-2", message_id="wamid.yc.in.2")
        )
        conversation.refresh_from_db()
        self.assertEqual(conversation.status, Conversation.STATUS_OPEN)

    def test_sent_delivered_read_and_failed_statuses_are_supported(self):
        for index, status in enumerate(("sent", "delivered", "read", "failed"), 1):
            message_id = f"wamid.yc.status.{index}"
            message = self.create_outbound(message_id=message_id)
            process_ycloud_event(
                self.status_payload(
                    event_id=f"evt-status-{index}",
                    message_id=message_id,
                    status=status,
                )
            )
            message.refresh_from_db()
            self.assertEqual(message.status, status)

    def test_status_does_not_regress_from_read_to_delivered(self):
        message = self.create_outbound()
        message.status = Message.STATUS_READ
        message.save(update_fields=["status", "updated_at"])
        process_ycloud_event(self.status_payload(status="delivered"))
        message.refresh_from_db()
        self.assertEqual(message.status, Message.STATUS_READ)

    def test_status_reconciles_ycloud_id_to_wamid(self):
        message = self.create_outbound(message_id="ycloud-internal-7")
        message.ycloud_message_id = "ycloud-internal-7"
        message.whatsapp_message_id = "wamid.meta-7"
        message.save(update_fields=["ycloud_message_id", "whatsapp_message_id"])
        payload = self.status_payload(message_id="wamid.meta-7")
        payload["whatsappMessage"]["id"] = "ycloud-internal-7"
        process_ycloud_event(payload)
        message.refresh_from_db()
        self.assertEqual(message.status, Message.STATUS_DELIVERED)

    def test_status_reconciles_by_external_id_inside_same_tenant(self):
        message = self.create_outbound(message_id="ycloud-internal-8")
        message.provider_external_id = str(message.internal_id)
        message.save(update_fields=["provider_external_id"])
        payload = self.status_payload(message_id="wamid.new-8")
        payload["whatsappMessage"]["id"] = "ycloud-new-8"
        payload["whatsappMessage"]["externalId"] = str(message.internal_id)
        process_ycloud_event(payload)
        message.refresh_from_db()
        self.assertEqual(message.status, Message.STATUS_DELIVERED)

    def test_status_cannot_update_message_from_another_tenant(self):
        message = self.create_outbound(channel=self.channel_b, message_id="wamid.b")
        with self.assertRaises(YCloudWebhookError):
            process_ycloud_event(self.status_payload(message_id="wamid.b"))
        message.refresh_from_db()
        self.assertEqual(message.status, Message.STATUS_SENT)

    def test_unknown_channel_is_rejected_without_creating_records(self):
        with self.assertRaises(YCloudWebhookError):
            process_ycloud_event(self.inbound_payload(to="525500099999"))
        self.assertEqual(WebhookEvent.objects.count(), 0)
        self.assertEqual(Message.objects.count(), 0)

    def test_inactive_company_warehouse_and_channel_are_rejected(self):
        cases = (
            (self.company_a, "is_active"),
            (self.warehouse_a, "is_active"),
            (self.channel_a, "is_active"),
        )
        for instance, field in cases:
            with self.subTest(model=instance._meta.label):
                type(instance).objects.filter(pk=instance.pk).update(**{field: False})
                with self.assertRaises(YCloudWebhookError):
                    process_ycloud_event(self.inbound_payload())
                type(instance).objects.filter(pk=instance.pk).update(**{field: True})

    def test_suspended_channel_and_non_ycloud_provider_are_rejected(self):
        WhatsAppNumber.objects.filter(pk=self.channel_a.pk).update(
            activation_status=WhatsAppNumber.STATUS_SUSPENDED
        )
        with self.assertRaises(YCloudWebhookError):
            process_ycloud_event(self.inbound_payload())
        WhatsAppNumber.objects.filter(pk=self.channel_a.pk).update(
            activation_status=WhatsAppNumber.STATUS_ACTIVE,
            provider=WhatsAppNumber.PROVIDER_META,
        )
        with self.assertRaises(YCloudWebhookError):
            process_ycloud_event(self.inbound_payload())

    def test_long_event_id_is_safely_bounded(self):
        payload = self.inbound_payload(event_id="e" * 500)
        event = process_ycloud_event(payload)
        self.assertLessEqual(len(event.event_type), 80)
        self.assertTrue(event.is_processed)
