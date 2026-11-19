from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from analytics.landing_content import (
    build_landing_context,
    build_sales_whatsapp_url,
    get_public_plans,
    get_trial_days,
)
from subscriptions.plan_catalog import PLAN_CATALOG


def _catalog(slug):
    return next(plan["synced"] for plan in PLAN_CATALOG if plan["slug"] == slug)


class LandingPlansTests(SimpleTestCase):
    def test_public_plans_follow_catalog_order_and_prices(self):
        plans = get_public_plans()

        self.assertEqual([p["slug"] for p in plans], ["esencial", "profesional", "maximo"])
        for plan in plans:
            price = int(_catalog(plan["slug"])["price"])
            self.assertEqual(plan["price_display"], f"{price:,}")

    def test_only_profesional_is_recommended(self):
        recommended = [p["slug"] for p in get_public_plans() if p["recommended"]]
        self.assertEqual(recommended, ["profesional"])

    def test_features_use_catalog_limits(self):
        for plan in get_public_plans():
            fields = _catalog(plan["slug"])
            text = " | ".join(plan["features"])
            self.assertIn(f"{fields['max_users']:,} usuarios", text)
            if fields["max_products"] is None:
                self.assertIn("Productos ilimitados", text)
            else:
                self.assertIn(f"{fields['max_products']:,} productos", text)

    def test_no_se_anuncia_whatsapp_conectado(self):
        # Alta de numeros pausada (nov-2026): ningun plan promete numeros,
        # pedidos desde el chat ni supervision de conversaciones.
        for plan in get_public_plans():
            text = " | ".join(plan["features"])
            self.assertNotIn("números de WhatsApp", text)
            self.assertNotIn("número de WhatsApp", text)
            self.assertNotIn("chat de WhatsApp", text)
            self.assertNotIn("Supervisión", text)
            self.assertIn("sin costo por mensaje", text)

    def test_precio_anual_es_diez_meses(self):
        for plan in get_public_plans():
            price = int(_catalog(plan["slug"])["price"])
            self.assertEqual(plan["annual_display"], f"{price * 10:,}")

    def test_traspasos_solo_con_varias_bodegas(self):
        for plan in get_public_plans():
            listed = "Traspasos entre bodegas" in plan["features"]
            self.assertEqual(listed, _catalog(plan["slug"])["max_warehouses"] > 1)

    def test_unlimited_ai_is_worded_as_unlimited(self):
        maximo = next(p for p in get_public_plans() if p["slug"] == "maximo")
        if _catalog("maximo")["max_ai_queries"] is None:
            self.assertIn("Asistente con IA sin límite de consultas", maximo["features"])

    def test_trial_days_come_from_trial_slug(self):
        self.assertEqual(get_trial_days(), 14)


class SalesWhatsappUrlTests(SimpleTestCase):
    def test_ten_digit_mexican_number_gets_country_code(self):
        url = build_sales_whatsapp_url("55 1234-5678")
        self.assertTrue(url.startswith("https://wa.me/525512345678?text="))

    def test_number_with_country_code_is_kept(self):
        url = build_sales_whatsapp_url("+52 1 55 1234 5678")
        self.assertTrue(url.startswith("https://wa.me/5215512345678?text="))

    def test_empty_or_invalid_number_hides_button(self):
        self.assertEqual(build_sales_whatsapp_url(""), "")
        self.assertEqual(build_sales_whatsapp_url(None), "")
        self.assertEqual(build_sales_whatsapp_url("12345"), "")

    @override_settings(PAKIVOX_SALES_WHATSAPP="5512345678")
    def test_context_reads_number_from_settings(self):
        self.assertIn("wa.me/525512345678", build_landing_context()["sales_whatsapp_url"])


class LandingPageRenderTests(TestCase):
    @override_settings(PAKIVOX_SALES_WHATSAPP="")
    def test_landing_shows_prices_and_hides_whatsapp_without_number(self):
        response = self.client.get(reverse("landing"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "El sistema para mayoristas")
        self.assertContains(response, "Sin cobros por mensaje ni por pedido")
        self.assertContains(response, "2 meses gratis")
        self.assertContains(response, "Productos ilimitados")
        self.assertNotContains(response, "números de WhatsApp")
        self.assertContains(response, "$1,999")
        self.assertNotContains(response, "wa.me/")

    @override_settings(PAKIVOX_SALES_WHATSAPP="5512345678")
    def test_landing_shows_whatsapp_button_with_number(self):
        response = self.client.get(reverse("landing"))

        self.assertContains(response, "https://wa.me/525512345678")
        self.assertContains(response, 'class="wa-float"')
