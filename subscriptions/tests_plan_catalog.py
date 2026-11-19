"""
PLANES Fase 3 - El catalogo de planes cumple lo decidido.

Todas son SimpleTestCase: el catalogo es puro Python y solo se consulta la
definicion del modelo (Plan._meta), sin tocar la base de datos.
"""

from decimal import Decimal

from django.test import SimpleTestCase

from subscriptions.models import Plan
from subscriptions.plan_catalog import (
    PLAN_CATALOG,
    TRIAL_SLUG,
    TRIAL_WHATSAPP_HARD_CAP,
    TRIAL_WHATSAPP_OPEN,
    trial_whatsapp_fields,
)
from subscriptions.whatsapp_quota_rules import (
    FREE_MESSAGES_PER_NUMBER,
    budget_in_messages,
    message_cost_mxn,
)

from subscriptions.tests_whatsapp_spend_quota import TWILIO, round_robin, simulate_spend

COST = message_cost_mxn("0.0085", "17.24")

BY_SLUG = {entry["slug"]: entry["synced"] for entry in PLAN_CATALOG}


def ceiling(plan, connected_numbers):
    """
    Mensajes al mes con `connected_numbers` conectados por Twilio y el
    trafico repartido parejo (tope de gasto en pesos, 25-sep-2026).
    """
    numbers = list(range(1, connected_numbers + 1))
    sent, _ = simulate_spend(
        fees={n: TWILIO for n in numbers},
        budget_mxn=plan["whatsapp_overage_budget_mxn"],
        order=round_robin(numbers, 20000),
    )
    return sum(sent.values())


class PlanCatalogShapeTests(SimpleTestCase):
    def test_los_cuatro_planes_en_orden(self):
        self.assertEqual(
            [entry["slug"] for entry in PLAN_CATALOG],
            [TRIAL_SLUG, "esencial", "profesional", "maximo"],
        )

    def test_los_planes_que_ya_existen_en_produccion_no_se_tocan(self):
        self.assertNotIn("wms360-premium", BY_SLUG)
        self.assertNotIn("prueba-14-dias", BY_SLUG)

    def test_todos_los_campos_existen_en_el_modelo_plan(self):
        for entry in PLAN_CATALOG:
            for field in list(entry["synced"]) + list(entry["create_only"]):
                with self.subTest(plan=entry["slug"], field=field):
                    Plan._meta.get_field(field)

    def test_cada_plan_define_los_mismos_campos(self):
        reference = set(PLAN_CATALOG[0]["synced"])
        for entry in PLAN_CATALOG:
            with self.subTest(plan=entry["slug"]):
                self.assertEqual(set(entry["synced"]), reference)

    def test_stripe_y_activo_no_se_pisan_en_cada_corrida(self):
        for entry in PLAN_CATALOG:
            with self.subTest(plan=entry["slug"]):
                self.assertNotIn("stripe_price_id", entry["synced"])
                self.assertNotIn("stripe_price_id", entry["create_only"])
                self.assertNotIn("is_active", entry["synced"])

    def test_solo_el_trial_nace_activo(self):
        for entry in PLAN_CATALOG:
            with self.subTest(plan=entry["slug"]):
                self.assertEqual(
                    entry["create_only"]["is_active"], entry["slug"] == TRIAL_SLUG
                )

    def test_los_niveles_son_opciones_validas_del_modelo(self):
        shipping = {key for key, _label in Plan.SHIPPING_LEVEL_CHOICES}
        analytics = {key for key, _label in Plan.ANALYTICS_LEVEL_CHOICES}

        for entry in PLAN_CATALOG:
            with self.subTest(plan=entry["slug"]):
                self.assertIn(entry["synced"]["shipping_level"], shipping)
                self.assertIn(entry["synced"]["analytics_level"], analytics)

    def test_ya_no_existe_el_nivel_de_analisis_sin_acceso(self):
        keys = [key for key, _label in Plan.ANALYTICS_LEVEL_CHOICES]
        self.assertEqual(keys, ["basic", "intermediate", "advanced"])


class PlanCatalogNumbersTests(SimpleTestCase):
    def test_precios(self):
        self.assertEqual(
            [BY_SLUG[slug]["price"] for slug in BY_SLUG],
            [Decimal("0"), Decimal("999"), Decimal("1999"), Decimal("2999")],
        )

    def test_limites_de_la_tabla_de_planes(self):
        expected = {
            # El Trial no tiene WhatsApp hoy: 0 numeros (ver TRIAL_WHATSAPP_OPEN).
            # Productos (nov-2026): 1,000 / 5,000 / sin limite (None).
            TRIAL_SLUG: (5, 1000, 1, 0),
            "esencial": (5, 1000, 1, 1),
            "profesional": (15, 5000, 3, 3),
            "maximo": (30, None, 5, 6),
        }
        for slug, (users, products, warehouses, numbers) in expected.items():
            with self.subTest(plan=slug):
                plan = BY_SLUG[slug]
                self.assertEqual(plan["max_users"], users)
                self.assertEqual(plan["max_products"], products)
                self.assertEqual(plan["max_warehouses"], warehouses)
                self.assertEqual(plan["max_whatsapp_numbers"], numbers)

    def test_trial_tiene_ia_con_limite_y_no_ilimitada(self):
        # Bug del init_plans anterior: has_ai sin max_ai_queries = ilimitada.
        trial = BY_SLUG[TRIAL_SLUG]
        self.assertTrue(trial["has_ai"])
        self.assertEqual(trial["max_ai_queries"], 10)

    def test_solo_el_maximo_tiene_ia_ilimitada(self):
        for slug, plan in BY_SLUG.items():
            with self.subTest(plan=slug):
                self.assertTrue(plan["has_ai"])
                self.assertEqual(plan["max_ai_queries"] is None, slug == "maximo")

    def test_consultas_de_ia(self):
        self.assertEqual(
            [BY_SLUG[slug]["max_ai_queries"] for slug in BY_SLUG],
            [10, 20, 50, None],
        )

    def test_trial_tiene_tope_duro_de_50_y_los_de_pago_no(self):
        self.assertEqual(TRIAL_WHATSAPP_HARD_CAP, 50)
        for slug, plan in BY_SLUG.items():
            with self.subTest(plan=slug):
                expected = 50 if slug == TRIAL_SLUG else None
                self.assertEqual(plan["max_whatsapp_messages_per_month"], expected)

    def test_colchones_en_pesos(self):
        self.assertEqual(
            [BY_SLUG[slug]["whatsapp_overage_budget_mxn"] for slug in BY_SLUG],
            [Decimal("0"), Decimal("86"), Decimal("286"), Decimal("586")],
        )

    def test_todos_los_planes_tienen_colchon_definido(self):
        # None significaria "plan dormido: solo cuenta y nunca bloquea".
        for slug, plan in BY_SLUG.items():
            with self.subTest(plan=slug):
                self.assertIsNotNone(plan["whatsapp_overage_budget_mxn"])

    def test_escalera_de_mensajes_con_todos_los_numeros_conectados(self):
        # Topes decididos el 25-sep-2026 (~1,000 / ~3,118 / ~6,296).
        self.assertEqual(ceiling(BY_SLUG["esencial"], 1), 997)
        self.assertEqual(ceiling(BY_SLUG["profesional"], 3), 3117)
        self.assertEqual(ceiling(BY_SLUG["maximo"], 6), 6295)

    def test_escalera_del_maximo_con_un_solo_numero(self):
        self.assertEqual(ceiling(BY_SLUG["maximo"], 1), 3147)

    def test_funciones_por_plan(self):
        self.assertEqual(
            [BY_SLUG[slug]["can_supervise_whatsapp"] for slug in BY_SLUG],
            [False, False, True, True],
        )
        for slug, plan in BY_SLUG.items():
            with self.subTest(plan=slug):
                self.assertEqual(plan["shipping_level"], "full")

        for slug in ("esencial", "profesional", "maximo"):
            with self.subTest(plan=slug):
                self.assertTrue(BY_SLUG[slug]["can_create_orders_from_chat"])
                self.assertTrue(BY_SLUG[slug]["has_whatsapp"])

    def test_el_trial_no_tiene_whatsapp_hoy(self):
        # La compuerta de acceso (access.py) prohibe WhatsApp en el Trial;
        # el catalogo no debe contradecirla.
        self.assertFalse(TRIAL_WHATSAPP_OPEN)

        trial = BY_SLUG[TRIAL_SLUG]
        self.assertFalse(trial["has_whatsapp"])
        self.assertEqual(trial["max_whatsapp_numbers"], 0)
        self.assertEqual(trial["max_whatsapp_agents"], 0)
        self.assertFalse(trial["can_create_orders_from_chat"])
        self.assertFalse(trial["can_supervise_whatsapp"])

    def test_trial_abierto_a_whatsapp_queda_como_se_decidio(self):
        opened = trial_whatsapp_fields(True)

        self.assertTrue(opened["has_whatsapp"])
        self.assertEqual(opened["max_whatsapp_numbers"], 1)
        self.assertEqual(opened["max_whatsapp_agents"], 5)
        self.assertEqual(opened["max_whatsapp_numbers_per_warehouse"], 1)
        self.assertTrue(opened["can_create_orders_from_chat"])
        # Abierto o cerrado, define exactamente los mismos campos.
        self.assertEqual(set(opened), set(trial_whatsapp_fields(False)))

    def test_niveles_de_analisis(self):
        self.assertEqual(
            [BY_SLUG[slug]["analytics_level"] for slug in BY_SLUG],
            ["basic", "basic", "intermediate", "advanced"],
        )

    def test_agentes_y_numeros_por_bodega_son_coherentes(self):
        for slug, plan in BY_SLUG.items():
            with self.subTest(plan=slug):
                self.assertLessEqual(plan["max_whatsapp_agents"], plan["max_users"])
                self.assertLessEqual(
                    plan["max_whatsapp_numbers_per_warehouse"],
                    plan["max_whatsapp_numbers"],
                )
                # Los numeros caben en las bodegas del plan.
                self.assertLessEqual(
                    plan["max_whatsapp_numbers"],
                    plan["max_warehouses"]
                    * plan["max_whatsapp_numbers_per_warehouse"],
                )

    def test_multibodega_solo_donde_hay_mas_de_una_bodega(self):
        for slug, plan in BY_SLUG.items():
            with self.subTest(plan=slug):
                self.assertEqual(plan["has_multiwarehouse"], plan["max_warehouses"] > 1)
