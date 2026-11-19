from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.test import TestCase

from companies.models import Company
from crm.models import Customer
from dashboard.models import WarehouseFinancialFact
from dashboard.services.warehouse_cash_attribution import (
    record_locked_payment_collection,
)
from dashboard.services.warehouse_financial_attribution import attribute_order_sale
from inventory.models import Product, ProductVariant, Warehouse
from payments.models import Payment
from payments.services.refunds import (
    confirm_manual_full_refund,
    request_full_refund,
)
from sales.models import Order, OrderItem
from warehouse.models import OrderFulfillment, OrderItemAllocation


class AF6BCashAttributionTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Empresa AF6B", slug="empresa-af6b",
            timezone="America/Mexico_City",
        )
        self.customer = Customer.objects.create(
            company=self.company, full_name="Cliente AF6B"
        )
        self.product = Product.objects.create(
            company=self.company, name="Producto AF6B"
        )
        self.variant = ProductVariant.objects.create(
            company=self.company, product=self.product, sku="AF6B-SKU",
            sale_price=Decimal("50.00"), cost_price=Decimal("20.00"),
        )
        self.instant = datetime(2026, 8, 13, 18, 0, tzinfo=dt_timezone.utc)

    def make_order(self):
        order = Order.objects.create(
            company=self.company, customer=self.customer,
            status="pending_payment", confirmed_at=self.instant,
        )
        OrderItem.objects.create(
            order=order, variant=self.variant, quantity=2,
            unit_price=Decimal("50.00"), unit_cost=Decimal("20.00"),
        )
        order.refresh_from_db()
        attribute_order_sale(
            company_id=self.company.id, order_id=order.id,
            source_event_type="af6b_seed", source_event_id=f"order:{order.id}",
            occurred_at=self.instant,
        )
        return order

    def make_payment(self, order, amount="40.00"):
        return Payment.objects.create(
            company=self.company, order=order, folio=f"PAY-{order.id}",
            payment_method="transfer", payment_type="deposit",
            status="under_review", amount=Decimal(amount),
        )

    def test_approval_creates_reconciled_estimated_collection(self):
        order = self.make_order()
        payment = self.make_payment(order)
        payment.approve()
        facts = WarehouseFinancialFact.objects.filter(
            company=self.company, order=order,
            fact_type=WarehouseFinancialFact.FACT_COLLECTION,
        )
        self.assertEqual(facts.count(), 1)
        fact = facts.get()
        self.assertEqual(fact.collected_cents, 4000)
        self.assertEqual(fact.quality_label, WarehouseFinancialFact.QUALITY_ESTIMATED)
        self.assertTrue(fact.is_estimated)
        self.assertEqual(fact.date_basis, WarehouseFinancialFact.DATE_PAYMENT_APPROVED)
        payment.refresh_from_db()
        self.assertEqual(
            fact.effective_local_date,
            payment.reviewed_at.astimezone(ZoneInfo("America/Mexico_City")).date(),
        )

    def test_collection_retry_is_idempotent(self):
        order = self.make_order()
        payment = self.make_payment(order)
        payment.approve()
        first_count = WarehouseFinancialFact.objects.filter(
            fact_type=WarehouseFinancialFact.FACT_COLLECTION
        ).count()
        result = record_locked_payment_collection(order=order, payment=payment)
        self.assertEqual(len(result["existing"]), 1)
        self.assertEqual(
            WarehouseFinancialFact.objects.filter(
                fact_type=WarehouseFinancialFact.FACT_COLLECTION
            ).count(),
            first_count,
        )

    def test_financial_failure_rolls_back_payment_approval(self):
        order = self.make_order()
        payment = self.make_payment(order)
        with patch(
            "dashboard.services.warehouse_cash_attribution."
            "record_locked_payment_collection",
            side_effect=ValidationError("Fallo financiero controlado"),
        ):
            with self.assertRaises(ValidationError):
                payment.approve()
        payment.refresh_from_db()
        self.assertEqual(payment.status, "under_review")
        self.assertFalse(
            WarehouseFinancialFact.objects.filter(
                order=order, fact_type=WarehouseFinancialFact.FACT_COLLECTION
            ).exists()
        )

    def test_collection_uses_largest_remainder_across_warehouses(self):
        order = Order.objects.create(
            company=self.company, customer=self.customer,
            status="pending_payment", confirmed_at=self.instant,
        )
        item = OrderItem.objects.create(
            order=order, variant=self.variant, quantity=2,
            unit_price=Decimal("50.00"), unit_cost=Decimal("20.00"),
        )
        south = Warehouse.objects.create(company=self.company, name="Sur AF6B")
        north = Warehouse.objects.create(company=self.company, name="Norte AF6B")
        for sequence, warehouse in enumerate((south, north), start=1):
            fulfillment = OrderFulfillment.objects.create(
                company=self.company, order=order, warehouse=warehouse,
                sequence=sequence, status=OrderFulfillment.STATUS_PENDING,
            )
            OrderItemAllocation.objects.create(
                company=self.company, fulfillment=fulfillment, order_item=item,
                variant=self.variant, commercial_quantity=Decimal("1"),
                internal_quantity=1,
            )
        order.refresh_from_db()
        attribute_order_sale(
            company_id=self.company.id, order_id=order.id,
            source_event_type="af6b_split", source_event_id=f"order:{order.id}",
            occurred_at=self.instant,
        )
        payment = self.make_payment(order, amount="33.33")
        payment.approve()
        values = list(
            WarehouseFinancialFact.objects.filter(
                order=order, fact_type=WarehouseFinancialFact.FACT_COLLECTION
            ).order_by("warehouse_id").values_list("collected_cents", flat=True)
        )
        self.assertEqual(sum(values), 3333)
        self.assertEqual(sorted(values), [1666, 1667])

    def test_confirmed_refund_compensates_original_collection(self):
        order = self.make_order()
        payment = self.make_payment(order)
        payment.approve()
        order.status = "cancelled"
        order.save(update_fields=["status", "updated_at"])
        decision = request_full_refund(
            company_id=self.company.id, payment_id=payment.id, actor=None,
            reason="Cancelación solicitada", idempotency_key="af6b-request",
        )
        confirm_manual_full_refund(
            company_id=self.company.id, refund_id=decision.refund.id, actor=None,
            confirmation_reference="TRANSFER-DEV-1",
            idempotency_key="af6b-confirm",
        )
        cash_facts = WarehouseFinancialFact.objects.filter(
            order=order,
            fact_type__in=[
                WarehouseFinancialFact.FACT_COLLECTION,
                WarehouseFinancialFact.FACT_REFUND,
            ],
        )
        self.assertEqual(sum(cash_facts.values_list("collected_cents", flat=True)), 0)
        self.assertEqual(
            cash_facts.filter(fact_type=WarehouseFinancialFact.FACT_REFUND).count(),
            1,
        )

    def test_refund_fact_failure_rolls_back_confirmation(self):
        order = self.make_order()
        payment = self.make_payment(order)
        payment.approve()
        order.status = "cancelled"
        order.save(update_fields=["status", "updated_at"])
        decision = request_full_refund(
            company_id=self.company.id, payment_id=payment.id, actor=None,
            reason="Cancelación solicitada", idempotency_key="af6b-request-fail",
        )
        with patch(
            "dashboard.services.warehouse_cash_attribution."
            "record_locked_payment_refund",
            side_effect=ValidationError("Fallo financiero controlado"),
        ):
            with self.assertRaises(ValidationError):
                confirm_manual_full_refund(
                    company_id=self.company.id, refund_id=decision.refund.id,
                    actor=None, confirmation_reference="TRANSFER-DEV-FAIL",
                    idempotency_key="af6b-confirm-fail",
                )
        payment.refresh_from_db()
        decision.refund.refresh_from_db()
        self.assertEqual(payment.status, "approved")
        self.assertEqual(
            decision.refund.status, decision.refund.STATUS_REQUESTED
        )
        self.assertFalse(
            WarehouseFinancialFact.objects.filter(
                order=order, fact_type=WarehouseFinancialFact.FACT_REFUND
            ).exists()
        )
