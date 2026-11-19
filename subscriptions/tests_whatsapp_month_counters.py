"""
Migracion a Twilio - Contadores mensuales por linea (enviados y recibidos).

Pruebas con base de datos. Las lineas se crean con bulk_create para no
pasar por WhatsAppNumber.save(), que usa un candado exclusivo de
PostgreSQL; aqui solo se prueban los UPDATE atomicos de los contadores.
"""

from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings

from companies.models import Company
from messaging.models import WhatsAppNumber
from subscriptions import whatsapp_quota

THIS_MONTH = date(2026, 10, 1)
LAST_MONTH = date(2026, 9, 1)


class ContadoresMensualesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(
            name="Empresa Contadores", slug="empresa-contadores", is_active=True
        )
        cls.other_company = Company.objects.create(
            name="Empresa Ajena", slug="empresa-ajena", is_active=True
        )

    def make_number(self, *, company=None, provider="twilio", **fields):
        number = WhatsAppNumber(
            company=company or self.company,
            provider=provider,
            **fields,
        )
        WhatsAppNumber.objects.bulk_create([number])
        return WhatsAppNumber.objects.filter(company=company or self.company).order_by("-id").first()

    def bump(self, number, field):
        return whatsapp_quota._bump_number_month_counter(
            whatsapp_number_id=number.pk,
            this_month=THIS_MONTH,
            field=field,
        )

    def test_recibido_en_linea_nueva_inicia_el_mes(self):
        number = self.make_number()
        self.assertTrue(self.bump(number, whatsapp_quota._RECEIVED_FIELD))
        number.refresh_from_db()
        self.assertEqual(number.whatsapp_month_start, THIS_MONTH)
        self.assertEqual(number.whatsapp_month_received, 1)
        self.assertEqual(number.whatsapp_month_sent, 0)

    def test_mismo_mes_solo_suma_su_contador(self):
        number = self.make_number(
            whatsapp_month_start=THIS_MONTH,
            whatsapp_month_sent=7,
            whatsapp_month_received=3,
        )
        self.bump(number, whatsapp_quota._RECEIVED_FIELD)
        self.bump(number, whatsapp_quota._SENT_FIELD)
        number.refresh_from_db()
        self.assertEqual(number.whatsapp_month_sent, 8)
        self.assertEqual(number.whatsapp_month_received, 4)

    def test_mes_nuevo_reinicia_ambos_contadores(self):
        number = self.make_number(
            whatsapp_month_start=LAST_MONTH,
            whatsapp_month_sent=900,
            whatsapp_month_received=400,
        )
        self.bump(number, whatsapp_quota._SENT_FIELD)
        number.refresh_from_db()
        self.assertEqual(number.whatsapp_month_start, THIS_MONTH)
        self.assertEqual(number.whatsapp_month_sent, 1)
        self.assertEqual(number.whatsapp_month_received, 0)

    def test_un_reinicio_no_pisa_lo_que_ya_se_conto_en_el_mes_nuevo(self):
        # Un recibido ya abrio el mes nuevo; el envio que llega despues
        # debe sumarse, no reiniciar.
        number = self.make_number(whatsapp_month_start=LAST_MONTH, whatsapp_month_sent=50)
        self.bump(number, whatsapp_quota._RECEIVED_FIELD)
        self.bump(number, whatsapp_quota._SENT_FIELD)
        number.refresh_from_db()
        self.assertEqual(number.whatsapp_month_received, 1)
        self.assertEqual(number.whatsapp_month_sent, 1)

    def test_linea_inexistente_no_cuenta(self):
        self.assertFalse(
            whatsapp_quota._bump_number_month_counter(
                whatsapp_number_id=999999,
                this_month=THIS_MONTH,
                field=whatsapp_quota._RECEIVED_FIELD,
            )
        )

    @override_settings(WHATSAPP_TWILIO_FEE_USD="0.005", WHATSAPP_USD_MXN="17.24")
    def test_el_gasto_suma_recibidos_y_solo_de_la_propia_empresa(self):
        self.make_number(
            whatsapp_month_start=THIS_MONTH,
            whatsapp_month_sent=10,
            whatsapp_month_received=20,
        )
        self.make_number(
            company=self.other_company,
            whatsapp_month_start=THIS_MONTH,
            whatsapp_month_sent=5000,
            whatsapp_month_received=5000,
        )
        usage, _ = whatsapp_quota._company_usage_by_number(
            company=self.company, this_month=THIS_MONTH
        )
        self.assertEqual(len(usage), 1)
        (only,) = usage.values()
        self.assertEqual((only.sent, only.received), (10, 20))
        self.assertEqual(only.provider_fee_mxn, Decimal("0.0862"))

    def test_count_inbound_usa_el_mes_actual(self):
        number = self.make_number()
        with patch.object(whatsapp_quota.timezone, "localdate", return_value=date(2026, 10, 17)):
            whatsapp_quota.count_whatsapp_inbound_message(whatsapp_number_id=number.pk)
        number.refresh_from_db()
        self.assertEqual(number.whatsapp_month_start, THIS_MONTH)
        self.assertEqual(number.whatsapp_month_received, 1)
