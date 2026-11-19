from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
import inspect

from django.conf import settings
from django.test import SimpleTestCase

from payments import views
from payments.simple_ui import build_payment_simple_step


class PS6ASimplePaymentUITests(SimpleTestCase):
    def _payment(self, *, status="pending", balance="100.00"):
        order = SimpleNamespace(balance_due=Decimal(balance))
        return SimpleNamespace(
            status=status,
            order=order,
            can_approve=status in {"pending", "under_review"},
        )

    def _template(self):
        return (
            Path(settings.BASE_DIR)
            / "templates"
            / "payments"
            / "detalle_pago.html"
        ).read_text(encoding="utf-8")

    def test_authorized_pending_payment_has_review_as_primary_task(self):
        step = build_payment_simple_step(
            self._payment(status="under_review"),
            can_approve_payment=True,
        )
        self.assertEqual(step.code, "review")
        self.assertEqual(step.title, "Requiere tu revisión")

    def test_non_approver_sees_waiting_message_not_action(self):
        step = build_payment_simple_step(
            self._payment(status="pending"),
            can_approve_payment=False,
        )
        self.assertEqual(step.code, "waiting_review")
        self.assertIn("persona autorizada", step.description)

    def test_approved_payment_explains_remaining_balance(self):
        step = build_payment_simple_step(
            self._payment(status="approved", balance="75.50"),
            can_approve_payment=False,
        )
        self.assertEqual(step.code, "approved")
        self.assertIn("$75.50", step.description)

    def test_approved_fully_covered_payment_stays_out_of_warehouse_actions(self):
        step = build_payment_simple_step(
            self._payment(status="approved", balance="0.00"),
            can_approve_payment=False,
        )
        self.assertEqual(step.code, "approved")
        self.assertIn("área correspondiente", step.description)

    def test_terminal_payment_states_are_human_readable(self):
        expected = {
            "rejected": "rejected",
            "cancelled": "cancelled",
            "refunded": "refunded",
        }
        for status, code in expected.items():
            with self.subTest(status=status):
                step = build_payment_simple_step(
                    self._payment(status=status),
                    can_approve_payment=False,
                )
                self.assertEqual(step.code, code)

    def test_template_has_one_primary_payment_area_and_no_picking_shortcut(self):
        template = self._template()
        self.assertIn('data-ps6a-primary="payment"', template)
        self.assertNotIn("order_operational_step", template)
        self.assertNotIn("warehouse:generar_picking", template)
        self.assertIn("Aprobar pago", template)
        self.assertIn("Resoluciones y opciones avanzadas", template)

    def test_sensitive_payment_actions_are_hidden_without_capability(self):
        template = self._template()
        self.assertIn(
            "{% if payment.can_cancel and can_cancel_payment %}",
            template,
        )
        self.assertIn(
            "{% if payment.can_refund and can_refund_payment %}",
            template,
        )
        self.assertIn("{% if can_confirm_refund %}", template)
        source = inspect.getsource(views.detalle_pago)
        for capability in (
            "payments.cancel",
            "payments.refund",
            "payments.refund.confirm",
            "payments.proof.view",
            "sales.view",
        ):
            self.assertIn(f'has_capability("{capability}")', source)

    def test_proof_and_sales_navigation_are_guarded(self):
        template = self._template()
        self.assertIn("payment.proof_file and can_view_payment_proof", template)
        self.assertIn("payment.order and can_view_sales", template)
