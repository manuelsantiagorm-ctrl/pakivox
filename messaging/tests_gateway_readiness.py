from types import SimpleNamespace

from django.test import SimpleTestCase

from messaging.whatsapp_gateway import (
    get_whatsapp_provider_name,
    is_whatsapp_channel_ready,
)


class WhatsAppGatewayReadinessTests(SimpleTestCase):
    """
    Pruebas de preparación neutral del proveedor.

    No utiliza base de datos.
    No llama Twilio.
    No llama Meta.
    """

    def make_channel(
        self,
        *,
        provider="twilio",
        is_active=True,
        company_id=1,
        warehouse_id=1,
        is_twilio_ready=True,
    ):
        return SimpleNamespace(
            company_id=company_id,
            warehouse_id=warehouse_id,
            provider=provider,
            is_active=is_active,
            is_twilio_ready=is_twilio_ready,
            PROVIDER_TWILIO="twilio",
            PROVIDER_META="meta",
        )

    def test_provider_name_is_normalized(self):
        channel = self.make_channel(
            provider="TWILIO",
        )

        self.assertEqual(
            get_whatsapp_provider_name(channel),
            "twilio",
        )

    def test_ready_twilio_channel_is_ready(self):
        channel = self.make_channel(
            provider="twilio",
            is_twilio_ready=True,
        )

        self.assertTrue(
            is_whatsapp_channel_ready(channel)
        )

    def test_unready_twilio_channel_is_not_ready(self):
        channel = self.make_channel(
            provider="twilio",
            is_twilio_ready=False,
        )

        self.assertFalse(
            is_whatsapp_channel_ready(channel)
        )

    def test_meta_is_not_ready_until_implementation(self):
        channel = self.make_channel(
            provider="meta",
        )

        self.assertFalse(
            is_whatsapp_channel_ready(channel)
        )

    def test_inactive_channel_is_not_ready(self):
        channel = self.make_channel(
            is_active=False,
        )

        self.assertFalse(
            is_whatsapp_channel_ready(channel)
        )

    def test_channel_without_company_is_not_ready(self):
        channel = self.make_channel(
            company_id=None,
        )

        self.assertFalse(
            is_whatsapp_channel_ready(channel)
        )

    def test_channel_without_warehouse_is_not_ready(self):
        channel = self.make_channel(
            warehouse_id=None,
        )

        self.assertFalse(
            is_whatsapp_channel_ready(channel)
        )

    def test_unknown_provider_is_not_ready(self):
        channel = self.make_channel(
            provider="unknown",
        )

        self.assertFalse(
            is_whatsapp_channel_ready(channel)
        )
