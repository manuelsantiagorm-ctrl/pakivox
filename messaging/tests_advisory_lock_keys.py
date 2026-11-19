"""
Candados de PostgreSQL de los limites de WhatsApp.

La llave de pg_advisory_xact_lock tiene que ser la MISMA en todos los
procesos de gunicorn; si no, dos altas simultaneas en workers distintos no
se esperan entre si y se puede rebasar el limite de numeros por bodega o de
agentes por canal. Antes del 26-sep-2026 la llave de WhatsAppNumber se
calculaba con hash() de un texto, que Python cambia en cada proceso.
"""

import subprocess
import sys
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from messaging import models as messaging_models


class AdvisoryLockKeysTests(SimpleTestCase):
    def test_llaves_son_enteros_fijos_y_distintos(self):
        number_key = messaging_models.WHATSAPP_NUMBER_LIMIT_LOCK_KEY
        agent_key = messaging_models.CHANNEL_AGENT_LIMIT_LOCK_KEY
        self.assertIsInstance(number_key, int)
        self.assertIsInstance(agent_key, int)
        self.assertNotEqual(number_key, agent_key)
        # Caben en el int4 que recibe pg_advisory_xact_lock(int, int).
        for key in (number_key, agent_key):
            self.assertTrue(0 < key < 2**31)

    def test_la_llave_no_cambia_entre_procesos(self):
        # Dos procesos con semillas de hash distintas deben calcular la
        # misma llave (con hash() de un texto esto fallaba).
        code = (
            "import django, os;"
            "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings');"
            "django.setup();"
            "from messaging import models as m;"
            "print(m.WHATSAPP_NUMBER_LIMIT_LOCK_KEY, m.CHANNEL_AGENT_LIMIT_LOCK_KEY)"
        )
        outputs = set()
        for seed in ("1", "2"):
            env = dict(__import__("os").environ, PYTHONHASHSEED=seed)
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                env=env,
                check=True,
            )
            outputs.add(result.stdout.strip().splitlines()[-1])
        self.assertEqual(len(outputs), 1)

    def test_whatsappnumber_save_usa_la_llave_fija(self):
        number = messaging_models.WhatsAppNumber(company_id=7)
        cursor = MagicMock()
        connection = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor

        with patch("django.db.connection", connection), patch.object(
            messaging_models.transaction, "atomic"
        ), patch.object(
            messaging_models.WhatsAppNumber, "full_clean", side_effect=RuntimeError("alto")
        ):
            with self.assertRaises(RuntimeError):
                number.save()

        cursor.execute.assert_called_once_with(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            [messaging_models.WHATSAPP_NUMBER_LIMIT_LOCK_KEY, 7],
        )
