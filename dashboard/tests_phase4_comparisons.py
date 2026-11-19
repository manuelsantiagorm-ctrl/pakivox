from datetime import date

from django.test import SimpleTestCase

from dashboard.services.ceo_comparisons import (
    build_period_comparison,
    build_warehouse_comparison_answer,
    calculate_change,
    detect_comparison_metric,
    resolve_comparison_periods,
)


class CEOComparisonTests(SimpleTestCase):
    def setUp(self):
        self.today = date(2026, 7, 31)
        self.scope = {"label": "Toda la empresa"}

    def test_today_against_yesterday(self):
        periods = resolve_comparison_periods("Compara hoy contra ayer", self.today)
        self.assertEqual(periods["current"]["start_date_iso"], "2026-07-31")
        self.assertEqual(periods["previous"]["start_date_iso"], "2026-07-30")

    def test_week_uses_same_elapsed_days(self):
        periods = resolve_comparison_periods("Compara esta semana contra la semana pasada", self.today)
        self.assertEqual(periods["current"]["days"], periods["previous"]["days"])
        self.assertEqual(periods["current"]["start_date_iso"], "2026-07-27")

    def test_month_uses_same_elapsed_days(self):
        periods = resolve_comparison_periods("Compara este mes contra el mes anterior", self.today)
        self.assertEqual(periods["current"]["days"], 31)
        self.assertEqual(periods["previous"]["end_date_iso"], "2026-06-30")

    def test_named_months(self):
        periods = resolve_comparison_periods("Compara julio contra junio", self.today)
        self.assertEqual(periods["current"]["label"], "Julio 2026")
        self.assertEqual(periods["previous"]["label"], "Junio 2026")

    def test_non_comparison_is_ignored(self):
        self.assertIsNone(resolve_comparison_periods("Cuánto vendí hoy", self.today))

    def test_sales_is_default_metric(self):
        self.assertEqual(detect_comparison_metric("Compara julio contra junio"), "registered_sales")

    def test_collection_metric_is_separate(self):
        self.assertEqual(detect_comparison_metric("Compara lo cobrado hoy contra ayer"), "cash_collected")

    def test_profit_metric_is_separate(self):
        self.assertEqual(detect_comparison_metric("Compara la utilidad de julio contra junio"), "recognized_gross_profit")

    def test_margin_metric_is_percentage(self):
        self.assertEqual(detect_comparison_metric("Compara el margen de julio contra junio"), "recognized_margin_percent")

    def test_positive_percentage(self):
        change = calculate_change(150, 100)
        self.assertEqual(change["absolute"], 50)
        self.assertEqual(change["percent"], 50)

    def test_zero_previous_has_no_invented_percentage(self):
        change = calculate_change(100, 0)
        self.assertIsNone(change["percent"])

    def test_both_zero_is_zero_percent(self):
        self.assertEqual(calculate_change(0, 0)["percent"], 0)

    def test_period_answer_has_exact_values_scope_and_variation(self):
        periods = resolve_comparison_periods("Compara hoy contra ayer", self.today)
        result = build_period_comparison(
            question="Compara las ventas de hoy contra ayer",
            periods=periods,
            current_analysis={"summary": {"registered_sales": 1500}},
            previous_analysis={"summary": {"registered_sales": 1000}},
            warehouse_scope=self.scope,
        )
        self.assertIn("$1,500.00 MXN", result["answer"])
        self.assertIn("$1,000.00 MXN", result["answer"])
        self.assertIn("50.00%", result["answer"])
        self.assertIn("Toda la empresa", result["answer"])
        self.assertEqual(result["source"], "wms360_python")

    def test_warehouse_leader_and_difference_are_deterministic(self):
        analysis = {"warehouses": [
            {"warehouse_name": "Centro", "registered_sales": 1200},
            {"warehouse_name": "Norte", "registered_sales": 800},
        ]}
        result = build_warehouse_comparison_answer(
            question="Compara las ventas de las bodegas",
            warehouse_analysis=analysis,
            warehouse_scope=self.scope,
        )
        self.assertIn("Centro", result["answer"])
        self.assertIn("Norte", result["answer"])
        self.assertIn("400.00 MXN", result["answer"])
        self.assertIn("50.00%", result["answer"])

    def test_warehouse_comparison_requires_two_authorized_warehouses(self):
        result = build_warehouse_comparison_answer(
            question="Compara las bodegas",
            warehouse_analysis={"warehouses": [{"warehouse_name": "Centro", "registered_sales": 1}]},
            warehouse_scope=self.scope,
        )
        self.assertIsNone(result)

    def test_warehouse_comparison_does_not_activate_without_warehouse_topic(self):
        result = build_warehouse_comparison_answer(
            question="Compara julio contra junio",
            warehouse_analysis={"warehouses": [{"warehouse_name": "Centro"}, {"warehouse_name": "Norte"}]},
            warehouse_scope=self.scope,
        )
        self.assertIsNone(result)
