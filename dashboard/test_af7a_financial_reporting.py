from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from companies.models import Company
from crm.models import Customer
from dashboard.models import WarehouseFinancialFact
from dashboard.services.warehouse_financial_attribution import attribute_order_sale
from dashboard.services.warehouse_financial_reporting import (
    get_financial_summary,
    reconcile_order_financial_book,
)
from inventory.models import Product, ProductVariant, Warehouse
from payments.models import Payment
from sales.models import Order, OrderItem
from warehouse.models import OrderFulfillment, OrderItemAllocation


class AF7AFinancialReportingTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Empresa AF7A", slug="empresa-af7a",
            timezone="America/Mexico_City",
        )
        self.other = Company.objects.create(
            name="Otra AF7A", slug="otra-af7a",
            timezone="America/Mexico_City",
        )
        self.customer = Customer.objects.create(
            company=self.company, full_name="Cliente AF7A"
        )
        self.product = Product.objects.create(
            company=self.company, name="Producto AF7A"
        )
        self.variant = ProductVariant.objects.create(
            company=self.company, product=self.product, sku="AF7A-SKU",
            sale_price=Decimal("50.00"), cost_price=Decimal("20.00"),
        )
        self.south = Warehouse.objects.create(company=self.company, name="Sur AF7A")
        self.north = Warehouse.objects.create(company=self.company, name="Norte AF7A")
        self.instant = datetime(2026, 8, 13, 18, 0, tzinfo=dt_timezone.utc)

    def make_order(self, *, split=True):
        order = Order.objects.create(
            company=self.company, customer=self.customer,
            status="pending_payment", confirmed_at=self.instant,
        )
        item = OrderItem.objects.create(
            order=order, variant=self.variant, quantity=2,
            unit_price=Decimal("50.00"), unit_cost=Decimal("20.00"),
        )
        if split:
            for sequence, warehouse in enumerate((self.south, self.north), start=1):
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
            source_event_type="af7a_seed", source_event_id=f"order:{order.id}",
            occurred_at=self.instant,
        )
        return order

    def approve_deposit(self, order):
        payment = Payment.objects.create(
            company=self.company, order=order, folio=f"PAY-AF7A-{order.id}",
            payment_method="transfer", payment_type="deposit",
            status="under_review", amount=Decimal("40.00"),
        )
        payment.approve()
        payment.refresh_from_db()
        return payment

    def summary(self, warehouse_id=None):
        return get_financial_summary(
            company_id=self.company.id,
            start_date=date(2020, 1, 1), end_date=date(2030, 12, 31),
            warehouse_id=warehouse_id,
        )

    def test_all_warehouses_summary_uses_financial_facts(self):
        order = self.make_order()
        self.approve_deposit(order)
        result = self.summary()
        self.assertEqual(result["sales_cents"], 10000)
        self.assertEqual(result["cost_cents"], 4000)
        self.assertEqual(result["gross_profit_cents"], 6000)
        self.assertEqual(result["collected_cents"], 4000)
        self.assertEqual(result["sales_quality"], "attributed")
        self.assertEqual(result["collected_quality"], "estimated")

    def test_warehouse_scope_changes_sales_and_collection(self):
        order = self.make_order()
        self.approve_deposit(order)
        south = self.summary(self.south.id)
        north = self.summary(self.north.id)
        self.assertEqual(south["sales_cents"], 5000)
        self.assertEqual(north["sales_cents"], 5000)
        self.assertEqual(south["collected_cents"], 2000)
        self.assertEqual(north["collected_cents"], 2000)

    def test_period_uses_effective_local_date(self):
        self.make_order()
        included = get_financial_summary(
            company_id=self.company.id,
            start_date=date(2026, 8, 13), end_date=date(2026, 8, 13),
        )
        excluded = get_financial_summary(
            company_id=self.company.id,
            start_date=date(2026, 8, 12), end_date=date(2026, 8, 12),
        )
        self.assertEqual(included["sales_cents"], 10000)
        self.assertEqual(excluded["sales_cents"], 0)

    def test_cross_company_warehouse_is_rejected(self):
        foreign = Warehouse.objects.create(company=self.other, name="Ajena AF7A")
        with self.assertRaises(ValidationError):
            get_financial_summary(
                company_id=self.company.id,
                start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
                warehouse_id=foreign.id,
            )

    def test_reconciler_reports_healthy_order_without_writing(self):
        order = self.make_order()
        self.approve_deposit(order)
        count_before = WarehouseFinancialFact.objects.count()
        result = reconcile_order_financial_book(
            company_id=self.company.id, order_id=order.id
        )
        self.assertTrue(result["is_reconciled"])
        self.assertTrue(result["read_only"])
        self.assertEqual(result["issues"], [])
        self.assertEqual(WarehouseFinancialFact.objects.count(), count_before)

    def test_reconciler_detects_missing_collection_without_correcting(self):
        order = self.make_order()
        payment = Payment.objects.create(
            company=self.company, order=order, folio="PAY-AF7A-LEGACY",
            payment_method="manual", payment_type="deposit",
            status="approved", amount=Decimal("25.00"),
        )
        count_before = WarehouseFinancialFact.objects.count()
        result = reconcile_order_financial_book(
            company_id=self.company.id, order_id=order.id
        )
        issue = next(
            row for row in result["issues"]
            if row["code"] == "collection_mismatch" and row["payment_id"] == payment.id
        )
        self.assertEqual(issue["difference_cents"], 2500)
        self.assertFalse(result["is_reconciled"])
        self.assertEqual(WarehouseFinancialFact.objects.count(), count_before)
