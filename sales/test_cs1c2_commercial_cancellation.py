from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase

from accounts.models import UserProfile
from companies.models import Company
from crm.models import Customer
from payments.models import Payment, PaymentRefundRequest
from sales.models import Order, OrderResolutionEvent
from sales.services.order_resolution import cancel_paid_order_commercially

User = get_user_model()


class CS1C2CommercialCancellationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Empresa CS1C2", slug="empresa-cs1c2")
        self.other_company = Company.objects.create(name="Otra CS1C2", slug="otra-cs1c2")
        self.owner = User.objects.create_user("owner-cs1c2@test.local", password="x")
        UserProfile.objects.create(user=self.owner, company=self.company, role="owner")
        self.customer = Customer.objects.create(company=self.company, full_name="Cliente CS1C2")

    def order(self, **kwargs):
        defaults = dict(
            company=self.company,
            customer=self.customer,
            created_by=self.owner,
            total=Decimal("100.00"),
            amount_paid=Decimal("100.00"),
            balance_due=Decimal("0.00"),
            status="paid",
        )
        defaults.update(kwargs)
        return Order.objects.create(**defaults)

    def approved_payment(self, order, folio="PAY-CS1C2", amount=Decimal("100.00")):
        return Payment.objects.create(
            company=self.company, order=order, folio=folio,
            payment_method="transfer", payment_type="full",
            status="approved", amount=amount,
        )

    def empty_state(self):
        return {
            "fulfillments": [], "pickings": [], "packings": [], "shippings": [],
            "active_fulfillments": [], "active_pickings": [], "active_packings": [], "active_shippings": [],
            "active_fulfillment_ids": [], "active_picking_ids": [], "active_packing_ids": [], "active_shipping_ids": [],
            "cancelled_fulfillment_ids": [], "cancelled_picking_ids": [], "cancelled_packing_ids": [], "cancelled_shipping_ids": [],
        }

    def test_paid_order_is_cancelled_but_payment_stays_approved_until_real_refund(self):
        order = self.order()
        payment = self.approved_payment(order)
        with patch("sales.services.order_resolution._warehouse_state", return_value=self.empty_state()):
            decision = cancel_paid_order_commercially(
                company_id=self.company.id, order_id=order.id, actor=self.owner,
                reason_text="El cliente canceló definitivamente la compra.",
                idempotency_key="commercial-no-fake-refund",
            )
        order.refresh_from_db(); payment.refresh_from_db()
        self.assertTrue(decision.cancelled)
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(order.amount_paid, Decimal("100.00"))
        self.assertEqual(order.balance_due, Decimal("0.00"))
        self.assertEqual(payment.status, "approved")
        refund = PaymentRefundRequest.objects.get(payment=payment)
        self.assertEqual(refund.status, PaymentRefundRequest.STATUS_REQUESTED)
        self.assertEqual(refund.amount, Decimal("100.00"))

    def test_same_key_does_not_duplicate_refund_request(self):
        order = self.order(); self.approved_payment(order)
        kwargs = dict(
            company_id=self.company.id, order_id=order.id, actor=self.owner,
            reason_text="Cancelación comercial confirmada por el cliente.",
            idempotency_key="same-commercial-key",
        )
        with patch("sales.services.order_resolution._warehouse_state", return_value=self.empty_state()):
            first = cancel_paid_order_commercially(**kwargs)
            second = cancel_paid_order_commercially(**kwargs)
        self.assertTrue(first.cancelled)
        self.assertTrue(second.cancelled)
        self.assertEqual(PaymentRefundRequest.objects.filter(order=order).count(), 1)
        self.assertEqual(OrderResolutionEvent.objects.filter(idempotency_key="same-commercial-key").count(), 1)

    def test_pending_or_review_payment_blocks_commercial_cancel(self):
        order = self.order(); self.approved_payment(order)
        Payment.objects.create(
            company=self.company, order=order, folio="PAY-PENDING-CS1C2",
            payment_method="transfer", payment_type="deposit",
            status="under_review", amount=Decimal("10.00"),
        )
        decision = cancel_paid_order_commercially(
            company_id=self.company.id, order_id=order.id, actor=self.owner,
            reason_text="Cliente solicita cancelación comercial.",
            idempotency_key="commercial-review-block",
        )
        order.refresh_from_db()
        self.assertFalse(decision.cancelled)
        self.assertEqual(order.status, "paid")
        self.assertEqual(PaymentRefundRequest.objects.filter(order=order).count(), 0)

    def test_shipped_order_is_never_commercially_cancelled(self):
        order = self.order(status="shipped"); self.approved_payment(order)
        decision = cancel_paid_order_commercially(
            company_id=self.company.id, order_id=order.id, actor=self.owner,
            reason_text="Cliente solicita cancelación después del envío.",
            idempotency_key="commercial-shipped-block",
        )
        order.refresh_from_db()
        self.assertFalse(decision.cancelled)
        self.assertEqual(order.status, "shipped")
        self.assertEqual(PaymentRefundRequest.objects.filter(order=order).count(), 0)

    @patch("accounts.warehouse_access.user_can_access_warehouse", return_value=True)
    @patch("warehouse.services.cancelar_flujo_warehouse")
    def test_active_fulfillment_is_cancelled_once_before_refund_request(self, cancel_mock, access_mock):
        order = self.order(); self.approved_payment(order)
        wh = SimpleNamespace(id=9, company_id=self.company.id)
        fulfillment = SimpleNamespace(id=11, warehouse_id=9, warehouse=wh, status="reserved", STATUS_SHIPPED="shipped", STATUS_DELIVERED="delivered")
        active = self.empty_state(); active["active_fulfillments"]=[fulfillment]; active["active_fulfillment_ids"]=[11]
        with patch("sales.services.order_resolution._warehouse_state", side_effect=[active, self.empty_state()]):
            decision = cancel_paid_order_commercially(
                company_id=self.company.id, order_id=order.id, actor=self.owner,
                reason_text="Cliente canceló antes de preparar el envío.",
                idempotency_key="commercial-wh-cancel",
            )
        self.assertTrue(decision.cancelled)
        cancel_mock.assert_called_once()
        access_mock.assert_called_once()
        self.assertEqual(PaymentRefundRequest.objects.filter(order=order).count(), 1)

    @patch("accounts.warehouse_access.user_can_access_warehouse", return_value=False)
    def test_missing_warehouse_operate_permission_rolls_back(self, _access):
        order = self.order(); payment=self.approved_payment(order)
        wh = SimpleNamespace(id=19, company_id=self.company.id)
        fulfillment = SimpleNamespace(id=21, warehouse_id=19, warehouse=wh, status="reserved", STATUS_SHIPPED="shipped", STATUS_DELIVERED="delivered")
        active = self.empty_state(); active["active_fulfillments"]=[fulfillment]; active["active_fulfillment_ids"]=[21]
        with patch("sales.services.order_resolution._warehouse_state", return_value=active):
            with self.assertRaises(PermissionDenied):
                cancel_paid_order_commercially(
                    company_id=self.company.id, order_id=order.id, actor=self.owner,
                    reason_text="Cancelación comercial sin permiso de bodega.",
                    idempotency_key="commercial-no-wh-access",
                )
        order.refresh_from_db(); payment.refresh_from_db()
        self.assertEqual(order.status, "paid")
        self.assertEqual(payment.status, "approved")
        self.assertEqual(PaymentRefundRequest.objects.filter(order=order).count(), 0)

    def test_cross_company_is_forbidden(self):
        order = self.order(); self.approved_payment(order)
        with self.assertRaises(PermissionDenied):
            cancel_paid_order_commercially(
                company_id=self.other_company.id, order_id=order.id, actor=self.owner,
                reason_text="Intento desde una empresa distinta.",
                idempotency_key="commercial-cross-company",
            )
