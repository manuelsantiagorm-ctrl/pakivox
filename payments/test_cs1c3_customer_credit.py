from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from accounts.models import Capability, EmployeeCapabilityAssignment, UserProfile
from companies.models import Company
from crm.models import Customer
from payments.models import CustomerCredit, Payment, PaymentRefundRequest
from payments.services.customer_credit import convert_refund_to_customer_credit
from payments.services.refunds import request_full_refund
from sales.models import Order


class CustomerCreditCS1C3Tests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Empresa CS1C3")
        self.other_company = Company.objects.create(name="Otra Empresa CS1C3")
        self.customer = Customer.objects.create(company=self.company, full_name="Cliente Uno")
        self.other_customer = Customer.objects.create(company=self.other_company, full_name="Cliente Dos")
        User = get_user_model()
        self.user = User.objects.create_user(username="cs1c3", password="x")
        self.other_user = User.objects.create_user(username="cs1c3-other", password="x")
        UserProfile.objects.create(
            user=self.user,
            company=self.company,
            role="manager",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )
        UserProfile.objects.create(
            user=self.other_user,
            company=self.other_company,
            role="manager",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )
        cap, _ = Capability.objects.get_or_create(
            code="payments.customer_credit.manage",
            defaults={"name":"Saldo a favor", "area_code":"collections_payments", "is_active":True},
        )
        EmployeeCapabilityAssignment.objects.create(
            company=self.company, user=self.user, capability=cap, is_active=True
        )
        self.order = Order.objects.create(
            company=self.company, customer=self.customer, status="cancelled",
            total=Decimal("1000.00"), amount_paid=Decimal("1000.00"), balance_due=Decimal("0.00")
        )
        self.payment = Payment.objects.create(
            company=self.company, order=self.order, folio="PAY-CS1C3-1",
            payment_method="transfer", payment_type="full", status="approved",
            amount=Decimal("1000.00")
        )
        self.refund = PaymentRefundRequest.objects.create(
            company=self.company, payment=self.payment, order=self.order,
            customer=self.customer, requested_by=self.user, status="requested",
            amount=Decimal("1000.00"), reason="Cliente conservará saldo"
        )

    def issue(self, **overrides):
        data=dict(company_id=self.company.id, refund_id=self.refund.id, actor=self.user,
                  reason="Acuerdo de saldo a favor", idempotency_key="credit-key-1")
        data.update(overrides)
        return convert_refund_to_customer_credit(**data)

    def test_credit_preserves_original_approved_payment(self):
        decision=self.issue()
        self.payment.refresh_from_db(); self.refund.refresh_from_db()
        self.assertTrue(decision.created)
        self.assertEqual(Payment.objects.filter(pk=self.payment.pk).count(), 1)
        self.assertEqual(self.payment.status, "approved")
        self.assertEqual(decision.credit.amount, Decimal("1000.00"))
        self.assertEqual(decision.credit.remaining_amount, Decimal("1000.00"))
        self.assertEqual(self.refund.status, "converted_to_credit")

    def test_same_idempotency_key_does_not_duplicate_credit(self):
        first=self.issue(); second=self.issue()
        self.assertEqual(first.credit.id, second.credit.id)
        self.assertFalse(second.created)
        self.assertEqual(CustomerCredit.objects.count(), 1)

    def test_same_refund_cannot_create_second_credit_with_new_key(self):
        first=self.issue()
        second=self.issue(idempotency_key="another-key")
        self.assertEqual(first.credit.id, second.credit.id)
        self.assertEqual(CustomerCredit.objects.count(), 1)

    def test_cross_company_actor_is_forbidden(self):
        with self.assertRaises(PermissionDenied):
            self.issue(actor=self.other_user)
        self.assertFalse(CustomerCredit.objects.exists())

    def test_refunded_payment_cannot_be_converted(self):
        self.payment.status="refunded"; self.payment.save(update_fields=["status"])
        with self.assertRaises(ValidationError):
            self.issue()
        self.assertFalse(CustomerCredit.objects.exists())

    def test_non_cancelled_order_is_blocked(self):
        self.order.status="paid"; self.order.save(update_fields=["status"])
        with self.assertRaises(ValidationError):
            self.issue()
        self.assertFalse(CustomerCredit.objects.exists())

    def test_new_refund_request_is_blocked_after_credit_issue(self):
        self.issue()
        with self.assertRaises(ValidationError):
            request_full_refund(
                company_id=self.company.id, payment_id=self.payment.id, actor=self.user,
                reason="Intento duplicado", idempotency_key="new-refund-after-credit"
            )
