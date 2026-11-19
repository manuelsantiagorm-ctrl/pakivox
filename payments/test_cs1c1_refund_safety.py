from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from companies.models import Company
from crm.models import Customer
from payments.models import Payment, PaymentRefundRequest
from payments.services.refunds import request_full_refund
from sales.models import Order


class CS1C1RefundSafetyTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="CS1C1 Company")
        self.customer = Customer.objects.create(company=self.company, full_name="Cliente Refund")
        User = get_user_model()
        self.user = User.objects.create_user(username="cs1c1", password="x")
        self.order = Order.objects.create(
            company=self.company, customer=self.customer, created_by=self.user,
            folio="CS1C1-001", total=Decimal("1000.00"),
            amount_paid=Decimal("1000.00"), balance_due=Decimal("0.00"), status="cancelled",
        )
        self.payment = Payment.objects.create(
            company=self.company, order=self.order, registered_by=self.user,
            folio="PAY-CS1C1-001", payment_method="manual", payment_type="full",
            status="approved", amount=Decimal("1000.00"),
        )

    def test_request_does_not_mark_money_refunded(self):
        decision = request_full_refund(
            company_id=self.company.id, payment_id=self.payment.id, actor=self.user,
            reason="Venta cancelada por cliente", idempotency_key="request-1",
        )
        self.payment.refresh_from_db()
        self.assertTrue(decision.created_or_confirmed)
        self.assertEqual(self.payment.status, "approved")
        self.assertEqual(decision.refund.status, PaymentRefundRequest.STATUS_REQUESTED)

    def test_request_is_idempotent(self):
        first = request_full_refund(
            company_id=self.company.id, payment_id=self.payment.id, actor=self.user,
            reason="Venta cancelada por cliente", idempotency_key="request-2",
        )
        second = request_full_refund(
            company_id=self.company.id, payment_id=self.payment.id, actor=self.user,
            reason="Venta cancelada por cliente", idempotency_key="request-2",
        )
        self.assertEqual(first.refund.id, second.refund.id)
        self.assertEqual(PaymentRefundRequest.objects.count(), 1)

    def test_direct_refund_is_disabled(self):
        with self.assertRaises(ValidationError):
            self.payment.refund(user=self.user, reason="No directo")
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "approved")

    def test_cross_company_request_is_rejected_by_scope(self):
        other = Company.objects.create(name="Other")
        with self.assertRaises(Payment.DoesNotExist):
            request_full_refund(
                company_id=other.id, payment_id=self.payment.id, actor=self.user,
                reason="Intento cruzado", idempotency_key="request-cross",
            )
