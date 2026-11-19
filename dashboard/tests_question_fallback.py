from django.test import SimpleTestCase

from dashboard.services.ceo_question_fallback import (
    build_question_fallback,
    build_technical_fallback,
)


class CEOQuestionFallbackTests(SimpleTestCase):
    def test_empty_question_returns_local_fallback(self):
        result = build_question_fallback("")
        self.assertEqual(result["fallback_reason"], "empty_question")
        self.assertFalse(result["quota_consumed"])

    def test_ambiguous_question_requests_area(self):
        result = build_question_fallback("¿Cómo vamos?")
        self.assertEqual(result["fallback_reason"], "ambiguous_question")

    def test_bank_balance_is_not_inferred_from_collections(self):
        result = build_question_fallback("¿Cuánto dinero tengo en el banco?")
        self.assertEqual(result["fallback_reason"], "unavailable_bank_data")
        self.assertIn("no está conectado", result["respuesta_directa"])

    def test_outside_question_is_rejected(self):
        result = build_question_fallback("¿Quién ganará el partido mañana?")
        self.assertEqual(result["fallback_reason"], "outside_wms360")

    def test_automatic_purchase_is_blocked(self):
        result = build_question_fallback("Compra automáticamente todo lo necesario")
        self.assertEqual(result["fallback_reason"], "automatic_action_blocked")

    def test_automatic_payment_is_blocked(self):
        result = build_question_fallback("Haz el pago automáticamente")
        self.assertEqual(result["fallback_reason"], "automatic_action_blocked")

    def test_exact_prediction_is_not_guaranteed(self):
        result = build_question_fallback("Dime exactamente cuánto venderé el próximo mes")
        self.assertEqual(result["fallback_reason"], "exact_prediction_unavailable")

    def test_maximum_four_suggestions(self):
        result = build_question_fallback("¿Quién ganará mañana?")
        self.assertLessEqual(len(result["suggestions"]), 4)

    def test_suggestions_have_safe_shape(self):
        result = build_question_fallback("¿Quién ganará mañana?")
        self.assertTrue(all(set(item) == {"label", "question"} for item in result["suggestions"]))

    def test_financial_situation_is_not_offered_as_suggestion(self):
        results = (
            build_question_fallback("¿Quién ganará mañana?"),
            build_question_fallback("¿Cuánto dinero tengo en el banco?"),
            build_technical_fallback(),
        )
        for result in results:
            serialized = str(result["suggestions"]).lower()
            self.assertNotIn("situación financiera", serialized)
            self.assertNotIn("situacion financiera", serialized)

    def test_sales_question_continues_to_existing_engine(self):
        self.assertIsNone(build_question_fallback("¿Cuánto vendí hoy?"))

    def test_comparison_continues_to_existing_engine(self):
        self.assertIsNone(build_question_fallback("Compara este mes contra el mes anterior"))

    def test_inventory_question_continues_to_existing_engine(self):
        self.assertIsNone(build_question_fallback("¿Cuántos días de inventario me quedan?"))

    def test_financial_question_continues_to_existing_engine(self):
        self.assertIsNone(build_question_fallback("¿Cuál fue mi utilidad y margen?"))

    def test_attention_question_continues_to_existing_engine(self):
        self.assertIsNone(build_question_fallback("¿Qué requiere mi atención hoy?"))

    def test_executive_question_continues_to_existing_engine(self):
        self.assertIsNone(build_question_fallback("Dame mi resumen ejecutivo y plan de acción"))

    def test_warehouse_question_continues_for_permission_layer(self):
        self.assertIsNone(build_question_fallback("Muéstrame las ventas de Bodega Sur"))

    def test_accents_and_case_are_normalized(self):
        result = build_question_fallback("¿CUÁNTO TENGO EN MI CUENTA BANCARIA?")
        self.assertEqual(result["fallback_reason"], "unavailable_bank_data")

    def test_fallback_never_contains_sensitive_identifiers(self):
        result = build_question_fallback("¿Quién ganará mañana?")
        serialized = str(result).lower()
        self.assertNotIn("company_id", serialized)
        self.assertNotIn("warehouse_id", serialized)

    def test_technical_error_returns_safe_alternatives(self):
        result = build_technical_fallback()
        self.assertEqual(result["fallback_reason"], "technical_error")
        self.assertFalse(result["quota_consumed"])
