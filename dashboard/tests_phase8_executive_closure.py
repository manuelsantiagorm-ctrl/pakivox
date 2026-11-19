from datetime import date
from decimal import Decimal
from unittest import TestCase

from dashboard.services.ceo_executive_closure import (
    build_executive_csv,
    build_executive_goals,
    build_prioritized_actions,
    question_asks_executive_closure,
)


class CEOExecutiveClosureTests(TestCase):
    def test_detects_integral_summary(self):
        self.assertTrue(question_asks_executive_closure("Dame el resumen ejecutivo integral"))

    def test_detects_action_plan(self):
        self.assertTrue(question_asks_executive_closure("Necesito un plan de acción"))

    def test_detects_export(self):
        self.assertTrue(question_asks_executive_closure("Genera la exportación ejecutiva"))

    def test_accents_are_normalized(self):
        self.assertTrue(question_asks_executive_closure("¿Cuáles son las prioridades del negocio?"))

    def test_daily_sales_is_not_intercepted(self):
        self.assertFalse(question_asks_executive_closure("¿Cuánto vendí hoy?"))

    def test_critical_action_is_first(self):
        attention = {"findings": [
            {"code": "medium", "severity": "medium", "module": "crm", "title": "CRM", "evidence": "1", "recommended_action": "A", "action_url": "/crm/"},
            {"code": "critical", "severity": "critical", "module": "warehouse", "title": "Envío", "evidence": "2", "recommended_action": "B", "action_url": "/warehouse/"},
        ]}
        actions = build_prioritized_actions(attention=attention, inventory={}, financial={})
        self.assertEqual(actions[0]["priority"], "critical")

    def test_actions_require_human_approval(self):
        attention = {"findings": [{"code": "x", "severity": "high", "module": "sales", "title": "X", "evidence": "E", "recommended_action": "A", "action_url": "/"}]}
        action = build_prioritized_actions(attention=attention, inventory={}, financial={})[0]
        self.assertTrue(action["requires_human_approval"])

    def test_duplicate_codes_are_removed(self):
        finding = {"code": "x", "severity": "high", "module": "sales", "title": "X", "evidence": "E", "recommended_action": "A", "action_url": "/"}
        actions = build_prioritized_actions(attention={"findings": [finding, finding]}, inventory={}, financial={})
        self.assertEqual(len(actions), 1)

    def test_inventory_action_uses_remaining_purchase(self):
        inventory = {"items": [{"risk": "alto", "remaining_purchase_internal": 7, "reorder_internal": 10}]}
        actions = build_prioritized_actions(attention={}, inventory=inventory, financial={})
        self.assertIn("7 piezas internas", actions[0]["evidence"])

    def test_financial_alert_becomes_action(self):
        financial = {"summary": {}, "alerts": [{"code": "negative_projected_flow", "evidence": -100}]}
        actions = build_prioritized_actions(attention={}, inventory={}, financial=financial)
        self.assertEqual(actions[0]["area"], "finance")

    def test_unknown_financial_alert_is_not_invented(self):
        financial = {"summary": {}, "alerts": [{"code": "unknown", "evidence": 1}]}
        self.assertEqual(build_prioritized_actions(attention={}, inventory={}, financial=financial), [])

    def test_receivables_goal_reduces_baseline_ten_percent(self):
        goals = build_executive_goals(financial={"summary": {"accounts_receivable": 1000}}, inventory={}, actions=[], generated_date=date(2026, 7, 31))
        self.assertEqual(goals[0]["target"], Decimal("900.00"))

    def test_inventory_goal_targets_zero_risks(self):
        inventory = {"items": [{"risk": "critico"}, {"risk": "alto"}, {"risk": "bajo"}]}
        goals = build_executive_goals(financial={}, inventory=inventory, actions=[], generated_date=date(2026, 7, 31))
        self.assertEqual(goals[0]["baseline"], 2)
        self.assertEqual(goals[0]["target"], 0)

    def test_critical_goal_has_two_day_deadline(self):
        actions = [{"priority": "critical"}]
        goals = build_executive_goals(financial={}, inventory={}, actions=actions, generated_date=date(2026, 7, 31))
        self.assertEqual(goals[0]["deadline"], "2026-08-02")

    def test_csv_has_header_and_action(self):
        actions = [{"priority_label": "Alta", "area": "sales", "recommended_action": "Cobrar", "evidence": "Saldo", "due_days": 2, "action_url": "/pagos/"}]
        content = build_executive_csv(actions)
        self.assertIn("prioridad,area,accion", content)
        self.assertIn("Cobrar", content)

    def test_csv_escapes_commas(self):
        actions = [{"priority_label": "Alta", "area": "sales", "recommended_action": "Cobrar, revisar", "evidence": "Saldo", "due_days": 2, "action_url": "/pagos/"}]
        self.assertIn('"Cobrar, revisar"', build_executive_csv(actions))

    def test_actions_are_capped_at_ten(self):
        findings = [{"code": str(i), "severity": "low", "module": "crm", "title": str(i), "evidence": "E", "recommended_action": "A", "action_url": "/"} for i in range(20)]
        self.assertEqual(len(build_prioritized_actions(attention={"findings": findings}, inventory={}, financial={})), 10)

    def test_empty_sources_produce_no_false_action(self):
        self.assertEqual(build_prioritized_actions(attention={}, inventory={}, financial={}), [])
