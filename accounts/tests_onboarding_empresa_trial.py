"""
PLANES Fase 3 paso 4b - onboarding_empresa_view usa el Trial oficial y
no deja empresas huerfanas.

Punto clave de la base real: UserProfile.company es obligatorio, asi que un
usuario que todavia no tiene empresa NO tiene perfil. Estas pruebas parten de
ese estado real (usuario sin perfil) y no crean perfiles vacios.

Se llama a la vista directamente con RequestFactory para no depender de los
middlewares; render se parchea porque solo interesa que se pida la plantilla
correcta.
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.messages.storage.fallback import FallbackStorage
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts import views as accounts_views
from accounts.models import UserProfile
from accounts.services import TRIAL_PLAN_SLUG
from companies.models import Company
from subscriptions.models import Plan, Subscription

User = get_user_model()

COMPANY_NAME = "Empresa Onboarding Cuatro B"


class OnboardingEmpresaTrialTests(TestCase):
    def setUp(self):
        # Usuario recien registrado, sin empresa y por lo tanto sin perfil.
        self.user = User.objects.create_user(
            username="onboarding4b@example.com",
            email="onboarding4b@example.com",
            password="clave-de-prueba-4b",
        )

    def profile(self):
        return UserProfile.objects.filter(user=self.user).first()

    def post(self, data):
        request = RequestFactory().post("/accounts/onboarding-empresa/", data)
        request.user = self.user
        request.session = {}
        request._messages = FallbackStorage(request)
        return request, accounts_views.onboarding_empresa_view(request)

    def get(self):
        request = RequestFactory().get("/accounts/onboarding-empresa/")
        request.user = self.user
        request.session = {}
        request._messages = FallbackStorage(request)
        return request, accounts_views.onboarding_empresa_view(request)

    def make_trial_plan(self, is_active=True):
        plan, _ = Plan.objects.update_or_create(
            slug=TRIAL_PLAN_SLUG,
            defaults={
                "name": "Trial de prueba",
                "price": 0,
                "max_users": 4,
                "max_products": 100,
                "max_warehouses": 1,
                "is_active": is_active,
            },
        )
        return plan

    def remove_trial_plan(self):
        Plan.objects.filter(slug=TRIAL_PLAN_SLUG).delete()

    def assert_nothing_created(self):
        self.assertFalse(Company.objects.filter(name=COMPANY_NAME).exists())
        self.assertIsNone(self.profile())
        self.assertFalse(
            Subscription.objects.filter(company__name=COMPANY_NAME).exists()
        )

    # ------------------------------------------------------------------
    # Camino feliz
    # ------------------------------------------------------------------
    def test_el_usuario_nuevo_queda_como_dueno_de_su_empresa(self):
        self.make_trial_plan()

        request, response = self.post({"company_name": COMPANY_NAME})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("companies:onboarding_wms360"))

        company = Company.objects.get(name=COMPANY_NAME)
        profile = self.profile()
        self.assertIsNotNone(profile)
        self.assertEqual(profile.company_id, company.pk)
        self.assertEqual(profile.role, "owner")
        self.assertTrue(profile.is_active)

    def test_la_empresa_nueva_recibe_el_trial_oficial_por_14_dias(self):
        trial = self.make_trial_plan()

        self.post({"company_name": COMPANY_NAME})

        company = Company.objects.get(name=COMPANY_NAME)
        subscription = Subscription.objects.get(company=company)
        today = timezone.localdate()

        self.assertEqual(subscription.plan_id, trial.pk)
        self.assertEqual(subscription.status, Subscription.STATUS_TRIAL)
        self.assertEqual(subscription.start_date, today)
        self.assertEqual(subscription.trial_ends_at, today + timedelta(days=14))
        self.assertEqual(subscription.end_date, subscription.trial_ends_at)
        self.assertFalse(subscription.auto_renew)

    def test_ya_no_se_crea_el_plan_suelto_prueba_14_dias(self):
        self.make_trial_plan()

        self.post({"company_name": COMPANY_NAME})

        self.assertFalse(Plan.objects.filter(slug="prueba-14-dias").exists())

    def test_muestra_el_mensaje_de_bienvenida(self):
        self.make_trial_plan()

        request, response = self.post({"company_name": COMPANY_NAME})

        texts = [str(message) for message in get_messages(request)]
        self.assertEqual(len(texts), 1)
        self.assertIn(COMPANY_NAME, texts[0])

    def test_dos_empresas_con_el_mismo_nombre_reciben_slugs_distintos(self):
        self.make_trial_plan()
        Company.objects.create(name=COMPANY_NAME, slug="empresa-onboarding-cuatro-b")

        self.post({"company_name": COMPANY_NAME})

        mine = self.profile().company
        self.assertEqual(mine.slug, "empresa-onboarding-cuatro-b-1")

    # ------------------------------------------------------------------
    # Regresion: ya no se intenta crear un perfil vacio (antes fallaba con
    # IntegrityError en cada onboarding y el error se mandaba a Sentry)
    # ------------------------------------------------------------------
    def test_onboarding_normal_no_manda_errores_falsos_a_sentry(self):
        self.make_trial_plan()

        with patch("sentry_sdk.capture_exception") as exception_mock, patch(
            "sentry_sdk.capture_message"
        ) as message_mock:
            self.post({"company_name": COMPANY_NAME})

        exception_mock.assert_not_called()
        message_mock.assert_not_called()

    def test_ver_el_formulario_no_manda_errores_a_sentry(self):
        with patch.object(accounts_views, "render") as render_mock, patch(
            "sentry_sdk.capture_exception"
        ) as exception_mock:
            render_mock.return_value = HttpResponse("formulario")
            self.get()

        exception_mock.assert_not_called()

    def test_solo_ver_el_formulario_no_crea_perfil_ni_empresa(self):
        self.make_trial_plan()

        with patch.object(accounts_views, "render") as render_mock:
            render_mock.return_value = HttpResponse("formulario")
            request, response = self.get()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(render_mock.call_args.args[1], "accounts/onboarding.html")
        self.assert_nothing_created()

    def test_sin_nombre_de_empresa_solo_muestra_el_formulario(self):
        self.make_trial_plan()

        with patch.object(accounts_views, "render") as render_mock:
            render_mock.return_value = HttpResponse("formulario")
            request, response = self.post({"company_name": ""})

        self.assertEqual(response.status_code, 200)
        self.assert_nothing_created()

    def test_quien_ya_tiene_empresa_no_crea_otra(self):
        self.make_trial_plan()
        existing = Company.objects.create(name="Empresa Vieja", slug="empresa-vieja")
        UserProfile.objects.create(
            user=self.user, company=existing, role="owner", is_active=True
        )

        companies_before = Company.objects.count()

        request, response = self.post({"company_name": COMPANY_NAME})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("companies:onboarding_wms360"))
        self.assertFalse(Company.objects.filter(name=COMPANY_NAME).exists())
        self.assertEqual(Company.objects.count(), companies_before)
        self.assertEqual(self.profile().company_id, existing.pk)

    # ------------------------------------------------------------------
    # Trial oficial ausente o inactivo
    # ------------------------------------------------------------------
    def test_sin_trial_oficial_no_se_crea_nada_y_se_avisa(self):
        self.remove_trial_plan()

        with patch.object(accounts_views, "render") as render_mock, patch(
            "sentry_sdk.capture_message"
        ) as sentry_mock:
            render_mock.return_value = HttpResponse("formulario")
            request, response = self.post({"company_name": COMPANY_NAME})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(render_mock.call_args.args[1], "accounts/onboarding.html")
        sentry_mock.assert_called_once()

        self.assert_nothing_created()
        self.assertFalse(Plan.objects.filter(slug="prueba-14-dias").exists())

        texts = [str(message) for message in get_messages(request)]
        self.assertEqual(len(texts), 1)
        self.assertIn("periodo de prueba", texts[0])

    def test_un_trial_inactivo_no_cuenta_como_oficial(self):
        self.make_trial_plan(is_active=False)

        with patch.object(accounts_views, "render") as render_mock, patch(
            "sentry_sdk.capture_message"
        ):
            render_mock.return_value = HttpResponse("formulario")
            self.post({"company_name": COMPANY_NAME})

        self.assert_nothing_created()

    # ------------------------------------------------------------------
    # Todo o nada
    # ------------------------------------------------------------------
    def test_si_falla_la_suscripcion_no_queda_empresa_ni_perfil_a_medias(self):
        self.make_trial_plan()

        with patch.object(
            Subscription.objects,
            "update_or_create",
            side_effect=RuntimeError("fallo simulado"),
        ):
            with self.assertRaises(RuntimeError):
                self.post({"company_name": COMPANY_NAME})

        self.assert_nothing_created()

    def test_si_falla_el_perfil_no_queda_empresa_ni_suscripcion_a_medias(self):
        self.make_trial_plan()

        with patch.object(
            UserProfile.objects,
            "create",
            side_effect=RuntimeError("fallo simulado"),
        ):
            with self.assertRaises(RuntimeError):
                self.post({"company_name": COMPANY_NAME})

        self.assert_nothing_created()
