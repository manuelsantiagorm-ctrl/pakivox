from django.test import SimpleTestCase

from shipping_hub.management.commands.diagnose_postgres_migration_differences import (
    normalize,
)


class PostgresDifferenceDiagnosticTests(SimpleTestCase):
    def test_normalize_none(self):
        self.assertIsNone(normalize(None))

    def test_normalize_integer(self):
        self.assertEqual(normalize(10), "10")

    def test_normalize_string(self):
        self.assertEqual(normalize("abc"), "abc")
