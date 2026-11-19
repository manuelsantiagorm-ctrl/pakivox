"""
Migracion a Twilio - Tope de GASTO en pesos del cupo de WhatsApp.

Ver claude/twilio-migracion-checklist.md, secciones 1 y 4.

Con Twilio todo mensaje cuesta (comision del proveedor), y encima Meta
cobra lo que cada numero envia despues de su cupo gratis. El colchon del
plan es el tope de gasto total de la empresa en el mes, sin importar
cuantos numeros tenga ni como reparta el trafico.

Todas son SimpleTestCase: las reglas viven en whatsapp_quota_rules.py y los
ajustes de tarifa en whatsapp_quota.py se prueban sin base de datos.
"""

from decimal import Decimal

from django.test import SimpleTestCase, override_settings

from subscriptions import whatsapp_quota
from subscriptions.whatsapp_quota_rules import (
    BLOCK_BUDGET,
    BLOCK_HARD_CAP,
    FREE_MESSAGES_PER_NUMBER,
    NumberUsage,
    budget_in_messages,
    evaluate_reservation,
    evaluate_spend_reservation,
    message_cost_mxn,
    next_message_cost_mxn,
    spent_mxn,
)

META = message_cost_mxn("0.0085", "17.24")
TWILIO = message_cost_mxn("0.005", "17.24")
FREE = FREE_MESSAGES_PER_NUMBER


def simulate_spend(*, fees, budget_mxn, order, authorized=False, hard_cap=None):
    """
    Manda mensajes uno por uno segun `order` hasta el primer bloqueo.
    fees = {id_de_linea: comision del proveedor en pesos}.
    Devuelve (mensajes por linea, gasto final).
    """
    sent = {number_id: 0 for number_id in fees}
    passed = 0

    def usage():
        return {
            number_id: NumberUsage(sent=sent[number_id], provider_fee_mxn=fee)
            for number_id, fee in fees.items()
        }

    for number_id in order:
        decision = evaluate_spend_reservation(
            usage_by_number=usage(),
            number_id=number_id,
            free_per_number=FREE,
            meta_fee_mxn=META,
            hard_cap=hard_cap,
            used=passed,
            budget_mxn=budget_mxn,
            overage_authorized=authorized,
        )

        if not decision.allowed:
            break

        sent[number_id] += 1
        passed += 1

    return sent, spent_mxn(usage(), free_per_number=FREE, meta_fee_mxn=META)


def round_robin(numbers, total):
    return [numbers[i % len(numbers)] for i in range(total)]


class CostoPorMensajeTests(SimpleTestCase):
    def test_twilio_cobra_aunque_meta_regale(self):
        usage = NumberUsage(sent=0, provider_fee_mxn=TWILIO)
        self.assertEqual(
            next_message_cost_mxn(usage, free_per_number=FREE, meta_fee_mxn=META),
            TWILIO,
        )

    def test_twilio_mas_meta_despues_del_cupo_gratis(self):
        usage = NumberUsage(sent=FREE, provider_fee_mxn=TWILIO)
        self.assertEqual(
            next_message_cost_mxn(usage, free_per_number=FREE, meta_fee_mxn=META),
            TWILIO + META,
        )

    def test_meta_directo_bajo_cupo_gratis_no_cuesta(self):
        usage = NumberUsage(sent=FREE - 1)
        self.assertEqual(
            next_message_cost_mxn(usage, free_per_number=FREE, meta_fee_mxn=META),
            Decimal("0"),
        )

    def test_gasto_acumulado_suma_comision_y_excedente_de_meta(self):
        usage = {
            1: NumberUsage(sent=1200, provider_fee_mxn=TWILIO),
            2: NumberUsage(sent=300, provider_fee_mxn=TWILIO),
            3: NumberUsage(sent=1100),  # sigue en Meta directo
        }
        expected = TWILIO * 1500 + META * 200 + META * 100
        self.assertEqual(
            spent_mxn(usage, free_per_number=FREE, meta_fee_mxn=META),
            expected,
        )


class EscaleraConTwilioTests(SimpleTestCase):
    """Los topes decididos el 25-sep-2026: $86 / $286 / $586."""

    def test_esencial_un_numero_llega_a_casi_1000(self):
        sent, spent = simulate_spend(
            fees={1: TWILIO}, budget_mxn=Decimal("86"), order=[1] * 5000
        )
        self.assertEqual(sum(sent.values()), 997)
        self.assertLessEqual(spent, Decimal("86"))

    def test_profesional_tres_numeros_repartido_llega_a_3117(self):
        sent, spent = simulate_spend(
            fees={1: TWILIO, 2: TWILIO, 3: TWILIO},
            budget_mxn=Decimal("286"),
            order=round_robin([1, 2, 3], 9000),
        )
        self.assertEqual(sum(sent.values()), 3117)
        self.assertLessEqual(spent, Decimal("286"))

    def test_maximo_seis_numeros_repartido_llega_a_6295(self):
        sent, spent = simulate_spend(
            fees={n: TWILIO for n in range(1, 7)},
            budget_mxn=Decimal("586"),
            order=round_robin(list(range(1, 7)), 12000),
        )
        self.assertEqual(sum(sent.values()), 6295)
        self.assertLessEqual(spent, Decimal("586"))

    def test_el_tope_es_por_empresa_no_por_numero(self):
        # Concentrar el trafico en un numero rinde MENOS mensajes (pasa
        # antes al cobro de Meta), pero el gasto nunca rebasa el tope.
        one, spent_one = simulate_spend(
            fees={n: TWILIO for n in range(1, 7)},
            budget_mxn=Decimal("586"),
            order=[1] * 12000,
        )
        self.assertLess(sum(one.values()), 6295)
        self.assertLessEqual(spent_one, Decimal("586"))

    def test_nunca_se_rebasa_el_tope_ni_por_un_centavo(self):
        for budget in ("86", "286", "586", "0.10", "1"):
            with self.subTest(budget=budget):
                _, spent = simulate_spend(
                    fees={1: TWILIO, 2: TWILIO},
                    budget_mxn=Decimal(budget),
                    order=round_robin([1, 2], 12000),
                )
                self.assertLessEqual(spent, Decimal(budget))

    def test_sin_tope_de_gasto_con_twilio_bloquea_todo(self):
        # Un plan con colchon 0 ya no deja salir nada por Twilio: por eso
        # Esencial paso de $0 a $86.
        sent, _ = simulate_spend(
            fees={1: TWILIO}, budget_mxn=Decimal("0"), order=[1] * 10
        )
        self.assertEqual(sum(sent.values()), 0)


class CompatibilidadConMetaDirectoTests(SimpleTestCase):
    """
    Mientras un numero siga en Meta directo (Jazmin, hasta su migracion),
    la regla nueva debe decidir EXACTAMENTE igual que la anterior.
    """

    def test_misma_decision_que_la_regla_anterior(self):
        for budget in ("0", "86", "350", "500"):
            budget_messages = budget_in_messages(Decimal(budget), META)
            for sent_1, sent_2 in ((0, 0), (999, 0), (1000, 0), (1500, 1400),
                                   (1000 + budget_messages, 0),
                                   (1000 + budget_messages - 1, 1000)):
                with self.subTest(budget=budget, sent=(sent_1, sent_2)):
                    old = evaluate_reservation(
                        sent_by_number={1: sent_1, 2: sent_2},
                        number_id=1,
                        free_per_number=FREE,
                        hard_cap=None,
                        used=sent_1 + sent_2,
                        budget_messages=budget_messages,
                        overage_authorized=False,
                    )
                    new = evaluate_spend_reservation(
                        usage_by_number={
                            1: NumberUsage(sent=sent_1),
                            2: NumberUsage(sent=sent_2),
                        },
                        number_id=1,
                        free_per_number=FREE,
                        meta_fee_mxn=META,
                        hard_cap=None,
                        used=sent_1 + sent_2,
                        budget_mxn=Decimal(budget),
                        overage_authorized=False,
                    )
                    self.assertEqual(old.allowed, new.allowed)

    def test_meta_bajo_cupo_gratis_pasa_aunque_el_gasto_este_topado(self):
        usage = {
            1: NumberUsage(sent=500),  # Meta directo, gratis
            2: NumberUsage(sent=5000, provider_fee_mxn=TWILIO),  # ya topo
        }
        decision = evaluate_spend_reservation(
            usage_by_number=usage,
            number_id=1,
            free_per_number=FREE,
            meta_fee_mxn=META,
            hard_cap=None,
            used=5500,
            budget_mxn=Decimal("86"),
            overage_authorized=False,
        )
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.billable)


class OtrasReglasTests(SimpleTestCase):
    def evaluate(self, **overrides):
        params = dict(
            usage_by_number={1: NumberUsage(sent=5000, provider_fee_mxn=TWILIO)},
            number_id=1,
            free_per_number=FREE,
            meta_fee_mxn=META,
            hard_cap=None,
            used=5000,
            budget_mxn=Decimal("86"),
            overage_authorized=False,
        )
        params.update(overrides)
        return evaluate_spend_reservation(**params)

    def test_topado_se_bloquea_por_presupuesto(self):
        decision = self.evaluate()
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, BLOCK_BUDGET)

    def test_con_autorizacion_del_dueno_todo_pasa(self):
        self.assertTrue(self.evaluate(overage_authorized=True).allowed)

    def test_sin_tope_definido_solo_cuenta(self):
        self.assertTrue(self.evaluate(budget_mxn=None).allowed)

    def test_tope_duro_del_trial(self):
        decision = self.evaluate(
            usage_by_number={1: NumberUsage(sent=50, provider_fee_mxn=TWILIO)},
            hard_cap=50,
            used=50,
            budget_mxn=Decimal("1000"),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, BLOCK_HARD_CAP)


class ComisionPorProveedorTests(SimpleTestCase):
    @override_settings(WHATSAPP_TWILIO_FEE_USD="0.005", WHATSAPP_USD_MXN="17.24")
    def test_twilio_en_pesos(self):
        self.assertEqual(
            whatsapp_quota.get_whatsapp_provider_fee_mxn("twilio"),
            Decimal("0.0862"),
        )

    def test_meta_directo_no_cobra_comision(self):
        self.assertEqual(
            whatsapp_quota.get_whatsapp_provider_fee_mxn("meta"), Decimal("0")
        )

    @override_settings(WHATSAPP_TWILIO_FEE_USD="0.005", WHATSAPP_USD_MXN="17.24")
    def test_proveedor_desconocido_se_cobra_como_twilio(self):
        # Ante la duda se sobrestima el gasto: la compuerta cierra antes.
        for provider in ("", None, "Otro", "TWILIO"):
            with self.subTest(provider=provider):
                self.assertEqual(
                    whatsapp_quota.get_whatsapp_provider_fee_mxn(provider),
                    Decimal("0.0862"),
                )

    @override_settings(WHATSAPP_TWILIO_FEE_USD="basura", WHATSAPP_USD_MXN="17.24")
    def test_ajuste_invalido_usa_el_default(self):
        self.assertEqual(
            whatsapp_quota.get_whatsapp_provider_fee_mxn("twilio"),
            Decimal("0.0862"),
        )

    def test_sin_empresa_no_suma_lineas(self):
        # Fail closed: nunca consulta lineas sin filtrar por empresa.
        self.assertEqual(
            whatsapp_quota._company_usage_by_number(company=None, this_month=None),
            ({}, {}),
        )


class RecibidosCuentanContraElTopeTests(SimpleTestCase):
    """Decidido por Manuel el 26-sep-2026: Twilio cobra tambien los recibidos."""

    def test_recibidos_cuestan_la_comision_del_proveedor(self):
        usage = {1: NumberUsage(sent=0, received=100, provider_fee_mxn=TWILIO)}
        self.assertEqual(
            spent_mxn(usage, free_per_number=FREE, meta_fee_mxn=META),
            TWILIO * 100,
        )

    def test_recibidos_no_pagan_tarifa_de_meta_ni_gastan_su_cupo_gratis(self):
        usage = {1: NumberUsage(sent=900, received=5000, provider_fee_mxn=TWILIO)}
        self.assertEqual(
            spent_mxn(usage, free_per_number=FREE, meta_fee_mxn=META),
            TWILIO * 5900,
        )
        self.assertEqual(
            next_message_cost_mxn(usage[1], free_per_number=FREE, meta_fee_mxn=META),
            TWILIO,
        )

    def test_recibidos_en_meta_directo_no_cuestan(self):
        usage = {1: NumberUsage(sent=0, received=5000)}
        self.assertEqual(
            spent_mxn(usage, free_per_number=FREE, meta_fee_mxn=META),
            Decimal("0"),
        )

    def test_si_los_recibidos_agotan_el_tope_los_envios_se_detienen(self):
        usage = {1: NumberUsage(sent=0, received=998, provider_fee_mxn=TWILIO)}
        decision = evaluate_spend_reservation(
            usage_by_number=usage,
            number_id=1,
            free_per_number=FREE,
            meta_fee_mxn=META,
            hard_cap=None,
            used=0,
            budget_mxn=Decimal("86"),
            overage_authorized=False,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, BLOCK_BUDGET)

    def test_un_envio_nunca_rebasa_el_tope_aunque_haya_recibidos(self):
        # Por cada mensaje enviado llega uno de respuesta: con $86 salen la
        # mitad de mensajes que si nadie contestara. Ningun ENVIO rebasa el
        # tope; los recibidos que lleguen despues del tope no se pueden
        # bloquear, asi que si pueden rebasarlo (se cuentan igual).
        sent = 0
        received = 0
        while True:
            usage = {
                1: NumberUsage(sent=sent, received=received, provider_fee_mxn=TWILIO)
            }
            decision = evaluate_spend_reservation(
                usage_by_number=usage,
                number_id=1,
                free_per_number=FREE,
                meta_fee_mxn=META,
                hard_cap=None,
                used=sent,
                budget_mxn=Decimal("86"),
                overage_authorized=False,
            )
            if not decision.allowed:
                break
            sent += 1
            after_send = {
                1: NumberUsage(sent=sent, received=received, provider_fee_mxn=TWILIO)
            }
            self.assertLessEqual(
                spent_mxn(after_send, free_per_number=FREE, meta_fee_mxn=META),
                Decimal("86"),
            )
            received += 1

        self.assertEqual(sent, 499)


class PorcentajeDelCupoTests(SimpleTestCase):
    """El cliente ve porcentaje, nunca pesos (decidido el 26-sep-2026)."""

    def test_porcentaje_redondeado_hacia_abajo(self):
        self.assertEqual(
            whatsapp_quota._budget_used_percent(Decimal("43.00"), Decimal("86")), 50
        )
        self.assertEqual(
            whatsapp_quota._budget_used_percent(Decimal("85.99"), Decimal("86")), 99
        )

    def test_nunca_pasa_de_100_ni_baja_de_0(self):
        self.assertEqual(
            whatsapp_quota._budget_used_percent(Decimal("200"), Decimal("86")), 100
        )
        self.assertEqual(
            whatsapp_quota._budget_used_percent(Decimal("0"), Decimal("86")), 0
        )

    def test_sin_tope_es_none_y_tope_cero_es_lleno(self):
        self.assertIsNone(whatsapp_quota._budget_used_percent(Decimal("5"), None))
        self.assertEqual(
            whatsapp_quota._budget_used_percent(Decimal("0"), Decimal("0")), 100
        )
