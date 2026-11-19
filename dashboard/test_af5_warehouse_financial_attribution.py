from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from companies.models import Company
from crm.models import Customer
from dashboard.models import WarehouseFinancialFact
from dashboard.services.warehouse_financial_attribution import attribute_order_sale
from inventory.models import Product, ProductVariant, Warehouse
from sales.models import Order, OrderItem
from warehouse.models import OrderFulfillment, OrderItemAllocation


class AF5WarehouseFinancialAttributionTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Empresa AF5", slug="empresa-af5", timezone="America/Mexico_City"
        )
        self.other_company = Company.objects.create(
            name="Otra AF5", slug="otra-af5", timezone="America/Mexico_City"
        )
        self.customer = Customer.objects.create(company=self.company, full_name="Cliente")
        self.product = Product.objects.create(company=self.company, name="Producto")
        self.variant = ProductVariant.objects.create(
            company=self.company, product=self.product, sku="AF5-SKU",
            sale_price=Decimal("100.00"), cost_price=Decimal("50.00"),
        )
        self.south = Warehouse.objects.create(company=self.company, name="Sur")
        self.north = Warehouse.objects.create(company=self.company, name="Norte")
        self.confirmed = datetime(2026, 8, 2, 10, 0, tzinfo=dt_timezone.utc)

    def order(self, status="pending_payment", **values):
        defaults = {
            "company": self.company, "customer": self.customer, "status": status,
            "confirmed_at": self.confirmed, "subtotal": Decimal("200.00"),
            "discount": Decimal("20.00"), "tax": Decimal("28.80"),
            "shipping_cost": Decimal("10.00"), "total": Decimal("218.80"),
            "total_cost": Decimal("100.00"),
        }
        defaults.update(values)
        order = Order.objects.create(**defaults)
        OrderItem.objects.create(
            order=order, variant=self.variant, quantity=2,
            unit_price=Decimal("100.00"), total=Decimal("200.00"),
            unit_cost=Decimal("50.00"), total_cost=Decimal("100.00"),
        )
        # OrderItem recalcula el pedido al guardarse. Restauramos el total
        # indicado por el caso para poder probar también datos históricos
        # que no concilian contra sus componentes actuales.
        Order.objects.filter(pk=order.pk).update(total=defaults["total"])
        order.refresh_from_db()
        return order

    def allocate(self, order, warehouse, quantity):
        fulfillment = OrderFulfillment.objects.create(
            company=self.company, order=order, warehouse=warehouse,
            sequence=order.fulfillments.count() + 1,
        )
        item = order.items.get()
        return OrderItemAllocation.objects.create(
            company=self.company, fulfillment=fulfillment, order_item=item,
            variant=self.variant, commercial_quantity=Decimal(str(quantity)),
            internal_quantity=int(quantity),
        )

    def attribute(self, order, event="confirmed"):
        return attribute_order_sale(
            company_id=self.company.id, order_id=order.id,
            source_event_type="af5_test", source_event_id=event,
            occurred_at=self.confirmed,
        )

    def test_full_assignment_creates_one_reconciled_warehouse_fact(self):
        order = self.order()
        self.allocate(order, self.south, 2)
        result = self.attribute(order)
        self.assertEqual(len(result["created"]), 1)
        fact = result["created"][0]
        self.assertEqual(fact.warehouse, self.south)
        self.assertEqual(fact.total_cents, 21880)
        self.assertEqual(fact.gross_profit_cents, 8000)

    def test_partial_assignment_creates_unassigned_bucket_and_reconciles(self):
        order = self.order()
        self.allocate(order, self.south, 1)
        result = self.attribute(order)
        self.assertEqual(len(result["created"]), 2)
        self.assertEqual(sum(f.total_cents for f in result["created"]), 21880)
        self.assertTrue(any(f.warehouse_id is None for f in result["created"]))

    def test_all_bodegas_is_not_duplicated(self):
        order = self.order()
        self.allocate(order, self.south, 1)
        self.allocate(order, self.north, 1)
        facts = self.attribute(order)["created"]
        self.assertEqual(sum(f.subtotal_cents for f in facts), 20000)
        self.assertEqual(sum(f.total_cents for f in facts), 21880)

    def test_same_event_is_idempotent(self):
        order = self.order()
        self.allocate(order, self.south, 2)
        first = self.attribute(order)
        second = self.attribute(order)
        self.assertEqual(len(first["created"]), 1)
        self.assertEqual(second["created"], [])
        self.assertEqual(WarehouseFinancialFact.objects.count(), 1)

    def test_reallocation_reverses_old_facts_and_creates_new_version(self):
        order = self.order()
        allocation = self.allocate(order, self.south, 2)
        self.attribute(order, "v1")
        allocation.fulfillment.warehouse = self.north
        allocation.fulfillment.save(update_fields=["warehouse"])
        result = self.attribute(order, "v2")
        self.assertEqual(len(result["reversed"]), 1)
        self.assertEqual(result["created"][0].source_version, 2)
        self.assertEqual(result["created"][0].warehouse, self.north)
        self.assertEqual(sum(f.total_cents for f in WarehouseFinancialFact.objects.all()), 21880)

    def test_cancelled_order_only_reverses_active_sale(self):
        order = self.order()
        self.allocate(order, self.south, 2)
        self.attribute(order, "v1")
        Order.objects.filter(pk=order.pk).update(status="cancelled")
        result = self.attribute(order, "cancelled")
        self.assertEqual(result["created"], [])
        self.assertEqual(len(result["reversed"]), 1)
        self.assertEqual(sum(f.total_cents for f in WarehouseFinancialFact.objects.all()), 0)

    def test_draft_creates_no_revenue(self):
        order = self.order(status="draft", confirmed_at=None)
        result = self.attribute(order)
        self.assertEqual(result["created"], [])

    def test_cross_company_order_is_rejected(self):
        order = self.order()
        with self.assertRaises(ValidationError):
            attribute_order_sale(
                company_id=self.other_company.id, order_id=order.id,
                source_event_type="af5_test", source_event_id="cross",
                occurred_at=self.confirmed,
            )

    def test_overallocation_is_rejected_without_partial_facts(self):
        order = self.order()
        self.allocate(order, self.south, 2)
        self.allocate(order, self.north, 1)
        with self.assertRaises(ValidationError):
            self.attribute(order)
        self.assertEqual(WarehouseFinancialFact.objects.count(), 0)

    def test_inferred_confirmation_uses_existing_estimated_language(self):
        order = self.order(confirmation_date_is_inferred=True)
        self.allocate(order, self.south, 2)
        fact = self.attribute(order)["created"][0]
        self.assertEqual(fact.quality_label, WarehouseFinancialFact.QUALITY_ESTIMATED)
        self.assertIn("inferred_confirmation_date", fact.metadata["estimate_reasons"])

    def test_legacy_difference_is_explicit_not_hidden(self):
        order = self.order(total=Decimal("218.83"))
        self.allocate(order, self.south, 2)
        fact = self.attribute(order)["created"][0]
        self.assertEqual(fact.legacy_difference_cents, 3)
        self.assertTrue(fact.has_legacy_inconsistency)
