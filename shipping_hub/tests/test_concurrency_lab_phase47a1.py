from unittest.mock import patch

from django.test import SimpleTestCase

from shipping_hub.models import (
    ShippingProviderEvent,
    ShippingPurchase,
)
from shipping_hub.services import (
    provider_event_engine,
    purchase_engine,
    recovery_engine,
)
from shipping_hub.services.concurrency_lab import (
    ConcurrencyCheck,
    ConcurrencyReport,
    _source_contains,
)


class ConcurrencyArchitectureTests(SimpleTestCase):
    def test_purchase_worker_uses_skip_locked(self):
        self.assertTrue(
            _source_contains(
                purchase_engine.process_shipping_purchase,
                "select_for_update(skip_locked=True)",
            )
        )

    def test_purchase_request_locks_shipment(self):
        self.assertTrue(
            _source_contains(
                purchase_engine.request_shipping_purchase,
                "select_for_update()",
                "open_purchase",
            )
        )

    def test_open_purchase_constraint_exists(self):
        names = {
            item.name
            for item in ShippingPurchase._meta.constraints
        }
        self.assertIn("uniq_open_ship_purchase", names)

    def test_purchase_generation_constraint_exists(self):
        names = {
            item.name
            for item in ShippingPurchase._meta.constraints
        }
        self.assertIn("uniq_ship_purchase_generation", names)

    def test_event_claim_uses_lock_and_lease(self):
        self.assertTrue(
            _source_contains(
                provider_event_engine.claim_event,
                "select_for_update()",
                "lease_expires_at",
            )
        )

    def test_event_deduplication_constraints_exist(self):
        names = {
            item.name
            for item in ShippingProviderEvent._meta.constraints
        }
        self.assertIn("uniq_ship_provider_event", names)
        self.assertIn("uniq_ship_provider_dedupe", names)

    def test_purchase_lease_expiry_uses_reconciliation(self):
        self.assertTrue(
            _source_contains(
                purchase_engine.mark_expired_purchase_leases_for_reconciliation,
                "STATUS_RECONCILING",
            )
        )

    def test_event_lease_expiry_uses_retry(self):
        self.assertTrue(
            _source_contains(
                provider_event_engine.release_expired_event_leases,
                "STATUS_RETRY",
            )
        )

    def test_recovery_uses_row_lock(self):
        self.assertTrue(
            _source_contains(
                recovery_engine.claim_recovery_purchase,
                "select_for_update",
            )
        )

    def test_report_requires_all_checks_and_zero_duplicates(self):
        report = ConcurrencyReport(
            backend="test",
            supports_select_for_update=True,
            supports_skip_locked=True,
            checks=(
                ConcurrencyCheck("a", True, "ok"),
                ConcurrencyCheck("b", True, "ok"),
            ),
            open_purchase_duplicates=0,
            provider_event_duplicates=0,
            active_purchase_leases=0,
            active_event_leases=0,
        )
        self.assertTrue(report.passed)

    def test_report_fails_on_duplicate(self):
        report = ConcurrencyReport(
            backend="test",
            supports_select_for_update=True,
            supports_skip_locked=True,
            checks=(ConcurrencyCheck("a", True, "ok"),),
            open_purchase_duplicates=1,
            provider_event_duplicates=0,
            active_purchase_leases=0,
            active_event_leases=0,
        )
        self.assertFalse(report.passed)
