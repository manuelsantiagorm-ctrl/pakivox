from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company
from crm.models import Customer
from payments.models import Payment
from sales.models import Order, OrderResolutionEvent
from sales.services.order_duplicates import (
    detect_related_orders,
    record_duplicate_override,
    validate_new_order_duplicate_decision,
)

User = get_user_model()


class CS1DDuplicateOrderDetectorTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Empresa CS1D", slug="empresa-cs1d")
        self.other_company = Company.objects.create(name="Otra CS1D", slug="otra-cs1d")
        self.owner = User.objects.create_user("owner-cs1d@test.local", password="x")
        UserProfile.objects.create(user=self.owner, company=self.company, role="owner")
        self.customer = Customer.objects.create(
            company=self.company, full_name="Cliente CS1D"
        )
        self.other_customer = Customer.objects.create(
            company=self.other_company, full_name="Otro Cliente"
        )

    def order(self, **kwargs):
        defaults = dict(
            company=self.company,
            customer=self.customer,
            created_by=self.owner,
            total=Decimal("100.00"),
            amount_paid=Decimal("0.00"),
            balance_due=Decimal("100.00"),
            status="draft",
        )
        defaults.update(kwargs)
        return Order.objects.create(**defaults)

    @override_settings(PAKIVOX_DUPLICATE_ORDER_CANCELLED_HOURS=72)
    def test_active_order_expires_after_cancelled_window(self):
        order = self.order(status="draft")
        Order.objects.filter(pk=order.pk).update(
            created_at=timezone.now() - timedelta(days=180)
        )
        detection = detect_related_orders(company=self.company, customer=self.customer)
        self.assertFalse(detection.has_candidates)

    def test_recent_active_order_is_detected(self):
        order = self.order(status="draft")
        detection = detect_related_orders(company=self.company, customer=self.customer)
        self.assertEqual(detection.candidate_ids, (order.id,))
        self.assertEqual(len(detection.active_orders), 1)

    @override_settings(PAKIVOX_DUPLICATE_ORDER_CANCELLED_HOURS=72)
    def test_recent_cancelled_order_is_detected(self):
        order = self.order(status="cancelled")
        detection = detect_related_orders(company=self.company, customer=self.customer)
        self.assertIn(order.id, detection.candidate_ids)
        self.assertEqual(len(detection.cancelled_recent_orders), 1)

    @override_settings(PAKIVOX_DUPLICATE_ORDER_CANCELLED_HOURS=72)
    def test_old_cancelled_order_expires_from_warning(self):
        order = self.order(status="cancelled")
        Order.objects.filter(pk=order.pk).update(
            created_at=timezone.now() - timedelta(hours=73)
        )
        detection = detect_related_orders(company=self.company, customer=self.customer)
        self.assertFalse(detection.has_candidates)

    def test_other_company_never_appears(self):
        Order.objects.create(
            company=self.other_company,
            customer=self.other_customer,
            created_by=None,
            total=Decimal("500.00"),
            balance_due=Decimal("500.00"),
            status="paid",
        )
        detection = detect_related_orders(company=self.company, customer=self.customer)
        self.assertFalse(detection.has_candidates)

    def test_customer_from_other_company_is_rejected(self):
        with self.assertRaises(ValidationError):
            detect_related_orders(company=self.company, customer=self.other_customer)

    def test_creation_requires_explicit_override_when_candidate_exists(self):
        self.order(status="draft")
        with self.assertRaises(ValidationError):
            validate_new_order_duplicate_decision(
                company=self.company,
                customer=self.customer,
                override=False,
            )

    def test_legitimate_second_order_is_allowed_with_override(self):
        old = self.order(status="draft")
        detection = validate_new_order_duplicate_decision(
            company=self.company,
            customer=self.customer,
            override=True,
        )
        new = self.order(status="draft")
        event = record_duplicate_override(
            order=new, actor=self.owner, detection=detection
        )
        self.assertIsNotNone(event)
        self.assertEqual(
            event.event_type, OrderResolutionEvent.EVENT_DUPLICATE_WARNING_OVERRIDDEN
        )
        self.assertEqual(event.metadata["candidate_order_ids"], [old.id])
        self.assertEqual(
            Order.objects.filter(company=self.company, customer=self.customer).count(),
            2,
        )

    def test_cancelled_paid_order_is_marked_as_recovery_candidate(self):
        order = self.order(
            status="cancelled",
            amount_paid=Decimal("100.00"),
            balance_due=Decimal("0.00"),
        )
        Payment.objects.create(
            company=self.company,
            order=order,
            folio="PAY-CS1D",
            payment_method="transfer",
            payment_type="full",
            status="approved",
            amount=Decimal("100.00"),
        )
        detection = detect_related_orders(company=self.company, customer=self.customer)
        item = detection.cancelled_recent_orders[0]
        self.assertTrue(item["recovery_candidate"])

    def test_refunded_money_does_not_make_cancelled_order_recoverable(self):
        order = self.order(status="cancelled")
        Payment.objects.create(
            company=self.company,
            order=order,
            folio="PAY-CS1D-REF",
            payment_method="transfer",
            payment_type="full",
            status="refunded",
            amount=Decimal("100.00"),
        )
        detection = detect_related_orders(company=self.company, customer=self.customer)
        self.assertFalse(detection.cancelled_recent_orders[0]["recovery_candidate"])

    def test_detector_is_bounded_for_large_customer_history(self):
        for _ in range(15):
            self.order(status="draft")
        detection = detect_related_orders(company=self.company, customer=self.customer)
        self.assertLessEqual(len(detection.candidates), 8)

    def test_paid_or_shipped_orders_never_trigger_duplicate_warning(self):
        for status in ("paid", "picking", "packed", "shipped"):
            self.order(status=status)
        detection = detect_related_orders(company=self.company, customer=self.customer)
        self.assertFalse(detection.has_candidates)
        self.assertEqual(detection.total_candidates, 0)
