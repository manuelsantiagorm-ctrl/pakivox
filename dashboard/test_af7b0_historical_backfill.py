from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from companies.models import Company
from crm.models import Customer
from dashboard.models import WarehouseFinancialFact
from inventory.models import Product, ProductVariant, Warehouse
from payments.models import Payment
from sales.models import Order, OrderItem
from warehouse.models import OrderFulfillment, OrderItemAllocation


class AF7B0HistoricalBackfillTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Empresa AF7B0", slug="empresa-af7b0",
            timezone="America/Mexico_City",
        )
        self.other = Company.objects.create(
            name="Otra AF7B0", slug="otra-af7b0",
            timezone="America/Mexico_City",
        )
        self.customer = Customer.objects.create(
            company=self.company, full_name="Cliente AF7B0"
        )
        self.product = Product.objects.create(
            company=self.company, name="Producto AF7B0"
        )
        self.variant = ProductVariant.objects.create(
            company=self.company, product=self.product, sku="AF7B0-SKU",
            sale_price=Decimal("50.00"), cost_price=Decimal("20.00"),
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company, name="Principal AF7B0"
        )
        self.instant = datetime(2026, 7, 15, 18, 0, tzinfo=dt_timezone.utc)

    def make_order(self, *, company=None, status="pending_payment", allocate=True):
        company = company or self.company
        customer = self.customer
        if company != self.company:
            customer = Customer.objects.create(
                company=company, full_name="Cliente ajeno AF7B0"
            )
        order = Order.objects.create(
            company=company, customer=customer, status=status,
            confirmed_at=self.instant,
        )
        if company == self.company:
            item = OrderItem.objects.create(
                order=order, variant=self.variant, quantity=2,
                unit_price=Decimal("50.00"), unit_cost=Decimal("20.00"),
            )
            if allocate:
                fulfillment = OrderFulfillment.objects.create(
                    company=company, order=order, warehouse=self.warehouse,
                    sequence=1, status=OrderFulfillment.STATUS_PENDING,
                )
                OrderItemAllocation.objects.create(
                    company=company, fulfillment=fulfillment, order_item=item,
                    variant=self.variant, commercial_quantity=Decimal("2"),
                    internal_quantity=2,
                )
        return order

    def make_approved_payment(self, order, amount="40.00"):
        return Payment.objects.create(
            company=order.company, order=order, folio=f"HIST-{order.company_id}-{order.id}",
            payment_method="manual", payment_type="deposit",
            status="approved", amount=Decimal(amount), paid_at=self.instant,
        )

    def run_command(self, *args):
        output = StringIO()
        call_command("backfill_warehouse_financial_facts", *args, stdout=output)
        return output.getvalue()

    def test_default_is_dry_run_and_writes_nothing(self):
        order = self.make_order()
        self.make_approved_payment(order)
        output = self.run_command("--company-id", str(self.company.id))
        self.assertIn("SIMULACIÓN", output)
        self.assertEqual(WarehouseFinancialFact.objects.count(), 0)

    def test_apply_creates_sale_and_collection_facts(self):
        order = self.make_order()
        self.make_approved_payment(order)
        output = self.run_command(
            "--apply", "--company-id", str(self.company.id)
        )
        self.assertIn("BACKFILL AF-7B0 APLICADO", output)
        sales = WarehouseFinancialFact.objects.filter(
            order=order, fact_type=WarehouseFinancialFact.FACT_SALE
        )
        cash = WarehouseFinancialFact.objects.filter(
            order=order, fact_type=WarehouseFinancialFact.FACT_COLLECTION
        )
        self.assertEqual(sum(sales.values_list("total_cents", flat=True)), 10000)
        self.assertEqual(sum(cash.values_list("collected_cents", flat=True)), 4000)
        self.assertEqual(sales.get().warehouse, self.warehouse)
        self.assertEqual(cash.get().warehouse, self.warehouse)

    def test_apply_is_idempotent(self):
        order = self.make_order()
        self.make_approved_payment(order)
        self.run_command("--apply", "--company-id", str(self.company.id))
        count = WarehouseFinancialFact.objects.count()
        second = self.run_command("--apply", "--company-id", str(self.company.id))
        self.assertEqual(WarehouseFinancialFact.objects.count(), count)
        self.assertIn("sale_existing=1", second)
        self.assertIn("collection_existing=1", second)

    def test_company_filter_never_processes_another_tenant(self):
        own = self.make_order()
        foreign = self.make_order(company=self.other, allocate=False)
        self.run_command("--apply", "--company-id", str(self.company.id))
        self.assertTrue(WarehouseFinancialFact.objects.filter(order=own).exists())
        self.assertFalse(WarehouseFinancialFact.objects.filter(order=foreign).exists())

    def test_cancelled_order_skips_sale_but_preserves_historical_cash(self):
        order = self.make_order(status="cancelled", allocate=False)
        self.make_approved_payment(order, amount="25.00")
        self.run_command("--apply", "--company-id", str(self.company.id))
        self.assertFalse(WarehouseFinancialFact.objects.filter(
            order=order, fact_type=WarehouseFinancialFact.FACT_SALE
        ).exists())
        cash = WarehouseFinancialFact.objects.get(
            order=order, fact_type=WarehouseFinancialFact.FACT_COLLECTION
        )
        self.assertEqual(cash.collected_cents, 2500)
        self.assertIsNone(cash.warehouse_id)

    def test_inferred_approval_date_is_audited_as_estimated(self):
        order = self.make_order()
        payment = self.make_approved_payment(order)
        Payment.objects.filter(pk=payment.pk).update(
            reviewed_at=None, paid_at=None
        )
        self.run_command("--apply", "--company-id", str(self.company.id))
        fact = WarehouseFinancialFact.objects.get(
            order=order, fact_type=WarehouseFinancialFact.FACT_COLLECTION
        )
        self.assertEqual(fact.quality_label, WarehouseFinancialFact.QUALITY_ESTIMATED)
        self.assertIn(
            "inferred_payment_approval_date",
            fact.metadata["estimate_reasons"],
        )
