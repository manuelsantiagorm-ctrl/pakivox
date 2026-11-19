from unittest.mock import patch

from django.test import SimpleTestCase

from shipping_hub.management.commands.audit_postgres_migration import (
    normalized_pk,
    pk_digest,
)


class PostgresMigrationAuditPrimitiveTests(SimpleTestCase):
    def test_normalized_pk_supports_integer(self):
        self.assertEqual(normalized_pk(15), "15")

    def test_normalized_pk_supports_uuid_like_value(self):
        self.assertEqual(
            normalized_pk("abc-123"),
            "abc-123",
        )

    def test_pk_digest_is_deterministic(self):
        self.assertEqual(
            pk_digest([1, 2, 3]),
            pk_digest([1, 2, 3]),
        )

    def test_pk_digest_changes_with_values(self):
        self.assertNotEqual(
            pk_digest([1, 2, 3]),
            pk_digest([1, 2, 4]),
        )

    def test_pk_digest_changes_with_order(self):
        self.assertNotEqual(
            pk_digest([1, 2, 3]),
            pk_digest([3, 2, 1]),
        )
