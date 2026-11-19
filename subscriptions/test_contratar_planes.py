"""Contratar Esencial / Profesional / Máximo en línea con Stripe Checkout."""

from datetime import timedelta
from io import StringIO
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company
from inventory.models import Warehouse
from subscriptions.models import Plan, Subscription


@override_settings(STRIPE_SECRET_KEY="sk_test_prueba")
class ContratarPlanesTests(TestCase):
    def setUp(self):
        call_command("init_plans", stdout=StringIO())
        hoy = timezone.localdate()
        self.empresa = Company.objects.create(name="Textiles", slug="textiles-planes")
        Warehouse.objects.create(company=self.empresa, name="Central")
        self.suscripcion = Subscription.objects.create(
            company=self.empresa,
            plan=Plan.objects.get(slug="trial-14-dias"),
            status=Subscription.STATUS_TRIAL,
            start_date=hoy - timedelta(days=20),
            end_date=hoy - timedelta(days=6),
        )
        self.dueno = self._usuario("dueno@planes.test", "owner")
        self.vendedor = self._usuario("vendedor@planes.test", "sales")

    def _usuario(self, correo, rol):
        usuario = get_user_model().objects.create_user(username=correo, email=correo, password="Clave12345!")
        UserProfile.objects.create(user=usuario, company=self.empresa, role=rol, is_active=True)
        return usuario

    def _activar(self, slug, price):
        Plan.objects.filter(slug=slug).update(stripe_price_id=price, is_active=True)

    def _checkout(self, usuario, **datos):
        self.client.force_login(usuario)
        sesion = SimpleNamespace(url="https://checkout.stripe.test/c/abc")
        with mock.patch("subscriptions.views.stripe.checkout.Session.create", return_value=sesion) as crear:
            respuesta = self.client.post(reverse("subscriptions:create_checkout_session"), datos)
        return respuesta, crear

    def test_el_dueno_contrata_el_plan_elegido_con_el_precio_de_la_base(self):
        self._activar("profesional", "price_pro_1")
        respuesta, crear = self._checkout(self.dueno, plan="profesional")
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(respuesta["Location"], "https://checkout.stripe.test/c/abc")
        kwargs = crear.call_args.kwargs
        self.assertEqual(kwargs["line_items"], [{"price": "price_pro_1", "quantity": 1}])
        self.assertEqual(kwargs["metadata"]["plan_slug"], "profesional")
        self.assertEqual(kwargs["metadata"]["company_id"], str(self.empresa.pk))
        self.assertEqual(kwargs["client_reference_id"], str(self.empresa.pk))

    def test_plan_sin_precio_inactivo_o_ajeno_no_genera_cobro(self):
        self._activar("esencial", "price_es_1")
        Plan.objects.filter(slug="wms360-premium").update(stripe_price_id="price_viejo", is_active=True)
        for slug in ("maximo", "wms360-premium", "trial-14-dias", "no-existe"):
            with self.subTest(slug=slug):
                respuesta, crear = self._checkout(self.dueno, plan=slug)
                crear.assert_not_called()
                self.assertEqual(respuesta["Location"], reverse("subscriptions:subscription_expired"))

    def test_solo_dueno_o_administrador(self):
        self._activar("esencial", "price_es_1")
        _, crear = self._checkout(self.vendedor, plan="esencial")
        crear.assert_not_called()

    def test_la_pantalla_muestra_los_planes_contratables(self):
        self._activar("esencial", "price_es_1")
        self._activar("maximo", "price_max_1")
        self.client.force_login(self.dueno)
        contenido = self.client.get(reverse("subscriptions:subscription_expired")).content.decode()
        self.assertIn('value="esencial"', contenido)
        self.assertIn('value="maximo"', contenido)
        self.assertNotIn('value="profesional"', contenido)  # sin precio todavía
        self.assertIn("Contratar Esencial", contenido)
        self.assertNotIn("Renovar suscripción", contenido)

    def test_sin_planes_configurados_se_conserva_el_boton_anterior(self):
        self.client.force_login(self.dueno)
        contenido = self.client.get(reverse("subscriptions:subscription_expired")).content.decode()
        self.assertNotIn('name="plan"', contenido)
        self.assertIn("Renovar suscripción", contenido)

    def test_comando_configura_y_activa(self):
        salida = StringIO()
        call_command(
            "configurar_planes_stripe",
            "--esencial", "price_es_9", "--maximo", "price_max_9",
            "--sin-validar-stripe", stdout=salida,
        )
        self.assertFalse(Plan.objects.get(slug="esencial").is_active)  # vista previa
        call_command(
            "configurar_planes_stripe",
            "--esencial", "price_es_9", "--maximo", "price_max_9",
            "--sin-validar-stripe", "--aplicar", stdout=StringIO(),
        )
        esencial = Plan.objects.get(slug="esencial")
        self.assertEqual((esencial.stripe_price_id, esencial.is_active), ("price_es_9", True))
        # init_plans no pisa lo configurado.
        call_command("init_plans", stdout=StringIO())
        esencial.refresh_from_db()
        self.assertEqual((esencial.stripe_price_id, esencial.is_active), ("price_es_9", True))

    def test_comando_rechaza_datos_malos(self):
        for args in (
            ["--esencial", "prod_123"],
            ["--esencial", "price_x", "--maximo", "price_x"],
            [],
        ):
            with self.subTest(args=args), self.assertRaises(CommandError):
                call_command("configurar_planes_stripe", *args, "--sin-validar-stripe", stdout=StringIO())

    def test_comando_valida_contra_stripe(self):
        falso = {"active": True, "type": "recurring", "recurring": {"interval": "month"}, "currency": "mxn", "unit_amount": 99900}
        with mock.patch("stripe.Price.retrieve", return_value=falso):
            call_command("configurar_planes_stripe", "--esencial", "price_ok", "--aplicar", stdout=StringIO())
        self.assertTrue(Plan.objects.get(slug="esencial").is_active)
        malo = dict(falso, unit_amount=50000)
        with mock.patch("stripe.Price.retrieve", return_value=malo), self.assertRaises(CommandError):
            call_command("configurar_planes_stripe", "--maximo", "price_malo", stdout=StringIO())


class ProductosSinLimiteTests(TestCase):
    def test_plan_sin_limite_de_productos_nunca_bloquea(self):
        from subscriptions.product_limits import get_company_product_capacity

        call_command("init_plans", stdout=StringIO())
        hoy = timezone.localdate()
        empresa = Company.objects.create(name="Sin limite", slug="sin-limite-prod")
        Subscription.objects.create(
            company=empresa,
            plan=Plan.objects.get(slug="maximo"),
            status=Subscription.STATUS_ACTIVE,
            start_date=hoy,
            end_date=hoy + timedelta(days=30),
        )
        self.assertIsNone(Plan.objects.get(slug="maximo").max_products)
        capacidad = get_company_product_capacity(empresa)
        self.assertIsNone(capacidad["limit"])
        self.assertFalse(capacidad["limit_reached"])
