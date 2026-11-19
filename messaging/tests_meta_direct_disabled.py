"""
Migracion a Twilio - Paso 2: Meta directo apagado sin borrarlo.

Decision de Manuel (25-sep-2026): ningun numero NUEVO puede conectarse por
Meta directo; los que ya son de Meta (Jazmin) siguen funcionando hasta su
migracion. Ver messaging/provider_policy.py.
"""

from django.core.exceptions import ValidationError
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings

from companies.models import Company
from inventory.models import Warehouse
from messaging.context_processors import whatsapp_providers
from messaging.forms import OwnerWhatsAppNumberForm
from messaging.meta_embedded_signup import (
    MetaEmbeddedSignupConfigurationError,
    get_embedded_signup_settings,
)
from messaging.models import WhatsAppNumber
from messaging.provider_policy import (
    meta_direct_connection_blocked,
    meta_direct_new_connections_enabled,
)


class PoliticaMetaDirectoTests(SimpleTestCase):
    def test_apagado_por_omision(self):
        self.assertFalse(meta_direct_new_connections_enabled())

    @override_settings(WHATSAPP_META_DIRECT_ENABLED=False)
    def test_bloquea_numero_nuevo_y_cambio_hacia_meta(self):
        self.assertTrue(
            meta_direct_connection_blocked(provider="meta", previous_provider=None)
        )
        self.assertTrue(
            meta_direct_connection_blocked(provider="meta", previous_provider="twilio")
        )

    @override_settings(WHATSAPP_META_DIRECT_ENABLED=False)
    def test_no_bloquea_meta_existente_ni_otros_proveedores(self):
        self.assertFalse(
            meta_direct_connection_blocked(provider="meta", previous_provider="meta")
        )
        self.assertFalse(
            meta_direct_connection_blocked(provider="twilio", previous_provider=None)
        )
        self.assertFalse(
            meta_direct_connection_blocked(provider="twilio", previous_provider="meta")
        )

    @override_settings(WHATSAPP_META_DIRECT_ENABLED=True)
    def test_encendido_permite_altas_nuevas(self):
        self.assertFalse(
            meta_direct_connection_blocked(provider="meta", previous_provider=None)
        )

    @override_settings(WHATSAPP_META_DIRECT_ENABLED=False)
    def test_menu_no_muestra_meta(self):
        context = whatsapp_providers(RequestFactory().get("/"))
        self.assertEqual(context, {"whatsapp_meta_direct_enabled": False})

    @override_settings(
        META_EMBEDDED_SIGNUP_ENABLED=True, WHATSAPP_META_DIRECT_ENABLED=False
    )
    def test_embedded_signup_de_meta_apagado_aunque_su_bandera_siga_encendida(self):
        with self.assertRaises(MetaEmbeddedSignupConfigurationError):
            get_embedded_signup_settings()


@override_settings(WHATSAPP_META_DIRECT_ENABLED=False)
class ModeloMetaDirectoTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(
            name="Empresa Meta Apagado", slug="empresa-meta-apagado", is_active=True
        )
        cls.warehouse = Warehouse.objects.create(
            company=cls.company, name="Bodega MA", code="MA-1", is_active=True
        )

    def new_number(self, provider, **extra):
        return WhatsAppNumber(
            company=self.company,
            warehouse=self.warehouse,
            display_number="+525500099001",
            channel_name="Canal prueba",
            provider=provider,
            activation_status=WhatsAppNumber.STATUS_DISCONNECTED,
            twilio_enabled=False,
            meta_enabled=False,
            **extra,
        )

    def create_existing_meta(self):
        with override_settings(WHATSAPP_META_DIRECT_ENABLED=True):
            number = self.new_number(
                WhatsAppNumber.PROVIDER_META, phone_number_id="999000111222"
            )
            number.save()
        return number

    def test_no_se_puede_crear_un_numero_nuevo_de_meta(self):
        with self.assertRaises(ValidationError) as ctx:
            self.new_number(
                WhatsAppNumber.PROVIDER_META, phone_number_id="999000111333"
            ).save()
        self.assertIn("provider", ctx.exception.message_dict)
        self.assertFalse(
            WhatsAppNumber.objects.filter(provider=WhatsAppNumber.PROVIDER_META).exists()
        )

    def test_un_numero_de_meta_existente_se_sigue_pudiendo_editar(self):
        number = self.create_existing_meta()
        number.channel_name = "Jazmin ventas"
        number.save()
        number.refresh_from_db()
        self.assertEqual(number.channel_name, "Jazmin ventas")
        self.assertEqual(number.provider, WhatsAppNumber.PROVIDER_META)

    def test_no_se_puede_pasar_un_numero_de_twilio_a_meta(self):
        number = self.new_number(WhatsAppNumber.PROVIDER_TWILIO)
        number.save()
        number.provider = WhatsAppNumber.PROVIDER_META
        number.phone_number_id = "999000111444"
        with self.assertRaises(ValidationError):
            number.save()

    def test_un_numero_de_meta_si_se_puede_pasar_a_twilio(self):
        # Es justo la migracion de Jazmin.
        number = self.create_existing_meta()
        number.provider = WhatsAppNumber.PROVIDER_TWILIO
        number.save()
        number.refresh_from_db()
        self.assertEqual(number.provider, WhatsAppNumber.PROVIDER_TWILIO)

    def test_twilio_nuevo_no_se_afecta(self):
        number = self.new_number(WhatsAppNumber.PROVIDER_TWILIO)
        number.save()
        self.assertIsNotNone(number.pk)

    def test_formulario_de_alta_no_ofrece_meta(self):
        form = OwnerWhatsAppNumberForm()
        values = [value for value, _ in form.fields["provider"].choices]
        self.assertNotIn(WhatsAppNumber.PROVIDER_META, values)
        self.assertIn(WhatsAppNumber.PROVIDER_TWILIO, values)

    def test_formulario_de_numero_meta_existente_conserva_la_opcion(self):
        number = self.create_existing_meta()
        form = OwnerWhatsAppNumberForm(instance=number)
        values = [value for value, _ in form.fields["provider"].choices]
        self.assertIn(WhatsAppNumber.PROVIDER_META, values)
