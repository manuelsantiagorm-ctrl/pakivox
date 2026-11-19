from pathlib import Path

from django.test import SimpleTestCase


class ProviderEventPostgresqlLockTests(SimpleTestCase):
    def _source(self):
        root = Path(__file__).resolve().parents[1]
        return (
            root / "services" / "provider_event_engine.py"
        ).read_text(encoding="utf-8-sig")

    def test_claim_event_locks_base_row_before_relations(self):
        source = self._source()
        self.assertIn(
            "ShippingProviderEvent.objects.select_for_update()",
            source,
        )
        self.assertIn(
            "ShippingProviderEvent.objects.select_related(",
            source,
        )
        self.assertIn(
            "El bloqueo sobre ShippingProviderEvent permanece",
            source,
        )

    def test_no_for_update_nullable_outer_join_chain(self):
        source = self._source()
        vulnerable = (
            "ShippingProviderEvent.objects.select_for_update()\n"
            "            .select_related("
        )
        self.assertNotIn(vulnerable, source)

    def test_claim_event_keeps_atomic_and_lease(self):
        source = self._source()
        self.assertIn("def claim_event(event_id: int):", source)
        self.assertIn("with transaction.atomic():", source)
        self.assertIn("event.lease_expires_at = now + timedelta(", source)
        self.assertIn("transition_provider_event(", source)
