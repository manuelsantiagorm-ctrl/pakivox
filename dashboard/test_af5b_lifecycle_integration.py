from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from companies.models import Company
from crm.models import Customer
from dashboard.models import WarehouseFinancialFact
from dashboard.services.warehouse_financial_attribution import attribute_order_sale
from inventory.models import Product, ProductVariant, Warehouse
from sales.models import Order, OrderItem, OrderResolutionEvent
from sales.services.order_resolution import cancel_order_safely
from warehouse.models import OrderFulfillment, OrderItemAllocation
from warehouse.services.fulfillment_planning import plan_order_fulfillments


class AF5BLifecycleIntegrationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Empresa AF5B", slug="empresa-af5b", timezone="America/Mexico_City"
        )
        self.customer = Customer.objects.create(
            company=self.company, full_name="Cliente AF5B"
        )
        self.product = Product.objects.create(company=self.company, name="Producto AF5B")
        self.variant = ProductVariant.objects.create(
            company=self.company, product=self.product, sku="AF5B-SKU",
            sale_price=Decimal("100.00"), cost_price=Decimal("50.00"),
        )
        self.south = Warehouse.objects.create(company=self.company, name="Sur AF5B")
        self.confirmed = datetime(2026, 8, 13, 16, 0, tzinfo=dt_timezone.utc)

    def make_order(self, status="paid"):
        order = Order.objects.create(
            company=self.company, customer=self.customer, status=status,
            confirmed_at=self.confirmed, discount=Decimal("20.00"),
            tax=Decimal("28.80"), shipping_cost=Decimal("10.00"),
        )
        OrderItem.objects.create(
            order=order, variant=self.variant, quantity=2,
            unit_price=Decimal("100.00"), unit_cost=Decimal("50.00"),
        )
        order.refresh_from_db()
        return order

    def make_cancelled_fulfillment(self, order):
        fulfillment = OrderFulfillment.objects.create(
            company=self.company, order=order, warehouse=self.south,
            sequence=1, status=OrderFulfillment.STATUS_CANCELLED,
        )
        item = order.items.get()
        OrderItemAllocation.objects.create(
            company=self.company, fulfillment=fulfillment, order_item=item,
            variant=self.variant, commercial_quantity=Decimal("2"),
            internal_quantity=2,
        )
        return fulfillment

    def test_persisted_plan_creates_one_financial_version(self):
        order = self.make_order()
        fulfillment = self.make_cancelled_fulfillment(order)
        plan_order_fulfillments(order=order)
        fulfillment.refresh_from_db()
        self.assertEqual(fulfillment.status, OrderFulfillment.STATUS_PENDING)
        facts = WarehouseFinancialFact.objects.filter(
            company=self.company, order=order,
            fact_type=WarehouseFinancialFact.FACT_SALE,
        )
        self.assertEqual(facts.count(), 1)
        self.assertEqual(facts.get().warehouse, self.south)

    def test_retrying_same_plan_does_not_duplicate_financial_facts(self):
        order = self.make_order()
        self.make_cancelled_fulfillment(order)
        plan_order_fulfillments(order=order)
        plan_order_fulfillments(order=order)
        self.assertEqual(
            WarehouseFinancialFact.objects.filter(
                company=self.company, order=order,
            ).count(),
            1,
        )

    def test_financial_failure_rolls_back_fulfillment_reactivation(self):
        order = self.make_order()
        fulfillment = self.make_cancelled_fulfillment(order)
        with patch(
            "warehouse.services.fulfillment_planning._sync_sale_financial_attribution",
            side_effect=ValidationError("Fallo financiero controlado"),
        ):
            with self.assertRaises(ValidationError):
                plan_order_fulfillments(order=order)
        fulfillment.refresh_from_db()
        self.assertEqual(fulfillment.status, OrderFulfillment.STATUS_CANCELLED)
        self.assertFalse(WarehouseFinancialFact.objects.filter(order=order).exists())

    def test_safe_cancellation_compensates_active_sale(self):
        order = self.make_order(status="pending_payment")
        attribute_order_sale(
            company_id=self.company.id, order_id=order.id,
            source_event_type="af5b_seed", source_event_id="seed-cancel",
            occurred_at=self.confirmed,
        )
        decision = cancel_order_safely(
            company_id=self.company.id, order_id=order.id, actor=None,
            reason_code="customer_cancelled",
            reason_text="El cliente canceló la compra.",
            idempotency_key="af5b-safe-cancel",
        )
        order.refresh_from_db()
        self.assertTrue(decision.cancelled)
        self.assertEqual(order.status, "cancelled")
        self.assertEqual(
            sum(
                WarehouseFinancialFact.objects.filter(order=order)
                .values_list("total_cents", flat=True)
            ),
            0,
        )

    def test_financial_failure_rolls_back_cancellation_and_event(self):
        order = self.make_order(status="pending_payment")
        with patch(
            "sales.services.order_resolution._sync_order_financial_event",
            side_effect=ValidationError("Fallo financiero controlado"),
        ):
            with self.assertRaises(ValidationError):
                cancel_order_safely(
                    company_id=self.company.id, order_id=order.id, actor=None,
                    reason_code="customer_cancelled",
                    reason_text="El cliente canceló la compra.",
                    idempotency_key="af5b-rollback-cancel",
                )
        order.refresh_from_db()
        self.assertEqual(order.status, "pending_payment")
        self.assertFalse(
            OrderResolutionEvent.objects.filter(
                company=self.company, order=order,
                idempotency_key="af5b-rollback-cancel",
            ).exists()
        )

