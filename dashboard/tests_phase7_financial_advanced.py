from decimal import Decimal
from unittest import TestCase

from dashboard.services.ceo_financial_advanced import (
    calculate_break_even,
    calculate_cash_projection,
    calculate_sales_scenario,
    percentage_change,
    question_asks_financial_advanced,
)


class CEOFinancialAdvancedTests(TestCase):
    def test_detects_cash_flow(self):
        self.assertTrue(question_asks_financial_advanced("Pronóstico de flujo de efectivo"))

    def test_detects_profitability(self):
        self.assertTrue(question_asks_financial_advanced("¿Qué producto es más rentable?"))

    def test_detects_break_even(self):
        self.assertTrue(question_asks_financial_advanced("¿Cuál es mi punto de equilibrio?"))

    def test_detects_minus_ten_scenario(self):
        self.assertTrue(question_asks_financial_advanced("¿Qué pasa si vendo 10% menos?"))

    def test_accents_are_normalized(self):
        self.assertTrue(question_asks_financial_advanced("¿Cuál es mi pronóstico de flujo?"))

    def test_ordinary_daily_sales_is_not_intercepted(self):
        self.assertFalse(question_asks_financial_advanced("¿Cuánto vendí hoy?"))

    def test_positive_percentage_change(self):
        self.assertEqual(percentage_change(120, 100), Decimal("20.00"))

    def test_negative_percentage_change(self):
        self.assertEqual(percentage_change(80, 100), Decimal("-20.00"))

    def test_zero_base_never_invents_percentage(self):
        self.assertIsNone(percentage_change(100, 0))

    def test_both_zero_is_zero_percent(self):
        self.assertEqual(percentage_change(0, 0), Decimal("0.00"))

    def test_break_even_is_exact(self):
        result = calculate_break_even(fixed_expenses=30000, sales=100000, gross_profit=40000)
        self.assertEqual(result["contribution_margin_percent"], Decimal("40.00"))
        self.assertEqual(result["break_even_sales"], Decimal("75000.00"))

    def test_break_even_rejects_zero_sales(self):
        result = calculate_break_even(fixed_expenses=30000, sales=0, gross_profit=0)
        self.assertIsNone(result["break_even_sales"])

    def test_break_even_rejects_negative_margin(self):
        result = calculate_break_even(fixed_expenses=30000, sales=100000, gross_profit=-1)
        self.assertIsNone(result["break_even_sales"])

    def test_minus_ten_scenario(self):
        result = calculate_sales_scenario(sales=100000, gross_profit=30000, variation_percent=-10)
        self.assertEqual(result["projected_sales"], Decimal("90000.00"))
        self.assertEqual(result["projected_gross_profit"], Decimal("27000.00"))

    def test_scenario_preserves_margin_assumption(self):
        result = calculate_sales_scenario(sales=200, gross_profit=50, variation_percent=10)
        self.assertEqual(result["sales_difference"], Decimal("20.00"))
        self.assertEqual(result["gross_profit_difference"], Decimal("5.00"))

    def test_cash_projection_is_based_on_daily_average(self):
        result = calculate_cash_projection(collected=30000, expenses=12000, observed_days=30, target_days=30)
        self.assertEqual(result["projected_net_flow"], Decimal("18000.00"))

    def test_cash_projection_supports_fifteen_days(self):
        result = calculate_cash_projection(collected=30000, expenses=12000, observed_days=30, target_days=15)
        self.assertEqual(result["projected_inflows"], Decimal("15000.00"))
        self.assertEqual(result["projected_outflows"], Decimal("6000.00"))

    def test_cash_projection_caps_horizon(self):
        result = calculate_cash_projection(collected=90, expenses=0, observed_days=30, target_days=365)
        self.assertEqual(result["target_days"], 90)
        self.assertEqual(result["projected_inflows"], Decimal("270.00"))

    def test_cash_projection_never_divides_by_zero(self):
        result = calculate_cash_projection(collected=10, expenses=5, observed_days=0, target_days=1)
        self.assertEqual(result["projected_net_flow"], Decimal("5.00"))

    def test_projection_declares_its_method(self):
        result = calculate_cash_projection(collected=10, expenses=5)
        self.assertEqual(result["method"], "promedio_diario_historico")
