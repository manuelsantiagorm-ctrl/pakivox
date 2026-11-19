from copy import deepcopy
from unittest import TestCase

from dashboard.services.ceo_inventory_predictive import (
    calculate_item_forecast, question_asks_inventory_prediction,
    resolve_target_days, suggest_transfers,
)


class CEOInventoryPredictiveTests(TestCase):
    def test_detects_days_of_inventory(self):
        self.assertTrue(question_asks_inventory_prediction("¿Cuántos días de inventario me quedan?"))

    def test_detects_accents(self):
        self.assertTrue(question_asks_inventory_prediction("¿Qué debo reabastecer esta semana?"))

    def test_ordinary_sales_question_is_not_intercepted(self):
        self.assertFalse(question_asks_inventory_prediction("¿Cuánto vendí hoy?"))

    def test_target_15_days(self):
        self.assertEqual(resolve_target_days("cubrir 15 días"), 15)

    def test_target_60_days(self):
        self.assertEqual(resolve_target_days("comprar para 60 dias"), 60)

    def test_default_target_is_30(self):
        self.assertEqual(resolve_target_days("días de inventario"), 30)

    def test_coverage_is_exact(self):
        row = calculate_item_forecast(available=100, consumed=50, observed_days=10, target_days=30)
        self.assertEqual(row["coverage_days"], 20.0)

    def test_reorder_includes_minimum(self):
        row = calculate_item_forecast(available=20, consumed=30, observed_days=10, target_days=15, min_stock=5)
        self.assertEqual(row["reorder_internal"], 30)

    def test_excess_is_detected(self):
        row = calculate_item_forecast(available=100, consumed=10, observed_days=10, target_days=30)
        self.assertEqual(row["excess_internal"], 70)

    def test_no_history_never_invents_consumption(self):
        row = calculate_item_forecast(available=100, consumed=0, observed_days=0, target_days=30)
        self.assertIsNone(row["coverage_days"])
        self.assertEqual(row["risk"], "sin_historial")

    def test_zero_stock_is_critical_when_consuming(self):
        row = calculate_item_forecast(available=0, consumed=20, observed_days=10, target_days=30)
        self.assertEqual(row["risk"], "critico")

    def test_target_is_capped_at_sixty(self):
        row = calculate_item_forecast(available=0, consumed=10, observed_days=10, target_days=100)
        self.assertEqual(row["target_stock_internal"], 60)

    def _items(self):
        return [
            {"sku": "SKU1", "warehouse_id": 1, "warehouse_name": "Norte", "reorder_internal": 0, "excess_internal": 40},
            {"sku": "SKU1", "warehouse_id": 2, "warehouse_name": "Sur", "reorder_internal": 25, "excess_internal": 0},
        ]

    def test_transfer_precedes_purchase(self):
        items = self._items()
        transfers = suggest_transfers(items)
        self.assertEqual(transfers[0]["quantity_internal"], 25)
        self.assertEqual(items[1]["remaining_purchase_internal"], 0)

    def test_transfer_never_crosses_sku(self):
        items = self._items()
        items[0]["sku"] = "OTHER"
        self.assertEqual(suggest_transfers(items), [])

    def test_transfer_never_crosses_same_warehouse(self):
        items = self._items()
        items[1]["warehouse_id"] = 1
        self.assertEqual(suggest_transfers(items), [])

    def test_partial_transfer_leaves_purchase(self):
        items = self._items()
        items[0]["excess_internal"] = 10
        suggest_transfers(items)
        self.assertEqual(items[1]["remaining_purchase_internal"], 15)

    def test_input_can_be_copied_without_shared_state(self):
        original = self._items()
        suggest_transfers(deepcopy(original))
        self.assertNotIn("remaining_purchase_internal", original[1])
