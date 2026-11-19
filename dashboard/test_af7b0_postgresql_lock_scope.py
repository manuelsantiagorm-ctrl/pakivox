import inspect

from django.test import SimpleTestCase

from dashboard.services import warehouse_cash_attribution
from dashboard.services import warehouse_financial_attribution


class AF7B0PostgreSQLLockScopeTests(SimpleTestCase):
    def test_active_sales_locks_only_financial_fact_rows(self):
        source = inspect.getsource(warehouse_financial_attribution._active_sales)
        self.assertIn('select_for_update(of=("self",))', source)
        self.assertIn('select_related("warehouse")', source)

    def test_refund_collections_lock_only_financial_fact_rows(self):
        source = inspect.getsource(
            warehouse_cash_attribution.record_locked_payment_refund
        )
        self.assertIn('select_for_update(of=("self",))', source)
        self.assertIn('select_related("warehouse")', source)
