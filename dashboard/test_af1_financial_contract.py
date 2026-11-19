"""AF-1 · Contrato TDD para atribución financiera multibodega.

Esta suite es deliberadamente ROJA hasta implementar AF-2/AF-4. Los fallos
deben ser AssertionError con prefijo ``AF1-RED``; nunca errores de importación.
"""

from decimal import Decimal
from importlib import import_module

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist
from django.test import SimpleTestCase

from companies.models import Company
from sales.models import Order


def _future_math(testcase):
    try:
        return import_module("dashboard.services.warehouse_financial_math")
    except ModuleNotFoundError:
        testcase.fail(
            "AF1-RED: falta dashboard.services.warehouse_financial_math; "
            "el contrato existe antes que la implementación."
        )


def _future_fact_model(testcase):
    try:
        return apps.get_model("dashboard", "WarehouseFinancialFact")
    except LookupError:
        testcase.fail(
            "AF1-RED: falta dashboard.WarehouseFinancialFact; "
            "se implementará después del contrato TDD."
        )


class AF1TemporalModelContractTests(SimpleTestCase):
    def test_company_declares_iana_timezone(self):
        try:
            field = Company._meta.get_field("timezone")
        except FieldDoesNotExist:
            self.fail("AF1-RED: Company.timezone todavía no existe.")
        self.assertEqual(field.default, "America/Mexico_City")
        self.assertGreaterEqual(field.max_length, 64)

    def test_order_declares_immutable_confirmation_timestamp(self):
        try:
            field = Order._meta.get_field("confirmed_at")
        except FieldDoesNotExist:
            self.fail("AF1-RED: Order.confirmed_at todavía no existe.")
        self.assertTrue(field.null)
        self.assertTrue(field.blank)
        self.assertTrue(field.db_index)


class AF1FinancialFactModelContractTests(SimpleTestCase):
    def test_financial_fact_model_exists(self):
        self.assertIsNotNone(_future_fact_model(self))

    def test_financial_fact_keeps_rounding_and_legacy_difference_separate(self):
        model = _future_fact_model(self)
        names = {field.name for field in model._meta.get_fields()}
        required = {
            "rounding_residual_cents",
            "legacy_difference_cents",
            "has_legacy_inconsistency",
            "legacy_inconsistency_reason",
        }
        self.assertTrue(required.issubset(names), f"AF1-RED: faltan {required - names}")

    def test_financial_fact_carries_time_quality_and_actor_metadata(self):
        model = _future_fact_model(self)
        names = {field.name for field in model._meta.get_fields()}
        required = {
            "effective_at_utc",
            "effective_local_date",
            "date_basis",
            "occurred_at",
            "recorded_at",
            "timezone_snapshot",
            "quality_label",
            "is_estimated",
            "allocation_method",
            "actor",
            "actor_type",
            "actor_snapshot",
            "source_event_type",
            "source_event_id",
            "metadata",
        }
        self.assertTrue(required.issubset(names), f"AF1-RED: faltan {required - names}")


class AF1MoneyContractTests(SimpleTestCase):
    def test_decimal_to_cents_uses_round_half_up(self):
        math = _future_math(self)
        self.assertEqual(math.money_to_cents(Decimal("1.005")), 101)
        self.assertEqual(math.money_to_cents(Decimal("1.004")), 100)
        self.assertEqual(math.money_to_cents(Decimal("-1.005")), -101)

    def test_financial_math_rejects_float(self):
        math = _future_math(self)
        with self.assertRaises(TypeError):
            math.money_to_cents(1.005)

    def test_largest_remainder_reconciles_exactly(self):
        math = _future_math(self)
        result = math.allocate_largest_remainder(
            total_cents=100,
            weights=[("sur", Decimal("1")), ("principal", Decimal("2"))],
        )
        self.assertEqual(result, {"sur": 33, "principal": 67})
        self.assertEqual(sum(result.values()), 100)

    def test_largest_remainder_tie_is_deterministic(self):
        math = _future_math(self)
        result = math.allocate_largest_remainder(
            total_cents=1,
            weights=[("warehouse:2/item:9", Decimal("1")), ("warehouse:1/item:8", Decimal("1"))],
        )
        self.assertEqual(
            result,
            {"warehouse:1/item:8": 1, "warehouse:2/item:9": 0},
        )

    def test_row_total_and_profit_are_derived_not_allocated(self):
        math = _future_math(self)
        row = math.derive_warehouse_row(
            subtotal_cents=100_00,
            discount_cents=10_00,
            tax_cents=14_40,
            shipping_cents=5_00,
            cost_cents=55_00,
            legacy_difference_cents=0,
            rounding_residual_cents=1,
        )
        self.assertEqual(row["total_cents"], 109_40)
        self.assertEqual(row["gross_profit_cents"], 35_00)
        self.assertEqual(row["rounding_residual_cents"], 1)

    def test_legacy_difference_is_explicit_in_total(self):
        math = _future_math(self)
        row = math.derive_warehouse_row(
            subtotal_cents=100_00,
            discount_cents=0,
            tax_cents=16_00,
            shipping_cents=0,
            cost_cents=60_00,
            legacy_difference_cents=3,
            rounding_residual_cents=0,
        )
        self.assertEqual(row["total_cents"], 116_03)
        self.assertTrue(row["has_legacy_inconsistency"])


class AF1QualityAndReconciliationContractTests(SimpleTestCase):
    def test_quality_dominance_real_attributed_estimated(self):
        math = _future_math(self)
        self.assertEqual(math.dominant_quality("real", "real"), "real")
        self.assertEqual(math.dominant_quality("real", "attributed"), "attributed")
        self.assertEqual(math.dominant_quality("attributed", "estimated"), "estimated")

    def test_inferred_confirmation_date_maps_to_estimated(self):
        math = _future_math(self)
        result = math.quality_for_value(
            amount_quality="real",
            confirmation_date_is_inferred=True,
        )
        self.assertEqual(result["quality_label"], "estimated")
        self.assertIn("inferred_confirmation_date", result["estimate_reasons"])

    def test_all_warehouses_equals_assigned_plus_unassigned(self):
        math = _future_math(self)
        result = math.reconcile_scope(
            consolidated_cents=1_000_00,
            warehouse_cents={"sur": 300_00, "principal": 600_00},
            unassigned_cents=100_00,
        )
        self.assertTrue(result["is_reconciled"])
        self.assertEqual(result["difference_cents"], 0)

    def test_reconciliation_detects_but_never_repairs(self):
        math = _future_math(self)
        result = math.reconcile_scope(
            consolidated_cents=1_000_00,
            warehouse_cents={"sur": 300_00, "principal": 600_00},
            unassigned_cents=99_99,
        )
        self.assertFalse(result["is_reconciled"])
        self.assertEqual(result["difference_cents"], 1)
        self.assertNotIn("corrected_values", result)
