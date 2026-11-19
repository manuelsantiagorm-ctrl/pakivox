from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.utils import timezone

from dashboard.services.ceo_attention import (
    SEVERITY_ORDER,
    _age_days,
    _finding,
    _normalize,
    build_attention_diagnosis,
    question_asks_attention_diagnosis,
)


class EmptyQuerySet:
    def filter(self, *args, **kwargs):
        return self

    def distinct(self):
        return self

    def count(self):
        return 0

    def aggregate(self, **kwargs):
        return {key: 0 for key in kwargs}

    def order_by(self, *args):
        return self

    def values_list(self, *args, **kwargs):
        return self

    def first(self):
        return None


MODEL_PATHS = [
    "Payment", "Order", "PickingOrder", "PackingOrder", "OrderFulfillment",
    "ShippingOrder", "StockCriticalAlert", "CarrierShipment", "Deal",
    "WarehouseTransfer",
]


class CEOAttentionDiagnosisTests(SimpleTestCase):
    def test_detects_star_question(self):
        self.assertTrue(question_asks_attention_diagnosis("¿Qué requiere mi atención hoy?"))

    def test_detects_review_today_phrase(self):
        self.assertTrue(question_asks_attention_diagnosis("Qué debo revisar hoy"))

    def test_detects_integral_diagnosis(self):
        self.assertTrue(question_asks_attention_diagnosis("Dame un diagnóstico integral"))

    def test_ordinary_sales_question_is_not_intercepted(self):
        self.assertFalse(question_asks_attention_diagnosis("¿Cuánto vendí hoy?"))

    def test_accents_are_normalized(self):
        self.assertEqual(_normalize("ATENCIÓN   HOY"), "atencion hoy")

    def test_age_never_becomes_negative(self):
        now = timezone.now()
        self.assertEqual(_age_days(now + timedelta(days=2), now), 0)

    def test_finding_has_auditable_fields(self):
        finding = _finding(
            code="x", severity="high", title="Prueba", count=2,
            impact="Dos", evidence="Evidencia", oldest_days=3,
            action="Revisar", module="sales", url="/sales/",
        )
        self.assertEqual(finding["severity_label"], "Alta")
        self.assertEqual(finding["oldest_days"], 3)
        self.assertEqual(finding["action_url"], "/sales/")

    def test_critical_precedes_high_medium_and_low(self):
        ordered = sorted(["low", "high", "critical", "medium"], key=SEVERITY_ORDER.get)
        self.assertEqual(ordered, ["critical", "high", "medium", "low"])

    def test_no_authorized_warehouse_does_not_query_models(self):
        result = build_attention_diagnosis(
            company=object(), warehouse_scope={"warehouse_ids": []},
        )
        self.assertEqual(result["finding_count"], 0)
        self.assertIn("no hay bodegas autorizadas", result["answer"].lower())

    def test_scope_is_limited_to_ten_warehouses(self):
        empty = EmptyQuerySet()
        patches = [
            patch(f"dashboard.services.ceo_attention.{name}.objects", filter=MagicMock(return_value=empty))
            for name in MODEL_PATHS
        ]
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        result = build_attention_diagnosis(
            company=object(), warehouse_scope={"warehouse_ids": list(range(1, 15))},
        )
        self.assertEqual(result["warehouse_ids"], list(range(1, 11)))

    def test_empty_diagnosis_is_explicit_not_a_false_guarantee(self):
        empty = EmptyQuerySet()
        patches = [
            patch(f"dashboard.services.ceo_attention.{name}.objects", filter=MagicMock(return_value=empty))
            for name in MODEL_PATHS
        ]
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        result = build_attention_diagnosis(
            company=object(), warehouse_scope={"warehouse_ids": [7]},
        )
        self.assertEqual(result["finding_count"], 0)
        self.assertIn("no significa ausencia total de riesgo", result["answer"].lower())

    def test_data_gaps_are_reported(self):
        empty = EmptyQuerySet()
        patches = [
            patch(f"dashboard.services.ceo_attention.{name}.objects", filter=MagicMock(return_value=empty))
            for name in MODEL_PATHS
        ]
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        result = build_attention_diagnosis(
            company=object(), warehouse_scope={"warehouse_ids": [1]},
        )
        self.assertGreaterEqual(len(result["data_gaps"]), 2)

    def test_generated_timestamp_is_included(self):
        empty = EmptyQuerySet()
        patches = [
            patch(f"dashboard.services.ceo_attention.{name}.objects", filter=MagicMock(return_value=empty))
            for name in MODEL_PATHS
        ]
        for active_patch in patches:
            active_patch.start()
            self.addCleanup(active_patch.stop)
        now = timezone.now()
        result = build_attention_diagnosis(
            company=object(), warehouse_scope={"warehouse_ids": [1]}, now=now,
        )
        self.assertEqual(result["generated_at"], now.isoformat())

    def test_result_has_stable_contract(self):
        result = build_attention_diagnosis(
            company=object(), warehouse_scope={"warehouse_ids": []},
        )
        self.assertTrue({"generated_at", "warehouse_ids", "finding_count", "findings", "data_gaps", "answer"}.issubset(result))
