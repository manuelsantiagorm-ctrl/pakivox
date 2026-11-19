"""
Pruebas de acceso: freno a contraseñas, alta cerrada, rutas privadas y errores.
"""

import re

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver, reverse

from accounts import login_throttle
from accounts.adapters import CompanyRegistrationAccountAdapter, CustomSocialAccountAdapter

User = get_user_model()


class FrenoInicioSesionTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username="dueno@demo.test", email="dueno@demo.test", password="ClaveSegura123!"
        )
        self.url = reverse("accounts:login")

    def tearDown(self):
        cache.clear()

    def intentar(self, password, ip="10.0.0.1"):
        return self.client.post(
            self.url, {"username": "dueno@demo.test", "password": password}, REMOTE_ADDR=ip
        )

    def test_bloquea_tras_cinco_fallos_aunque_luego_la_clave_sea_correcta(self):
        for _ in range(login_throttle.MAX_FALLOS_POR_CORREO):
            self.assertEqual(self.intentar("mala").status_code, 200)
        respuesta = self.intentar("ClaveSegura123!")
        self.assertEqual(respuesta.status_code, 429)
        self.assertContains(respuesta, "pausamos los intentos", status_code=429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_el_bloqueo_por_correo_no_depende_de_la_ip(self):
        for indice in range(login_throttle.MAX_FALLOS_POR_CORREO):
            self.intentar("mala", ip=f"10.0.1.{indice}")
        self.assertEqual(self.intentar("ClaveSegura123!", ip="10.0.9.9").status_code, 429)

    def test_un_acceso_correcto_limpia_los_fallos_del_correo(self):
        for _ in range(login_throttle.MAX_FALLOS_POR_CORREO - 1):
            self.intentar("mala")
        respuesta = self.intentar("ClaveSegura123!")
        self.assertEqual(respuesta.status_code, 302)
        self.client.post(reverse("accounts:logout"))
        for _ in range(login_throttle.MAX_FALLOS_POR_CORREO - 1):
            self.intentar("mala")
        self.assertEqual(self.intentar("ClaveSegura123!").status_code, 302)

    def test_el_cache_no_guarda_el_correo_en_claro(self):
        self.intentar("mala")
        clave = login_throttle._clave("correo", "dueno@demo.test")
        self.assertNotIn("dueno", clave)
        self.assertEqual(cache.get(clave), 1)

    def test_si_el_cache_falla_no_se_bloquea_el_acceso(self):
        class CacheRoto:
            def get(self, *a, **k):
                raise ConnectionError("sin redis")

            add = incr = set = delete = get

        original = login_throttle.cache
        login_throttle.cache = CacheRoto()
        try:
            self.assertFalse(login_throttle.esta_bloqueado("x@y.z", "1.1.1.1"))
            login_throttle.registrar_fallo("x@y.z", "1.1.1.1")
        finally:
            login_throttle.cache = original


class AltaYRedireccionTests(TestCase):
    def test_alta_local_de_allauth_redirige_al_registro_de_empresas(self):
        respuesta = self.client.get("/accounts/signup/")
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(respuesta["Location"], reverse("accounts:company_register"))

    def test_alta_local_cerrada_y_google_abierto(self):
        self.assertFalse(CompanyRegistrationAccountAdapter().is_open_for_signup(None))
        self.assertTrue(CustomSocialAccountAdapter().is_open_for_signup(None, None))

    def test_cuenta_sin_empresa_va_al_onboarding_y_no_a_un_error_405(self):
        User.objects.create_user(username="nuevo@demo.test", email="nuevo@demo.test", password="ClaveSegura123!")
        respuesta = self.client.post(
            reverse("accounts:login"), {"username": "nuevo@demo.test", "password": "ClaveSegura123!"}
        )
        self.assertRedirects(respuesta, reverse("accounts:onboarding_empresa"), fetch_redirect_response=False)


@override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
class PaginasDeErrorTests(TestCase):
    def test_404_amigable_sin_detalles_internos(self):
        respuesta = self.client.get("/no-existe-esta-ruta/")
        self.assertEqual(respuesta.status_code, 404)
        self.assertContains(respuesta, "No encontramos esta página", status_code=404)
        self.assertNotContains(respuesta, "Traceback", status_code=404)
        self.assertNotContains(respuesta, "urlpatterns", status_code=404)


# Rutas que deben abrirse sin sesión. Cualquier otra que responda 200 a un
# visitante anónimo es una fuga y hace fallar la prueba.
RUTAS_PUBLICAS = {
    "landing",
    "login",
    "account_login",
    "company_register",
    "registration_email_sent",
    "verify_company_registration",
    "account_inactive",
    "account_email_verification_sent",
    "account_reset_password",
    "account_reset_password_done",
    "account_reset_password_from_key_done",
    "socialaccount_login_cancelled",
    "health_live",
    "health_ready",
}


def _rutas(patrones, prefijo=""):
    for patron in patrones:
        if isinstance(patron, URLResolver):
            yield from _rutas(patron.url_patterns, prefijo + str(patron.pattern))
        elif isinstance(patron, URLPattern):
            yield prefijo + str(patron.pattern), patron


def _concreta(ruta):
    ruta = re.sub(r"<int:[^>]+>", "1", ruta)
    ruta = re.sub(r"<uuid:[^>]+>", "00000000-0000-0000-0000-000000000000", ruta)
    ruta = re.sub(r"<[^>]+>", "x", ruta)
    return "/" + ruta.replace("^", "").replace("$", "").lstrip("/")


class RutasPrivadasTests(TestCase):
    def test_ninguna_ruta_privada_responde_a_un_visitante_sin_sesion(self):
        cliente = Client(raise_request_exception=False)
        abiertas = []
        for ruta, patron in _rutas(get_resolver().url_patterns):
            if "(?P" in ruta or ruta.startswith(("admin/", "static/", "media/")):
                continue
            respuesta = cliente.get(_concreta(ruta))
            if respuesta.status_code == 200 and patron.name not in RUTAS_PUBLICAS:
                abiertas.append(f"{_concreta(ruta)} ({patron.name})")
        self.assertEqual(abiertas, [], "Rutas privadas abiertas sin sesión")


class RecuperarContrasenaTests(TestCase):
    def test_el_login_ofrece_recuperar_y_el_flujo_envia_correo(self):
        from django.core import mail

        User.objects.create_user(username="olvido@demo.test", email="olvido@demo.test", password="ClaveSegura123!")
        login = self.client.get(reverse("accounts:login"))
        self.assertContains(login, reverse("account_reset_password"))

        pagina = self.client.get(reverse("account_reset_password"))
        self.assertContains(pagina, "acceso-cuenta")
        respuesta = self.client.post(reverse("account_reset_password"), {"email": "olvido@demo.test"})
        self.assertEqual(respuesta.status_code, 302)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("olvido@demo.test", mail.outbox[0].to)
