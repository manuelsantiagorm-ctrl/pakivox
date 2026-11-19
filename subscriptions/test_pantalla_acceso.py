from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company
from inventory.models import Warehouse
from subscriptions.models import Plan, Subscription


class PantallaAccesoRestringidoTests(TestCase):
    def preparar(self, vigente):
        hoy = timezone.localdate()
        plan = Plan.objects.create(name="Plan Prueba Acceso", slug="plan-acceso")
        empresa = Company.objects.create(name="Acceso", slug="acceso")
        Warehouse.objects.create(company=empresa, name="Central")
        Subscription.objects.create(
            company=empresa, plan=plan, status=Subscription.STATUS_ACTIVE,
            start_date=hoy - timedelta(days=60),
            end_date=hoy + timedelta(days=30) if vigente else hoy - timedelta(days=1),
        )
        usuario = get_user_model().objects.create_user(username="dueno@acceso.test", password="Clave12345!")
        UserProfile.objects.create(user=usuario, company=empresa, role="owner", is_active=True)
        self.client.force_login(usuario)
        return self.client.get(reverse("subscriptions:subscription_expired"))

    def test_con_suscripcion_vigente_no_ofrece_volver_a_pagar(self):
        respuesta = self.preparar(vigente=True)
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Esta función no está activa en tu plan")
        self.assertNotContains(respuesta, reverse("subscriptions:create_checkout_session"))
        self.assertNotContains(respuesta, "Suscripción vencida")

    def test_con_suscripcion_vencida_ofrece_renovar_sin_precio_inventado(self):
        respuesta = self.preparar(vigente=False)
        self.assertContains(respuesta, "Tu operación está en pausa")
        self.assertContains(respuesta, reverse("subscriptions:create_checkout_session"))
        self.assertNotContains(respuesta, "4,500")
        self.assertContains(respuesta, "Plan Prueba Acceso")
