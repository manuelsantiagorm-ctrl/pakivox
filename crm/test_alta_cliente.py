from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company
from crm.etapas import asegurar_etapas_iniciales
from crm.models import Customer, PipelineStage
from inventory.models import Warehouse
from subscriptions.models import Plan, Subscription


def empresa_con_dueno(slug, correo):
    hoy = timezone.localdate()
    plan, _ = Plan.objects.get_or_create(slug="plan-alta-cliente", defaults={"name": "Plan"})
    empresa = Company.objects.create(name=f"Empresa {slug}", slug=slug)
    Subscription.objects.create(
        company=empresa, plan=plan, status=Subscription.STATUS_ACTIVE,
        start_date=hoy, end_date=hoy + timedelta(days=30),
    )
    Warehouse.objects.create(company=empresa, name=f"Central {slug}")
    usuario = get_user_model().objects.create_user(username=correo, email=correo, password="Clave12345!")
    UserProfile.objects.create(user=usuario, company=empresa, role="owner", is_active=True)
    return empresa, usuario


class AltaClienteCRMTests(TestCase):
    def setUp(self):
        self.empresa, self.usuario = empresa_con_dueno("alta-cliente", "dueno@alta.test")
        self.ajena, _ = empresa_con_dueno("alta-cliente-ajena", "otro@alta.test")
        self.client.force_login(self.usuario)
        self.url = reverse("customer_create")

    def datos(self, **extra):
        datos = {
            "full_name": "Boutique Nueva",
            "business_name": "Boutique Nueva SA",
            "phone": "5512345678",
            "whatsapp": "5512345678",
            "customer_type": Customer._meta.get_field("customer_type").default or "customer",
            "source": Customer._meta.get_field("source").default or "manual",
            "estimated_monthly_purchase": "15000",
            "is_active": "on",
        }
        datos.update(extra)
        return datos

    def test_el_formulario_abre_dentro_del_crm(self):
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Crear cliente")

    def test_crea_el_cliente_en_la_empresa_del_usuario(self):
        respuesta = self.client.post(self.url, self.datos())
        cliente = Customer.objects.get(full_name="Boutique Nueva")
        self.assertEqual(cliente.company, self.empresa)
        self.assertRedirects(
            respuesta,
            reverse("customer_smart_profile", args=[cliente.pk]),
            fetch_redirect_response=False,
        )

    def test_no_duplica_el_telefono_dentro_de_la_empresa(self):
        Customer.objects.create(company=self.empresa, full_name="Ya existe", phone="5512345678")
        respuesta = self.client.post(self.url, self.datos())
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(Customer.objects.filter(company=self.empresa).count(), 1)

    def test_el_mismo_telefono_en_otra_empresa_no_estorba(self):
        Customer.objects.create(company=self.ajena, full_name="De otra empresa", phone="5512345678")
        self.client.post(self.url, self.datos())
        self.assertTrue(Customer.objects.filter(company=self.empresa, full_name="Boutique Nueva").exists())

    def test_requiere_sesion(self):
        self.client.logout()
        respuesta = self.client.get(self.url)
        self.assertEqual(respuesta.status_code, 302)
        self.assertFalse(Customer.objects.exists())

    def test_el_inicio_del_crm_ya_no_manda_al_admin(self):
        respuesta = self.client.get(reverse("crm_home"))
        self.assertContains(respuesta, self.url)
        self.assertNotContains(respuesta, "/admin/crm/customer/add/")


class EtapasInicialesTests(TestCase):
    def setUp(self):
        self.empresa, self.usuario = empresa_con_dueno("etapas", "dueno@etapas.test")

    def test_crea_las_etapas_una_sola_vez(self):
        self.assertEqual(asegurar_etapas_iniciales(self.empresa), 5)
        self.assertEqual(asegurar_etapas_iniciales(self.empresa), 0)
        self.assertEqual(PipelineStage.objects.filter(company=self.empresa).count(), 5)
        self.assertTrue(PipelineStage.objects.get(company=self.empresa, name="Ganado").is_won)

    def test_no_toca_empresas_que_ya_tienen_etapas(self):
        PipelineStage.objects.create(company=self.empresa, name="Mi etapa", order=1)
        self.assertEqual(asegurar_etapas_iniciales(self.empresa), 0)
        self.assertEqual(PipelineStage.objects.filter(company=self.empresa).count(), 1)

    def test_sin_empresa_no_hace_nada(self):
        self.assertEqual(asegurar_etapas_iniciales(None), 0)

    def test_el_kanban_de_una_empresa_nueva_muestra_etapas(self):
        self.client.force_login(self.usuario)
        respuesta = self.client.get(reverse("crm_kanban"))
        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Nuevo prospecto")
        self.assertNotContains(respuesta, "/admin/crm/pipelinestage/add/")
