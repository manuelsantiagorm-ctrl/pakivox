from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounts.models import UserProfile
from companies.models import Company
from crm.models import Customer
from payments.models import Payment
from sales.models import Order, OrderResolutionEvent
from sales.services.order_resolution import cancel_order_safely

User = get_user_model()


class CS1ASafeCancellationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Empresa CS1", slug="empresa-cs1")
        self.other_company = Company.objects.create(name="Otra empresa", slug="otra-cs1")
        self.owner = User.objects.create_user("owner-cs1@test.local", password="x")
        UserProfile.objects.create(user=self.owner, company=self.company, role="owner")
        self.customer = Customer.objects.create(company=self.company, full_name="Cliente CS1")

    def order(self, **kwargs):
        defaults = dict(company=self.company, customer=self.customer, created_by=self.owner, total=Decimal("100.00"), balance_due=Decimal("100.00"))
        defaults.update(kwargs)
        return Order.objects.create(**defaults)

    def test_unpaid_order_can_be_cancelled_safely(self):
        order = self.order(status="pending_payment")
        result = cancel_order_safely(company_id=self.company.id, order_id=order.id, actor=self.owner, reason_code="customer_cancelled", reason_text="Cliente canceló la compra", idempotency_key="cs1-unpaid")
        order.refresh_from_db()
        self.assertTrue(result.cancelled)
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(OrderResolutionEvent.objects.filter(order=order, event_type="cancelled").count(), 1)

    def test_approved_payment_blocks_simple_cancel_and_is_not_duplicated(self):
        order = self.order(status="paid", amount_paid=Decimal("100.00"), balance_due=Decimal("0.00"))
        payment = Payment.objects.create(company=self.company, order=order, folio="PAY-CS1", payment_method="transfer", payment_type="full", status="approved", amount=Decimal("100.00"))
        before = Payment.objects.filter(order=order).count()
        result = cancel_order_safely(company_id=self.company.id, order_id=order.id, actor=self.owner, reason_code="human_error", reason_text="Error de captura del operador", idempotency_key="cs1-paid")
        order.refresh_from_db(); payment.refresh_from_db()
        self.assertFalse(result.cancelled)
        self.assertTrue(result.requires_resolution)
        self.assertEqual(order.status, "paid")
        self.assertEqual(payment.status, "approved")
        self.assertEqual(Payment.objects.filter(order=order).count(), before)

    def test_same_idempotency_key_does_not_duplicate_event(self):
        order = self.order(status="pending_payment")
        kwargs=dict(company_id=self.company.id, order_id=order.id, actor=self.owner, reason_code="customer_cancelled", reason_text="Cliente canceló la compra", idempotency_key="same-key")
        cancel_order_safely(**kwargs); cancel_order_safely(**kwargs)
        self.assertEqual(OrderResolutionEvent.objects.filter(idempotency_key="same-key").count(), 1)

    def test_cross_company_order_is_not_resolved(self):
        order = self.order(status="pending_payment")
        with self.assertRaises(Order.DoesNotExist):
            cancel_order_safely(company_id=self.other_company.id, order_id=order.id, actor=self.owner, reason_code="other", reason_text="Intento empresa incorrecta", idempotency_key="cross")

    def test_shipped_order_is_not_cancelled(self):
        order = self.order(status="shipped", amount_paid=Decimal("100.00"), balance_due=Decimal("0.00"))
        result = cancel_order_safely(company_id=self.company.id, order_id=order.id, actor=self.owner, reason_code="other", reason_text="Intento de cancelación tardía", idempotency_key="shipped")
        order.refresh_from_db()
        self.assertFalse(result.cancelled)
        self.assertTrue(result.requires_resolution)
        self.assertEqual(order.status, "shipped")
