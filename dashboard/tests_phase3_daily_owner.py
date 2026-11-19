from django.test import SimpleTestCase

from dashboard.services.ceo_daily_owner import build_daily_owner_direct_answer, detect_daily_owner_intent


class CEODailyOwnerTests(SimpleTestCase):
    def setUp(self):
        self.scope = {"label": "Toda la empresa"}
        self.snapshot = {"resolved_period": {"label": "hoy, 31 de julio de 2026"}, "payload": {
            "selected_period_summary": {"label": "hoy, 31 de julio de 2026", "registered_orders": 3, "registered_sales": 1250, "cash_collected": 900, "recognized_gross_profit": 310},
            "top_products_selected_period": [{"sku": "A", "product_name": "Producto A", "sales_units": 2, "internal_units": 2}, {"sku": "PACK12", "product_name": "Paquete", "sales_units": 4, "internal_units": 48}],
            "warehouse_analysis": {"warehouses": [{"warehouse_name": "Centro", "registered_sales": 800}, {"warehouse_name": "Norte", "registered_sales": 450}]},
        }}

    def answer(self, question):
        return build_daily_owner_direct_answer(question=question, snapshot=self.snapshot, warehouse_scope=self.scope)

    def test_detects_sales_without_accents(self):
        self.assertEqual(detect_daily_owner_intent("cuanto vendi hoy"), "sales")

    def test_sales_are_exact_and_labeled(self):
        self.assertIn("$1,250.00 MXN en ventas", self.answer("¿Cuánto vendí hoy?"))

    def test_orders_are_exact(self):
        self.assertIn("3 pedidos", self.answer("¿Cuántos pedidos hice hoy?"))

    def test_collection_uses_approved_cash(self):
        self.assertIn("$900.00 MXN en pagos aprobados", self.answer("¿Cuánto cobré hoy?"))

    def test_profit_is_identified_as_recognized(self):
        answer = self.answer("¿Cuál fue mi utilidad hoy?")
        self.assertIn("$310.00 MXN", answer)
        self.assertIn("pedidos entregados", answer)

    def test_top_product_uses_commercial_and_internal_units(self):
        answer = self.answer("¿Qué producto se vendió más hoy?")
        self.assertIn("Paquete (PACK12)", answer)
        self.assertIn("4 unidades comerciales (48 piezas internas)", answer)

    def test_top_warehouse_uses_registered_sales(self):
        answer = self.answer("¿Qué bodega vendió más hoy?")
        self.assertIn("Centro", answer)
        self.assertIn("$800.00 MXN", answer)

    def test_no_data_top_product_is_explicit(self):
        self.snapshot["payload"]["top_products_selected_period"] = []
        self.assertIn("no hay productos vendidos", self.answer("producto más vendido hoy"))

    def test_non_quick_question_is_left_for_gemini(self):
        self.assertIsNone(self.answer("Dame una estrategia para crecer"))

    def test_scope_and_period_are_always_visible(self):
        answer = self.answer("¿Cuánto vendí hoy?")
        self.assertIn("31 de julio de 2026", answer)
        self.assertIn("Toda la empresa", answer)
