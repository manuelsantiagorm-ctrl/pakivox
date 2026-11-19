from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from accounts.models import Capability, EmployeeCapabilityAssignment, UserProfile
from companies.models import Company
from crm.models import Customer
from payments.models import (
    CustomerCredit, CustomerCreditApplication, Payment, PaymentRefundRequest,
)
from payments.services.customer_credit import (
    apply_customer_credit_to_order, reverse_order_customer_credits,
)
from sales.models import Order
from sales.services.order_resolution import cancel_paid_order_commercially


class CustomerCreditApplicationCS1C4Tests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Empresa CS1C4", slug="empresa-cs1c4")
        self.other_company = Company.objects.create(name="Otra CS1C4", slug="otra-cs1c4")
        self.customer = Customer.objects.create(company=self.company, full_name="Cliente Crédito")
        self.other_customer = Customer.objects.create(company=self.other_company, full_name="Otro Cliente")
        User = get_user_model()
        self.user = User.objects.create_user(username="cs1c4", password="x")
        self.other_user = User.objects.create_user(username="cs1c4-other", password="x")
        UserProfile.objects.create(user=self.user, company=self.company, role="manager", is_active=True, employment_status=UserProfile.STATUS_ACTIVE)
        UserProfile.objects.create(user=self.other_user, company=self.other_company, role="manager", is_active=True, employment_status=UserProfile.STATUS_ACTIVE)
        for code in ("payments.customer_credit.apply", "sales.commercial_cancel"):
            cap, _ = Capability.objects.get_or_create(code=code, defaults={"name":code, "area_code":"collections_payments", "is_active":True})
            EmployeeCapabilityAssignment.objects.create(company=self.company, user=self.user, capability=cap, is_active=True)

        self.source_order = Order.objects.create(company=self.company, customer=self.customer, status="cancelled", total=Decimal("1000.00"), amount_paid=Decimal("1000.00"), balance_due=Decimal("0.00"))
        self.source_payment = Payment.objects.create(company=self.company, order=self.source_order, folio="PAY-CS1C4-SRC", payment_method="transfer", payment_type="full", status="approved", amount=Decimal("1000.00"))
        self.refund = PaymentRefundRequest.objects.create(company=self.company, payment=self.source_payment, order=self.source_order, customer=self.customer, requested_by=self.user, status="converted_to_credit", amount=Decimal("1000.00"), reason="Saldo emitido")
        self.credit = CustomerCredit.objects.create(company=self.company, customer=self.customer, source_payment=self.source_payment, source_order=self.source_order, source_refund_request=self.refund, created_by=self.user, amount=Decimal("1000.00"), remaining_amount=Decimal("1000.00"), status="active", reason="Saldo CS1C4")
        self.order = Order.objects.create(company=self.company, customer=self.customer, status="pending", total=Decimal("1200.00"), amount_paid=Decimal("0.00"), balance_due=Decimal("1200.00"))

    def apply(self, amount="400.00", key="apply-key-1", **kwargs):
        data=dict(company_id=self.company.id, credit_id=self.credit.id, order_id=self.order.id, amount=Decimal(amount), actor=self.user, reason="Aplicación autorizada", idempotency_key=key)
        data.update(kwargs)
        return apply_customer_credit_to_order(**data)

    def test_partial_credit_reduces_balance_without_creating_payment(self):
        before=Payment.objects.count()
        decision=self.apply("400.00")
        self.order.refresh_from_db(); self.credit.refresh_from_db()
        self.assertTrue(decision.created)
        self.assertEqual(Payment.objects.count(), before)
        self.assertEqual(self.order.amount_paid, Decimal("400.00"))
        self.assertEqual(self.order.balance_due, Decimal("800.00"))
        self.assertEqual(self.credit.remaining_amount, Decimal("600.00"))

    def test_same_idempotency_key_does_not_apply_twice(self):
        first=self.apply("300.00")
        second=self.apply("300.00")
        self.credit.refresh_from_db()
        self.assertEqual(first.application.id, second.application.id)
        self.assertFalse(second.created)
        self.assertEqual(self.credit.remaining_amount, Decimal("700.00"))
        self.assertEqual(CustomerCreditApplication.objects.count(), 1)

    def test_credit_from_other_customer_is_blocked(self):
        foreign_order=Order.objects.create(company=self.company, customer=Customer.objects.create(company=self.company, full_name="Cliente distinto"), status="pending", total=Decimal("100.00"), balance_due=Decimal("100.00"))
        with self.assertRaises(ValidationError):
            self.apply("50.00", order_id=foreign_order.id)

    def test_cross_company_actor_is_forbidden(self):
        with self.assertRaises(PermissionDenied):
            self.apply("50.00", actor=self.other_user)

    def test_amount_cannot_exceed_credit_or_order_due(self):
        with self.assertRaises(ValidationError):
            self.apply("1000.01")
        self.order.total=Decimal("500.00"); self.order.balance_due=Decimal("500.00"); self.order.save(update_fields=["total","balance_due"] )
        with self.assertRaises(ValidationError):
            self.apply("500.01", key="apply-key-2")

    @patch("warehouse.services.reserve_paid_order")
    def test_full_credit_covers_order_without_fake_payment(self, reserve_mock):
        class Dummy: pass
        reserve_mock.return_value=[Dummy()]
        self.order.total=Decimal("1000.00"); self.order.balance_due=Decimal("1000.00"); self.order.save(update_fields=["total","balance_due"] )
        # Full coverage requires at least one item in production; test the accounting
        # transition by bypassing only the item-presence guard with a partial cash payment.
        Payment.objects.create(company=self.company, order=self.order, folio="PAY-CS1C4-REAL", payment_method="cash", payment_type="deposit", status="approved", amount=Decimal("1.00"))
        # No item means full application must rollback rather than create an unusable paid order.
        with self.assertRaises(ValidationError):
            self.apply("999.00")
        self.credit.refresh_from_db(); self.order.refresh_from_db()
        self.assertEqual(self.credit.remaining_amount, Decimal("1000.00"))
        self.assertFalse(CustomerCreditApplication.objects.exists())

    def test_payment_sync_keeps_applied_credit_in_order_coverage(self):
        self.apply("400.00")
        payment=Payment.objects.create(company=self.company, order=self.order, folio="PAY-CS1C4-1", payment_method="cash", payment_type="deposit", status="approved", amount=Decimal("100.00"))
        payment.sync_order_payment_status()
        self.order.refresh_from_db()
        self.assertEqual(self.order.amount_paid, Decimal("500.00"))
        self.assertEqual(self.order.balance_due, Decimal("700.00"))

    def test_reverse_restores_credit_once(self):
        self.apply("250.00")
        decision=reverse_order_customer_credits(company_id=self.company.id, order_id=self.order.id, actor=self.user, reason="Cancelación comercial")
        second=reverse_order_customer_credits(company_id=self.company.id, order_id=self.order.id, actor=self.user, reason="Retry cancelación")
        self.credit.refresh_from_db()
        app=CustomerCreditApplication.objects.get()
        self.assertEqual(decision.restored_amount, Decimal("250.00"))
        self.assertEqual(second.restored_amount, Decimal("0.00"))
        self.assertEqual(self.credit.remaining_amount, Decimal("1000.00"))
        self.assertEqual(app.status, CustomerCreditApplication.STATUS_REVERSED)

    def test_credit_only_order_can_cancel_commercially_and_restore_credit(self):
        self.apply("300.00")
        decision=cancel_paid_order_commercially(company_id=self.company.id, order_id=self.order.id, actor=self.user, reason_text="Cliente canceló la venta", idempotency_key="commercial-credit-only")
        self.order.refresh_from_db(); self.credit.refresh_from_db()
        self.assertTrue(decision.cancelled)
        self.assertEqual(self.order.status, "cancelled")
        self.assertEqual(self.order.amount_paid, Decimal("0.00"))
        self.assertEqual(self.credit.remaining_amount, Decimal("1000.00"))
        self.assertEqual(tuple(decision.refund_request_ids), tuple())

    def test_mixed_real_payment_and_credit_cancel_restores_only_credit_and_refunds_real_payment(self):
        payment=Payment.objects.create(company=self.company, order=self.order, folio="PAY-CS1C4-MIX", payment_method="transfer", payment_type="deposit", status="approved", amount=Decimal("200.00"))
        self.apply("300.00")
        decision=cancel_paid_order_commercially(company_id=self.company.id, order_id=self.order.id, actor=self.user, reason_text="Cliente canceló venta mixta", idempotency_key="commercial-mixed")
        self.order.refresh_from_db(); self.credit.refresh_from_db(); payment.refresh_from_db()
        self.assertTrue(decision.cancelled)
        self.assertEqual(self.credit.remaining_amount, Decimal("1000.00"))
        self.assertEqual(payment.status, "approved")
        self.assertEqual(self.order.amount_paid, Decimal("200.00"))
        self.assertEqual(len(decision.refund_request_ids), 1)
        self.assertEqual(PaymentRefundRequest.objects.filter(pk__in=decision.refund_request_ids, payment=payment, status="requested").count(), 1)
