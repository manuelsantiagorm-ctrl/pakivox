from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import UserProfile
from companies.models import Company
from crm.models import Customer
from payments.models import Payment
from sales.models import Order, OrderResolutionEvent
from sales.services.order_resolution import recover_order_from_human_error

User = get_user_model()


class CS1BHumanErrorRecoveryTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Empresa CS1B",
            slug="empresa-cs1b",
        )
        self.other_company = Company.objects.create(
            name="Otra Empresa CS1B",
            slug="otra-cs1b",
        )
        self.owner = User.objects.create_user(
            "owner-cs1b@test.local",
            password="x",
        )
        UserProfile.objects.create(
            user=self.owner,
            company=self.company,
            role="owner",
        )
        self.customer = Customer.objects.create(
            company=self.company,
            full_name="Cliente CS1B",
        )

    def order(self, **kwargs):
        defaults = {
            "company": self.company,
            "customer": self.customer,
            "created_by": self.owner,
            "total": Decimal("100.00"),
            "amount_paid": Decimal("100.00"),
            "balance_due": Decimal("0.00"),
            "status": "cancelled",
        }
        defaults.update(kwargs)
        return Order.objects.create(**defaults)

    def approved_payment(self, order, *, folio="PAY-CS1B"):
        return Payment.objects.create(
            company=self.company,
            order=order,
            folio=folio,
            payment_method="transfer",
            payment_type="full",
            status="approved",
            amount=Decimal("100.00"),
        )

    def _successful_plan(self):
        warehouse = SimpleNamespace(id=91, company_id=self.company.id)
        fulfillment = SimpleNamespace(id=92, warehouse=warehouse)
        return {
            "groups": [
                {
                    "warehouse": warehouse,
                    "warehouse_id": warehouse.id,
                    "fulfillment": fulfillment,
                }
            ]
        }

    @patch("warehouse.services.generate_picking_order")
    @patch("warehouse.services.fulfillment_planning.plan_order_fulfillments")
    @patch("accounts.warehouse_access.user_can_access_warehouse", return_value=True)
    def test_human_error_recovery_does_not_duplicate_payment(
        self,
        _access,
        plan_mock,
        picking_mock,
    ):
        order = self.order()
        payment = self.approved_payment(order)
        original_order_id = order.id
        original_folio = order.folio
        before_count = Payment.objects.filter(order=order).count()

        plan_mock.return_value = self._successful_plan()
        picking_mock.return_value = SimpleNamespace(id=501)

        decision = recover_order_from_human_error(
            company_id=self.company.id,
            order_id=order.id,
            actor=self.owner,
            reason_text="El operador canceló el pedido por error.",
            idempotency_key="recover-no-duplicate-payment",
        )

        order.refresh_from_db()
        payment.refresh_from_db()

        self.assertTrue(decision.recovered)
        self.assertEqual(order.id, original_order_id)
        self.assertEqual(order.folio, original_folio)
        self.assertEqual(payment.status, "approved")
        self.assertEqual(
            Payment.objects.filter(order=order).count(),
            before_count,
        )
        self.assertEqual(order.amount_paid, Decimal("100.00"))
        self.assertEqual(order.balance_due, Decimal("0.00"))
        self.assertEqual(
            OrderResolutionEvent.objects.filter(
                order=order,
                event_type=OrderResolutionEvent.EVENT_RECOVERED,
            ).count(),
            1,
        )

    @patch("warehouse.services.generate_picking_order")
    @patch("warehouse.services.fulfillment_planning.plan_order_fulfillments")
    @patch("accounts.warehouse_access.user_can_access_warehouse", return_value=True)
    def test_same_recovery_key_does_not_repeat_operational_recovery(
        self,
        _access,
        plan_mock,
        picking_mock,
    ):
        order = self.order()
        self.approved_payment(order)
        plan_mock.return_value = self._successful_plan()
        picking_mock.return_value = SimpleNamespace(id=502)

        kwargs = {
            "company_id": self.company.id,
            "order_id": order.id,
            "actor": self.owner,
            "reason_text": "Error de dedo del operador.",
            "idempotency_key": "same-recovery-key",
        }
        first = recover_order_from_human_error(**kwargs)
        second = recover_order_from_human_error(**kwargs)

        self.assertTrue(first.recovered)
        self.assertTrue(second.recovered)
        self.assertEqual(plan_mock.call_count, 1)
        self.assertEqual(picking_mock.call_count, 1)
        self.assertEqual(
            OrderResolutionEvent.objects.filter(
                idempotency_key="same-recovery-key",
            ).count(),
            1,
        )

    def test_refunded_payment_cannot_be_reused(self):
        order = self.order()
        Payment.objects.create(
            company=self.company,
            order=order,
            folio="PAY-REFUNDED-CS1B",
            payment_method="transfer",
            payment_type="full",
            status="refunded",
            amount=Decimal("100.00"),
        )

        decision = recover_order_from_human_error(
            company_id=self.company.id,
            order_id=order.id,
            actor=self.owner,
            reason_text="Se intentó recuperar después del reembolso.",
            idempotency_key="refunded-cannot-reuse",
        )

        order.refresh_from_db()
        self.assertFalse(decision.recovered)
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(
            decision.event.event_type,
            OrderResolutionEvent.EVENT_RECOVERY_BLOCKED,
        )

    def test_payment_under_review_blocks_recovery(self):
        order = self.order()
        self.approved_payment(order)
        Payment.objects.create(
            company=self.company,
            order=order,
            folio="PAY-REVIEW-CS1B",
            payment_method="transfer",
            payment_type="partial",
            status="under_review",
            amount=Decimal("10.00"),
        )

        decision = recover_order_from_human_error(
            company_id=self.company.id,
            order_id=order.id,
            actor=self.owner,
            reason_text="Existe un segundo pago todavía en revisión.",
            idempotency_key="under-review-block",
        )

        self.assertFalse(decision.recovered)
        self.assertIn("pagos todavía en revisión", decision.message)

    def test_shipped_order_cannot_use_human_error_recovery(self):
        order = self.order(status="shipped")
        self.approved_payment(order)

        decision = recover_order_from_human_error(
            company_id=self.company.id,
            order_id=order.id,
            actor=self.owner,
            reason_text="Intento de recuperar un pedido ya enviado.",
            idempotency_key="shipped-block",
        )

        order.refresh_from_db()
        self.assertFalse(decision.recovered)
        self.assertEqual(order.status, "shipped")
        self.assertEqual(
            decision.event.event_type,
            OrderResolutionEvent.EVENT_RECOVERY_BLOCKED,
        )

    def test_cross_company_recovery_is_forbidden_by_tenant_scope(self):
        order = self.order()
        self.approved_payment(order)

        with self.assertRaises(Order.DoesNotExist):
            recover_order_from_human_error(
                company_id=self.other_company.id,
                order_id=order.id,
                actor=self.owner,
                reason_text="Intento de recuperación desde otra empresa.",
                idempotency_key="cross-company-recovery",
            )
