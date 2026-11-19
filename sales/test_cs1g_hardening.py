from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from accounts.models import (
    Capability,
    EmployeeCapabilityAssignment,
    UserProfile,
)
from companies.models import Company
from crm.models import Customer
from payments.models import (
    CustomerCredit,
    CustomerCreditApplication,
    Payment,
    PaymentRefundRequest,
)
from sales.models import Order, OrderResolutionEvent
from sales.services.order_duplicates import detect_related_orders
from sales.services.order_resolution import recover_order_from_human_error

User = get_user_model()


class CS1GFinalHardeningTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Empresa CS1G", slug="empresa-cs1g"
        )
        self.other_company = Company.objects.create(
            name="Otra CS1G", slug="otra-cs1g"
        )
        self.customer = Customer.objects.create(
            company=self.company, full_name="Cliente CS1G"
        )
        self.owner = User.objects.create_user("owner-cs1g@test.local", password="x")
        self.other_owner = User.objects.create_user(
            "owner-other-cs1g@test.local", password="x"
        )
        UserProfile.objects.create(
            user=self.owner, company=self.company, role="owner"
        )
        UserProfile.objects.create(
            user=self.other_owner, company=self.other_company, role="owner"
        )

    def order(self, *, total="100.00", status="cancelled"):
        amount = Decimal(total)
        return Order.objects.create(
            company=self.company,
            customer=self.customer,
            created_by=self.owner,
            total=amount,
            amount_paid=amount,
            balance_due=Decimal("0.00"),
            status=status,
        )

    def _plan(self):
        warehouse = SimpleNamespace(id=991, company_id=self.company.id)
        fulfillment = SimpleNamespace(id=992, warehouse=warehouse)
        return {
            "groups": [
                {
                    "warehouse": warehouse,
                    "warehouse_id": warehouse.id,
                    "fulfillment": fulfillment,
                }
            ]
        }

    def _make_credit_application(self, order, amount="100.00", status="applied"):
        amount = Decimal(amount)
        source_order = Order.objects.create(
            company=self.company,
            customer=self.customer,
            created_by=self.owner,
            total=amount,
            amount_paid=amount,
            balance_due=Decimal("0.00"),
            status="cancelled",
        )
        source_payment = Payment.objects.create(
            company=self.company,
            order=source_order,
            folio=f"PAY-CS1G-SRC-{source_order.id}",
            payment_method="transfer",
            payment_type="full",
            status="approved",
            amount=amount,
        )
        refund = PaymentRefundRequest.objects.create(
            company=self.company,
            payment=source_payment,
            order=source_order,
            customer=self.customer,
            requested_by=self.owner,
            status=PaymentRefundRequest.STATUS_CONVERTED_TO_CREDIT,
            amount=amount,
            reason="Saldo previamente autorizado",
        )
        credit = CustomerCredit.objects.create(
            company=self.company,
            customer=self.customer,
            source_payment=source_payment,
            source_order=source_order,
            source_refund_request=refund,
            created_by=self.owner,
            amount=amount,
            remaining_amount=Decimal("0.00") if status == "applied" else amount,
            status=(
                CustomerCredit.STATUS_EXHAUSTED
                if status == "applied"
                else CustomerCredit.STATUS_ACTIVE
            ),
            reason="Crédito CS1G",
        )
        return CustomerCreditApplication.objects.create(
            company=self.company,
            customer=self.customer,
            credit=credit,
            order=order,
            applied_by=self.owner,
            amount=amount,
            status=status,
            reason="Aplicación original CS1G",
            idempotency_key=f"cs1g-app-{order.id}-{credit.id}",
        )

    @patch("warehouse.services.generate_picking_order")
    @patch("warehouse.services.fulfillment_planning.plan_order_fulfillments")
    @patch("accounts.warehouse_access.user_can_access_warehouse", return_value=True)
    def test_credit_only_cancelled_order_recovers_without_fake_payment_or_second_credit(
        self, _access, plan_mock, picking_mock
    ):
        order = self.order(total="100.00")
        application = self._make_credit_application(order, "100.00")
        before_payments = Payment.objects.filter(order=order).count()
        before_apps = CustomerCreditApplication.objects.filter(order=order).count()

        plan_mock.return_value = self._plan()
        picking_mock.return_value = SimpleNamespace(id=993)

        decision = recover_order_from_human_error(
            company_id=self.company.id,
            order_id=order.id,
            actor=self.owner,
            reason_text="Cancelación accidental del operador.",
            idempotency_key="cs1g-credit-only-recover",
        )

        order.refresh_from_db()
        application.refresh_from_db()
        self.assertTrue(decision.recovered)
        self.assertEqual(order.amount_paid, Decimal("100.00"))
        self.assertEqual(order.balance_due, Decimal("0.00"))
        self.assertEqual(Payment.objects.filter(order=order).count(), before_payments)
        self.assertEqual(
            CustomerCreditApplication.objects.filter(order=order).count(), before_apps
        )
        self.assertEqual(
            application.status, CustomerCreditApplication.STATUS_APPLIED
        )

    @patch("warehouse.services.generate_picking_order")
    @patch("warehouse.services.fulfillment_planning.plan_order_fulfillments")
    @patch("accounts.warehouse_access.user_can_access_warehouse", return_value=True)
    def test_mixed_payment_and_credit_recovery_uses_combined_coverage(
        self, _access, plan_mock, picking_mock
    ):
        order = self.order(total="100.00")
        Payment.objects.create(
            company=self.company,
            order=order,
            folio="PAY-CS1G-MIX",
            payment_method="cash",
            payment_type="deposit",
            status="approved",
            amount=Decimal("40.00"),
        )
        self._make_credit_application(order, "60.00")
        plan_mock.return_value = self._plan()
        picking_mock.return_value = SimpleNamespace(id=994)

        result = recover_order_from_human_error(
            company_id=self.company.id,
            order_id=order.id,
            actor=self.owner,
            reason_text="Se canceló por error una venta mixta.",
            idempotency_key="cs1g-mixed-recover",
        )
        order.refresh_from_db()
        self.assertTrue(result.recovered)
        self.assertEqual(order.amount_paid, Decimal("100.00"))
        self.assertEqual(order.balance_due, Decimal("0.00"))

    def test_reversed_credit_application_is_not_reusable_for_recovery(self):
        order = self.order(total="100.00")
        self._make_credit_application(
            order, "100.00", status=CustomerCreditApplication.STATUS_REVERSED
        )
        result = recover_order_from_human_error(
            company_id=self.company.id,
            order_id=order.id,
            actor=self.owner,
            reason_text="Intento con crédito ya revertido.",
            idempotency_key="cs1g-reversed-credit-block",
        )
        self.assertFalse(result.recovered)
        self.assertIn("cobertura financiera suficiente", result.message)

    def test_recovery_requires_idempotency_key(self):
        order = self.order()
        Payment.objects.create(
            company=self.company,
            order=order,
            folio="PAY-CS1G-IDEMP",
            payment_method="transfer",
            payment_type="full",
            status="approved",
            amount=Decimal("100.00"),
        )
        with self.assertRaises(ValidationError):
            recover_order_from_human_error(
                company_id=self.company.id,
                order_id=order.id,
                actor=self.owner,
                reason_text="Reintento sin llave segura.",
                idempotency_key="",
            )

    def test_wrong_company_scope_hides_order_existence(self):
        order = self.order()
        with self.assertRaises(Order.DoesNotExist):
            recover_order_from_human_error(
                company_id=self.other_company.id,
                order_id=order.id,
                actor=self.owner,
                reason_text="Intento desde un tenant incorrecto.",
                idempotency_key="cs1g-cross-company",
            )

    def test_actor_from_other_company_is_rejected_after_valid_tenant_scope(self):
        order = self.order()
        with self.assertRaises(PermissionDenied):
            recover_order_from_human_error(
                company_id=self.company.id,
                order_id=order.id,
                actor=self.other_owner,
                reason_text="Actor de otra empresa.",
                idempotency_key="cs1g-cross-company-actor",
            )

    def test_non_owner_without_recovery_capability_is_rejected_inside_service(self):
        manager = User.objects.create_user("manager-cs1g@test.local", password="x")
        UserProfile.objects.create(
            user=manager,
            company=self.company,
            role="manager",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )
        order = self.order()
        with self.assertRaises(PermissionDenied):
            recover_order_from_human_error(
                company_id=self.company.id,
                order_id=order.id,
                actor=manager,
                reason_text="Intento sin capacidad interna.",
                idempotency_key="cs1g-no-capability",
            )

    def test_duplicate_detector_marks_credit_only_cancelled_order_as_recoverable(self):
        order = self.order(total="100.00")
        self._make_credit_application(order, "100.00")
        detection = detect_related_orders(
            company=self.company,
            customer=self.customer,
        )
        row = next(item for item in detection.cancelled_recent_orders if item["id"] == order.id)
        self.assertTrue(row["recovery_candidate"])
        self.assertEqual(row["approved_payment_total"], Decimal("0.00"))
        self.assertEqual(row["customer_credit_total"], Decimal("100.00"))
        self.assertEqual(row["covered_total"], Decimal("100.00"))
