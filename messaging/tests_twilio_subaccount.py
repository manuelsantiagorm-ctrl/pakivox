"""
Migracion a Twilio - Paso 3a: subcuenta de Twilio por empresa.

Nunca llama a Twilio: el cliente de Twilio es un doble de prueba.
"""

from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings

from companies.models import Company
from messaging.twilio_models import TwilioSubaccount
from messaging.twilio_provisioning import (
    TwilioProvisioningError,
    ensure_company_subaccount,
    get_master_client,
    subaccount_friendly_name,
    suspend_company_subaccount,
)


def fake_client(*, existing=(), create_sid="ACsub0001", create_error=None):
    client = MagicMock()
    accounts = client.api.v2010.accounts
    accounts.list.return_value = list(existing)

    if create_error:
        accounts.create.side_effect = create_error
    else:
        accounts.create.return_value = SimpleNamespace(
            sid=create_sid, auth_token="token-secreto", status="active"
        )
    return client


class SubcuentaTwilioTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(
            name="Empresa Twilio", slug="empresa-twilio", is_active=True
        )
        cls.other = Company.objects.create(
            name="Otra Empresa", slug="otra-empresa", is_active=True
        )

    def test_nombre_estable_por_id(self):
        self.assertEqual(
            subaccount_friendly_name(self.company), f"PAKIVOX empresa {self.company.pk}"
        )

    def test_crea_subcuenta_y_guarda_token_cifrado(self):
        client = fake_client()
        sub = ensure_company_subaccount(self.company, client=client)

        client.api.v2010.accounts.create.assert_called_once_with(
            friendly_name=f"PAKIVOX empresa {self.company.pk}"
        )
        self.assertTrue(sub.is_ready)
        self.assertEqual(sub.account_sid, "ACsub0001")

        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT auth_token FROM {TwilioSubaccount._meta.db_table} WHERE id = %s",
                [sub.pk],
            )
            raw = cursor.fetchone()[0]
        self.assertNotEqual(raw, "token-secreto")
        self.assertTrue(raw.startswith("fernet$"))
        sub.refresh_from_db()
        self.assertEqual(sub.auth_token, "token-secreto")

    def test_segunda_llamada_no_crea_otra(self):
        client = fake_client()
        first = ensure_company_subaccount(self.company, client=client)
        second = ensure_company_subaccount(self.company, client=client)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(client.api.v2010.accounts.create.call_count, 1)

    def test_reutiliza_la_que_ya_existe_en_twilio(self):
        # Un intento anterior la creo en Twilio pero no se guardo aqui.
        name = subaccount_friendly_name(self.company)
        client = fake_client(
            existing=[
                SimpleNamespace(
                    sid="ACprevia", auth_token="tok", status="active", friendly_name=name
                )
            ]
        )
        sub = ensure_company_subaccount(self.company, client=client)
        client.api.v2010.accounts.create.assert_not_called()
        self.assertEqual(sub.account_sid, "ACprevia")

    def test_dos_en_twilio_con_el_mismo_nombre_no_adivina(self):
        name = subaccount_friendly_name(self.company)
        dup = [
            SimpleNamespace(sid=f"AC{i}", auth_token="t", status="active", friendly_name=name)
            for i in range(2)
        ]
        with self.assertRaises(TwilioProvisioningError):
            ensure_company_subaccount(self.company, client=fake_client(existing=dup))
        self.assertFalse(TwilioSubaccount.objects.filter(status="active").exists())

    def test_ignora_subcuentas_cerradas_o_con_otro_nombre(self):
        name = subaccount_friendly_name(self.company)
        client = fake_client(
            existing=[
                SimpleNamespace(sid="ACcerrada", auth_token="t", status="closed", friendly_name=name),
                SimpleNamespace(sid="ACotra", auth_token="t", status="active", friendly_name=name + "0"),
            ]
        )
        sub = ensure_company_subaccount(self.company, client=client)
        self.assertEqual(sub.account_sid, "ACsub0001")

    def test_si_twilio_falla_queda_marcada_y_se_puede_reintentar(self):
        with patch("messaging.twilio_provisioning.sentry_sdk.capture_exception"):
            with self.assertRaises(TwilioProvisioningError):
                ensure_company_subaccount(
                    self.company, client=fake_client(create_error=RuntimeError("caido"))
                )
        sub = TwilioSubaccount.objects.get(company=self.company)
        self.assertEqual(sub.status, TwilioSubaccount.STATUS_FAILED)
        self.assertIn("caido", sub.last_error)

        retried = ensure_company_subaccount(self.company, client=fake_client())
        self.assertTrue(retried.is_ready)
        self.assertEqual(retried.last_error, "")

    def test_cada_empresa_tiene_la_suya(self):
        a = ensure_company_subaccount(self.company, client=fake_client(create_sid="ACa"))
        b = ensure_company_subaccount(self.other, client=fake_client(create_sid="ACb"))
        self.assertNotEqual(a.account_sid, b.account_sid)
        self.assertEqual(a.company_id, self.company.pk)
        self.assertEqual(b.company_id, self.other.pk)

    def test_suspender(self):
        ensure_company_subaccount(self.company, client=fake_client())
        client = fake_client()
        sub = suspend_company_subaccount(self.company, client=client)
        client.api.v2010.accounts.assert_called_with("ACsub0001")
        client.api.v2010.accounts.return_value.update.assert_called_once_with(
            status="suspended"
        )
        self.assertEqual(sub.status, TwilioSubaccount.STATUS_SUSPENDED)

    def test_suspendida_no_se_reactiva_sola(self):
        ensure_company_subaccount(self.company, client=fake_client())
        suspend_company_subaccount(self.company, client=fake_client())
        with self.assertRaises(TwilioProvisioningError):
            ensure_company_subaccount(self.company, client=fake_client())

    @override_settings(TWILIO_MASTER_ACCOUNT_SID="", TWILIO_MASTER_AUTH_TOKEN="")
    def test_sin_cuenta_principal_falla_claro(self):
        with self.assertRaises(TwilioProvisioningError):
            get_master_client()


class ComandoSubcuentaTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(
            name="Empresa Cmd", slug="empresa-cmd", is_active=True
        )

    def run_cmd(self, *args):
        out = StringIO()
        call_command("twilio_subaccount", *args, stdout=out)
        return out.getvalue()

    def test_dry_run_no_toca_nada(self):
        with patch("messaging.twilio_provisioning.get_master_client") as master:
            out = self.run_cmd("--company", str(self.company.pk), "--dry-run")
        master.assert_not_called()
        self.assertIn("se CREARIA", out)
        self.assertFalse(TwilioSubaccount.objects.exists())

    def test_crea_y_no_imprime_el_token(self):
        with patch(
            "messaging.twilio_provisioning.get_master_client", return_value=fake_client()
        ):
            out = self.run_cmd("--company", str(self.company.pk))
        self.assertIn("ACsub0001", out)
        self.assertNotIn("token-secreto", out)

    def test_empresa_inexistente(self):
        with self.assertRaises(CommandError):
            self.run_cmd("--company", "999999")
