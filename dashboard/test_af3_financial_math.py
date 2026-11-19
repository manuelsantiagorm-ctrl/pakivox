from decimal import Decimal

from django.test import SimpleTestCase

from dashboard.services.warehouse_financial_math import (
    allocate_largest_remainder,
    cents_to_money,
    derive_warehouse_row,
    dominant_quality,
    money_to_cents,
    quality_for_value,
    reconcile_scope,
)


class AF3MoneyConversionTests(SimpleTestCase):
    def test_round_half_up_handles_positive_and_negative_ties(self):
        self.assertEqual(money_to_cents(Decimal("1.005")), 101)
        self.assertEqual(money_to_cents(Decimal("-1.005")), -101)

    def test_float_and_bool_are_rejected(self):
        with self.assertRaises(TypeError):
            money_to_cents(1.005)
        with self.assertRaises(TypeError):
            cents_to_money(True)

    def test_non_finite_decimal_is_rejected(self):
        for value in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                money_to_cents(value)

    def test_cents_round_trip_is_exact(self):
        self.assertEqual(cents_to_money(12345), Decimal("123.45"))
        self.assertEqual(money_to_cents(cents_to_money(-12345)), -12345)


class AF3LargestRemainderTests(SimpleTestCase):
    def test_allocation_reconciles_to_total(self):
        result = allocate_largest_remainder(
            total_cents=100,
            weights=[("sur", Decimal("1")), ("principal", Decimal("2"))],
        )
        self.assertEqual(result, {"principal": 67, "sur": 33})
        self.assertEqual(sum(result.values()), 100)

    def test_tie_break_is_stable_regardless_of_input_order(self):
        first = allocate_largest_remainder(
            total_cents=1,
            weights=[("b", Decimal("1")), ("a", Decimal("1"))],
        )
        second = allocate_largest_remainder(
            total_cents=1,
            weights=[("a", Decimal("1")), ("b", Decimal("1"))],
        )
        self.assertEqual(first, {"a": 1, "b": 0})
        self.assertEqual(first, second)

    def test_negative_total_reconciles_exactly(self):
        result = allocate_largest_remainder(
            total_cents=-5,
            weights=[("a", Decimal("1")), ("b", Decimal("1"))],
        )
        self.assertEqual(result, {"a": -3, "b": -2})
        self.assertEqual(sum(result.values()), -5)

    def test_zero_total_with_zero_weights_is_valid(self):
        self.assertEqual(
            allocate_largest_remainder(
                total_cents=0,
                weights=[("a", Decimal("0")), ("b", Decimal("0"))],
            ),
            {"a": 0, "b": 0},
        )

    def test_nonzero_total_with_zero_weights_is_rejected(self):
        with self.assertRaises(ValueError):
            allocate_largest_remainder(
                total_cents=1,
                weights=[("a", Decimal("0"))],
            )

    def test_duplicate_key_negative_weight_and_float_are_rejected(self):
        invalid_cases = [
            [("a", Decimal("1")), ("a", Decimal("2"))],
            [("a", Decimal("-1"))],
            [("a", 1.0)],
        ]
        for weights in invalid_cases:
            with self.subTest(weights=weights), self.assertRaises((TypeError, ValueError)):
                allocate_largest_remainder(total_cents=1, weights=weights)


class AF3DerivedRowTests(SimpleTestCase):
    def test_total_and_profit_are_derived_from_same_row(self):
        row = derive_warehouse_row(
            subtotal_cents=10000,
            discount_cents=1000,
            tax_cents=1440,
            shipping_cents=500,
            cost_cents=5500,
            rounding_residual_cents=1,
        )
        self.assertEqual(row["total_cents"], 10940)
        self.assertEqual(row["gross_profit_cents"], 3500)
        self.assertEqual(row["rounding_residual_cents"], 1)

    def test_legacy_difference_is_separate_and_auditable(self):
        row = derive_warehouse_row(
            subtotal_cents=10000,
            discount_cents=0,
            tax_cents=1600,
            shipping_cents=0,
            cost_cents=6000,
            legacy_difference_cents=3,
        )
        self.assertEqual(row["total_cents"], 11603)
        self.assertTrue(row["has_legacy_inconsistency"])

    def test_non_integer_component_is_rejected(self):
        with self.assertRaises(TypeError):
            derive_warehouse_row(
                subtotal_cents=Decimal("100.00"),
                discount_cents=0,
                tax_cents=0,
                shipping_cents=0,
                cost_cents=0,
            )


class AF3QualityAndReconciliationTests(SimpleTestCase):
    def test_quality_dominance_is_monotonic(self):
        self.assertEqual(dominant_quality("real", "attributed"), "attributed")
        self.assertEqual(dominant_quality("attributed", "estimated"), "estimated")

    def test_invalid_quality_is_rejected(self):
        with self.assertRaises(ValueError):
            dominant_quality("real", "invented")

    def test_inferred_confirmation_uses_estimated_language(self):
        result = quality_for_value(
            amount_quality="real",
            confirmation_date_is_inferred=True,
        )
        self.assertEqual(result["quality_label"], "estimated")
        self.assertTrue(result["is_estimated"])
        self.assertEqual(result["estimate_reasons"], ["inferred_confirmation_date"])

    def test_reconciliation_detects_but_does_not_repair(self):
        result = reconcile_scope(
            consolidated_cents=100000,
            warehouse_cents={"sur": 30000, "principal": 60000},
            unassigned_cents=9999,
        )
        self.assertFalse(result["is_reconciled"])
        self.assertEqual(result["difference_cents"], 1)
        self.assertNotIn("corrected_values", result)

    def test_reconciliation_accepts_exact_negative_reversal(self):
        result = reconcile_scope(
            consolidated_cents=-10000,
            warehouse_cents={"sur": -7000},
            unassigned_cents=-3000,
        )
        self.assertTrue(result["is_reconciled"])
        self.assertEqual(result["difference_cents"], 0)
