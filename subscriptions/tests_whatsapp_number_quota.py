"""
PLANES Fase 3 - Reglas del cupo de WhatsApp por numero.

Todas son SimpleTestCase: la logica de decision vive en
whatsapp_quota_rules.py, que no toca la base de datos.
"""

from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from subscriptions import whatsapp_quota
from subscriptions.models import Subscription
from subscriptions.whatsapp_quota_rules import (
    BLOCK_BUDGET,
    BLOCK_HARD_CAP,
    FREE_MESSAGES_PER_NUMBER,
    billable_messages,
    budget_in_messages,
    evaluate_reservation,
    message_cost_mxn,
)

COST = message_cost_mxn("0.0085", "17.24")


def simulate_state(*, numbers, budget_mxn, order, authorized=False, hard_cap=None):
    """
    Manda mensajes uno por uno segun `order` (lista de ids de linea) hasta
    el primer bloqueo. Devuelve los mensajes enviados por cada linea.
    """
    sent = {number_id: 0 for number_id in numbers}
    budget_messages = budget_in_messages(budget_mxn, COST)
    passed = 0

    for number_id in order:
        decision = evaluate_reservation(
            sent_by_number=sent,
            number_id=number_id,
            free_per_number=FREE_MESSAGES_PER_NUMBER,
            hard_cap=hard_cap,
            used=passed,
            budget_messages=budget_messages,
            overage_authorized=authorized,
        )

        if not decision.allowed:
            break

        sent[number_id] += 1
        passed += 1

    return sent


def simulate(**kwargs):
    """Cuantos mensajes alcanzaron a salir antes del primer bloqueo."""
    return sum(simulate_state(**kwargs).values())


def round_robin(numbers, total):
    return [numbers[i % len(numbers)] for i in range(total)]


class MessageCostAndBudgetTests(SimpleTestCase):
    def test_costo_por_mensaje_es_tarifa_por_tipo_de_cambio(self):
        self.assertEqual(COST, Decimal("0.146540"))

    def test_colchon_en_pesos_se_traduce_a_mensajes_hacia_abajo(self):
        # Cifras del documento maestro: $350 -> 2,388 y $500 -> 3,412.
        self.assertEqual(budget_in_messages(Decimal("350"), COST), 2388)
        self.assertEqual(budget_in_messages(Decimal("500"), COST), 3412)

    def test_sin_colchon_es_cero_y_sin_definir_es_none(self):
        self.assertEqual(budget_in_messages(Decimal("0"), COST), 0)
        self.assertIsNone(budget_in_messages(None, COST))

    def test_costo_no_positivo_es_error(self):
        with self.assertRaises(ValueError):
            budget_in_messages(Decimal("350"), Decimal("0"))

    def test_mensajes_cobrables_suman_solo_lo_que_pasa_del_cupo_gratis(self):
        sent = {1: 1500, 2: 400, 3: 1000}
        self.assertEqual(billable_messages(sent), 500)


class CompuertaPorEscaleraTests(SimpleTestCase):
    """Las escaleras de mensajes que se le prometen al cliente."""

    def test_esencial_un_numero_sin_colchon_llega_a_1000(self):
        self.assertEqual(
            simulate(numbers=[1], budget_mxn=Decimal("0"), order=[1] * 5000),
            1000,
        )

    def test_profesional_tres_numeros_repartido_llega_a_5388(self):
        self.assertEqual(
            simulate(
                numbers=[1, 2, 3],
                budget_mxn=Decimal("350"),
                order=round_robin([1, 2, 3], 9000),
            ),
            5388,
        )

    def test_profesional_concentrado_en_un_numero_llega_a_3388(self):
        self.assertEqual(
            simulate(
                numbers=[1, 2, 3], budget_mxn=Decimal("350"), order=[1] * 9000
            ),
            3388,
        )

    def test_maximo_seis_numeros_repartido_llega_a_9412(self):
        numbers = [1, 2, 3, 4, 5, 6]
        self.assertEqual(
            simulate(
                numbers=numbers,
                budget_mxn=Decimal("500"),
                order=round_robin(numbers, 12000),
            ),
            9412,
        )

    def test_maximo_con_un_solo_numero_llega_a_4412(self):
        self.assertEqual(
            simulate(numbers=[1], budget_mxn=Decimal("500"), order=[1] * 9000),
            4412,
        )

    def test_el_costo_al_toparse_es_el_mismo_repartido_o_concentrado(self):
        # El colchon esta medido en pesos: al toparse, los mensajes
        # cobrables son los mismos sin importar como se repartio el trafico.
        budget_messages = budget_in_messages(Decimal("350"), COST)

        concentrated = simulate_state(
            numbers=[1, 2], budget_mxn=Decimal("350"), order=[1] * 9000
        )
        spread = simulate_state(
            numbers=[1, 2],
            budget_mxn=Decimal("350"),
            order=round_robin([1, 2], 9000),
        )

        self.assertEqual(billable_messages(concentrated), budget_messages)
        self.assertEqual(billable_messages(spread), budget_messages)


class SoloSeBloqueaLoQueCuestaTests(SimpleTestCase):
    def evaluate(self, sent, number_id, **overrides):
        params = dict(
            sent_by_number=sent,
            number_id=number_id,
            free_per_number=1000,
            hard_cap=None,
            used=sum(sent.values()),
            budget_messages=100,
            overage_authorized=False,
        )
        params.update(overrides)
        return evaluate_reservation(**params)

    def test_linea_topada_se_bloquea_pero_otra_con_cupo_gratis_pasa(self):
        # Linea 1 ya gasto todo el colchon (100 cobrables); linea 2 va en 10.
        sent = {1: 1100, 2: 10}

        blocked = self.evaluate(sent, 1)
        free = self.evaluate(sent, 2)

        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.reason, BLOCK_BUDGET)
        self.assertTrue(free.allowed)
        self.assertFalse(free.billable)

    def test_linea_pasada_del_cupo_gratis_pasa_mientras_quede_colchon(self):
        decision = self.evaluate({1: 1050, 2: 0}, 1)
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.billable)

    def test_con_autorizacion_del_dueno_todo_pasa(self):
        decision = self.evaluate({1: 5000}, 1, overage_authorized=True)
        self.assertTrue(decision.allowed)

    def test_sin_limites_solo_cuenta_y_deja_pasar(self):
        decision = self.evaluate(
            {1: 50000},
            1,
            hard_cap=None,
            budget_messages=None,
        )
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.billable)

    def test_tope_duro_del_trial_bloquea_incluso_bajo_el_cupo_gratis(self):
        blocked = self.evaluate({1: 50}, 1, hard_cap=50, used=50)
        allowed = self.evaluate({1: 49}, 1, hard_cap=50, used=49)

        self.assertFalse(blocked.allowed)
        self.assertEqual(blocked.reason, BLOCK_HARD_CAP)
        self.assertTrue(allowed.allowed)


class PeriodoDeWhatsAppTests(SimpleTestCase):
    def test_plan_de_pago_usa_mes_calendario(self):
        subscription = SimpleNamespace(
            status="active",
            start_date=date(2026, 8, 20),
            trial_ends_at=None,
            end_date=date(2026, 9, 19),
        )

        with patch.object(
            whatsapp_quota.timezone, "localdate", return_value=date(2026, 9, 18)
        ):
            start, end = whatsapp_quota.get_whatsapp_period_dates(subscription)

        self.assertEqual(start, date(2026, 9, 1))
        self.assertEqual(end, date(2026, 9, 30))

    def test_febrero_bisiesto_termina_el_29(self):
        subscription = SimpleNamespace(
            status="active",
            start_date=date(2028, 1, 5),
            trial_ends_at=None,
            end_date=None,
        )

        with patch.object(
            whatsapp_quota.timezone, "localdate", return_value=date(2028, 2, 10)
        ):
            start, end = whatsapp_quota.get_whatsapp_period_dates(subscription)

        self.assertEqual((start, end), (date(2028, 2, 1), date(2028, 2, 29)))

    def test_trial_conserva_su_propio_periodo(self):
        subscription = SimpleNamespace(
            status=Subscription.STATUS_TRIAL,
            start_date=date(2026, 9, 10),
            trial_ends_at=date(2026, 9, 24),
            end_date=None,
        )

        start, end = whatsapp_quota.get_whatsapp_period_dates(subscription)

        self.assertEqual((start, end), (date(2026, 9, 10), date(2026, 9, 24)))

    def test_as_date_acepta_fecha_y_fecha_hora(self):
        self.assertEqual(whatsapp_quota._as_date(date(2026, 9, 1)), date(2026, 9, 1))
        self.assertIsNone(whatsapp_quota._as_date(None))

        aware = datetime(2026, 10, 1, 3, 0, tzinfo=dt_timezone.utc)
        # 03:00 UTC del 1 de octubre sigue siendo 30 de septiembre en Mexico.
        with self.settings(TIME_ZONE="America/Mexico_City", USE_TZ=True):
            self.assertEqual(whatsapp_quota._as_date(aware), date(2026, 9, 30))
