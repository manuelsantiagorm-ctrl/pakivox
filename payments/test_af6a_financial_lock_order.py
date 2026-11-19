from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase

from companies.models import Company
from crm.models import Customer
from payments.models import Payment, PaymentRefundRequest
from payments.services.financial_locks import (
    lock_order_payment_refund,
    lock_order_then_payment,
)
from sales.models import Order


class AF6ACanonicalFinancialLockTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Empresa AF6A", slug="empresa-af6a")
        self.other = Company.objects.create(name="Otra AF6A", slug="otra-af6a")
        self.customer = Customer.objects.create(
            company=self.company, full_name="Cliente AF6A"
        )
        self.order = Order.objects.create(
            company=self.company, customer=self.customer,
            status="pending_payment", total=Decimal("100.00"),
            balance_due=Decimal("100.00"),
        )
        self.payment = Payment.objects.create(
            company=self.company, order=self.order, folio="PAY-AF6A",
            payment_method="transfer", payment_type="full",
            status="under_review", amount=Decimal("100.00"),
        )
        self.refund = PaymentRefundRequest.objects.create(
            company=self.company, payment=self.payment, order=self.order,
            customer=self.customer, status=PaymentRefundRequest.STATUS_REQUESTED,
            amount=Decimal("100.00"), reason="Prueba AF6A",
        )

    def test_payment_helper_locks_order_before_payment(self):
        trace = []
        original_order_get = Order.objects.select_for_update().get
        original_payment_get = Payment.objects.select_for_update().get

        def order_get(*args, **kwargs):
            trace.append("order")
            return original_order_get(*args, **kwargs)

        def payment_get(*args, **kwargs):
            trace.append("payment")
            return original_payment_get(*args, **kwargs)

        with patch(
            "payments.services.financial_locks.Order.objects.select_for_update"
        ) as order_lock, patch(
            "payments.services.financial_locks.Payment.objects.select_for_update"
        ) as payment_lock:
            order_lock.return_value.get.side_effect = order_get
            payment_lock.return_value.get.side_effect = payment_get
            order, payment = lock_order_then_payment(
                company_id=self.company.id, payment_id=self.payment.id
            )
        self.assertEqual(trace, ["order", "payment"])
        self.assertEqual(order.id, self.order.id)
        self.assertEqual(payment.id, self.payment.id)

    def test_refund_helper_locks_order_payment_refund_in_that_order(self):
        trace = []
        models = [
            ("Order", Order, "order"),
            ("Payment", Payment, "payment"),
            ("PaymentRefundRequest", PaymentRefundRequest, "refund"),
        ]
        patchers = []
        for name, model, label in models:
            original_get = model.objects.select_for_update().get
            patcher = patch(
                f"payments.services.financial_locks.{name}.objects.select_for_update"
            )
            mocked = patcher.start()
            mocked.return_value.get.side_effect = (
                lambda original_get=original_get, label=label, **kwargs:
                (trace.append(label), original_get(**kwargs))[1]
            )
            patchers.append(patcher)
        try:
            order, payment, refund = lock_order_payment_refund(
                company_id=self.company.id, refund_id=self.refund.id
            )
        finally:
            for patcher in reversed(patchers):
                patcher.stop()
        self.assertEqual(trace, ["order", "payment", "refund"])
        self.assertEqual((order.id, payment.id, refund.id), (
            self.order.id, self.payment.id, self.refund.id,
        ))

    def test_cross_company_payment_is_not_disclosed(self):
        with self.assertRaises(Payment.DoesNotExist):
            lock_order_then_payment(
                company_id=self.other.id, payment_id=self.payment.id
            )

    def test_reject_reloads_locked_rows_and_updates_payment(self):
        self.payment.reject(reason="Comprobante inválido")
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.payment.status, "rejected")
        self.assertEqual(self.order.amount_paid, Decimal("0.00"))

    def test_cancel_reloads_locked_rows_and_updates_payment(self):
        self.payment.cancel(reason="Captura duplicada")
        self.payment.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.payment.status, "cancelled")
        self.assertEqual(self.order.amount_paid, Decimal("0.00"))

