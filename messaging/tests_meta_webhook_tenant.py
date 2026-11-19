from django.test import TestCase, override_settings

from companies.models import Company
from inventory.models import Warehouse

from messaging.meta_webhook import (
    MetaWebhookChannelError,
    resolve_meta_whatsapp_channel,
)
from messaging.models import WhatsAppNumber


# Estas pruebas cubren numeros que YA son de Meta directo (como el de
# Jazmin hasta su migracion a Twilio). Dar de alta numeros nuevos por Meta
# esta apagado desde el 25-sep-2026 (messaging/provider_policy.py), asi que
# los fixtures lo encienden para poder crear esos numeros.
@override_settings(WHATSAPP_META_DIRECT_ENABLED=True)
class MetaWebhookTenantIsolationTests(TestCase):
    """
    Pruebas reales de aislamiento multiempresa para Meta.

    Utilizan exclusivamente la base temporal de Django.

    NO:
    - llaman a Meta;
    - llaman a Twilio;
    - utilizan Internet;
    - utilizan la base real;
    - utilizan tokens reales.
    """

    def setUp(self):
        # ======================================================
        # COMPANY A
        # ======================================================

        self.company_a = Company.objects.create(
            name="Meta Webhook Company A",
            slug="meta-webhook-company-a",
            is_active=True,
        )

        self.warehouse_a = Warehouse.objects.create(
            company=self.company_a,
            name="Meta Bodega A",
            code="META-A",
            is_active=True,
        )

        self.channel_a = self.create_meta_channel(
            company=self.company_a,
            warehouse=self.warehouse_a,
            display_number="+525500010001",
            channel_name="Meta Ventas A",
            phone_number_id="111111111111111",
        )

        # ======================================================
        # COMPANY B
        # ======================================================

        self.company_b = Company.objects.create(
            name="Meta Webhook Company B",
            slug="meta-webhook-company-b",
            is_active=True,
        )

        self.warehouse_b = Warehouse.objects.create(
            company=self.company_b,
            name="Meta Bodega B",
            code="META-B",
            is_active=True,
        )

        self.channel_b = self.create_meta_channel(
            company=self.company_b,
            warehouse=self.warehouse_b,
            display_number="+525500020001",
            channel_name="Meta Ventas B",
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
        active=True,
        meta_enabled=True,
    ):
        """
        Primero crea disconnected para respetar full_clean().

        Después modifica exclusivamente la DB temporal para
        representar un canal Meta ya provisionado.

        Nunca se ejecuta contra la base real.
        """

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
                if active
                else WhatsAppNumber.STATUS_DISCONNECTED
            ),
            is_active=active,
            meta_enabled=meta_enabled,
        )

        channel.refresh_from_db()

        return channel

    # ==========================================================
    # 1. COMPANY A
    # ==========================================================

    def test_phone_number_id_a_resolves_only_company_a(self):
        channel = resolve_meta_whatsapp_channel(
            "111111111111111"
        )

        self.assertEqual(
            channel.pk,
            self.channel_a.pk,
        )

        self.assertEqual(
            channel.company_id,
            self.company_a.pk,
        )

        self.assertEqual(
            channel.warehouse_id,
            self.warehouse_a.pk,
        )

        self.assertNotEqual(
            channel.company_id,
            self.company_b.pk,
        )

    # ==========================================================
    # 2. COMPANY B
    # ==========================================================

    def test_phone_number_id_b_resolves_only_company_b(self):
        channel = resolve_meta_whatsapp_channel(
            "222222222222222"
        )

        self.assertEqual(
            channel.pk,
            self.channel_b.pk,
        )

        self.assertEqual(
            channel.company_id,
            self.company_b.pk,
        )

        self.assertEqual(
            channel.warehouse_id,
            self.warehouse_b.pk,
        )

        self.assertNotEqual(
            channel.company_id,
            self.company_a.pk,
        )

    # ==========================================================
    # 3. PHONE NUMBER ID DESCONOCIDO
    # ==========================================================

    def test_unknown_phone_number_id_is_rejected(self):
        with self.assertRaises(
            MetaWebhookChannelError
        ):
            resolve_meta_whatsapp_channel(
                "999999999999999"
            )

    # ==========================================================
    # 4. PHONE NUMBER ID VACÍO
    # ==========================================================

    def test_empty_phone_number_id_is_rejected(self):
        with self.assertRaises(
            MetaWebhookChannelError
        ):
            resolve_meta_whatsapp_channel(
                ""
            )

    # ==========================================================
    # 5. PHONE NUMBER ID NO NUMÉRICO
    # ==========================================================

    def test_non_numeric_phone_number_id_is_rejected(self):
        with self.assertRaises(
            MetaWebhookChannelError
        ):
            resolve_meta_whatsapp_channel(
                "META-ABC-123"
            )

    # ==========================================================
    # 6. META DESHABILITADO
    # ==========================================================

    def test_disabled_meta_channel_is_rejected(self):
        WhatsAppNumber.objects.filter(
            pk=self.channel_a.pk,
        ).update(
            meta_enabled=False,
        )

        with self.assertRaises(
            MetaWebhookChannelError
        ):
            resolve_meta_whatsapp_channel(
                self.channel_a.phone_number_id
            )

    # ==========================================================
    # 7. CANAL INACTIVO
    # ==========================================================

    def test_inactive_meta_channel_is_rejected(self):
        WhatsAppNumber.objects.filter(
            pk=self.channel_a.pk,
        ).update(
            activation_status=(
                WhatsAppNumber.STATUS_DISCONNECTED
            ),
            is_active=False,
        )

        with self.assertRaises(
            MetaWebhookChannelError
        ):
            resolve_meta_whatsapp_channel(
                self.channel_a.phone_number_id
            )

    # ==========================================================
    # 8. COMPANY INACTIVA
    # ==========================================================

    def test_inactive_company_channel_is_rejected(self):
        Company.objects.filter(
            pk=self.company_a.pk,
        ).update(
            is_active=False,
        )

        with self.assertRaises(
            MetaWebhookChannelError
        ):
            resolve_meta_whatsapp_channel(
                self.channel_a.phone_number_id
            )

    # ==========================================================
    # 9. BODEGA INACTIVA
    # ==========================================================

    def test_inactive_warehouse_channel_is_rejected(self):
        Warehouse.objects.filter(
            pk=self.warehouse_a.pk,
        ).update(
            is_active=False,
        )

        with self.assertRaises(
            MetaWebhookChannelError
        ):
            resolve_meta_whatsapp_channel(
                self.channel_a.phone_number_id
            )

    # ==========================================================
    # 10. PROVEEDOR TWILIO NO PUEDE ENTRAR POR ENDPOINT META
    # ==========================================================

    def test_twilio_channel_is_rejected_by_meta_resolver(self):
        twilio_channel = WhatsAppNumber.objects.create(
            company=self.company_a,
            warehouse=self.warehouse_a,
            display_number="+525500030001",
            channel_name="Twilio No Meta",
            provider=WhatsAppNumber.PROVIDER_TWILIO,
            phone_number_id="333333333333333",
            activation_status=(
                WhatsAppNumber.STATUS_DISCONNECTED
            ),
            twilio_enabled=False,
            meta_enabled=False,
        )

        WhatsAppNumber.objects.filter(
            pk=twilio_channel.pk,
        ).update(
            activation_status=(
                WhatsAppNumber.STATUS_ACTIVE
            ),
            is_active=True,
            meta_enabled=True,
        )

        with self.assertRaises(
            MetaWebhookChannelError
        ):
            resolve_meta_whatsapp_channel(
                "333333333333333"
            )

    # ==========================================================
    # 11. NO EXISTE FALLBACK HACIA OTRA COMPANY
    # ==========================================================

    def test_no_cross_company_fallback_exists(self):
        """
        Aunque Company B tenga un canal Meta válido,
        si se deshabilita el ID solicitado de Company A
        jamás debemos devolver el canal de Company B.
        """

        WhatsAppNumber.objects.filter(
            pk=self.channel_a.pk,
        ).update(
            meta_enabled=False,
        )

        with self.assertRaises(
            MetaWebhookChannelError
        ):
            resolve_meta_whatsapp_channel(
                "111111111111111"
            )

        # Company B permanece perfectamente válida,
        # pero nunca puede utilizarse como fallback.
        channel_b = resolve_meta_whatsapp_channel(
            "222222222222222"
        )

        self.assertEqual(
            channel_b.company_id,
            self.company_b.pk,
        )
