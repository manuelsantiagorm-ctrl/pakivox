from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from companies.models import Company
from inventory.models import Product, ProductVariant, Warehouse
from purchases.models import (
    PurchaseOrder,
    PurchaseOrderItem,
    Supplier,
    SupplierPayment,
)
from purchases.services import (
    get_supplier_payment_totals,
    register_supplier_payment,
)


class PS3B0PurchaseLineTotalIntegrityTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="PS3B0 Compras",
            slug="ps3b0-compras",
        )

        self.warehouse = Warehouse.objects.create(
            company=self.company,
            name="Bodega PS3B0",
            code="PS3B0",
        )

        self.supplier = Supplier.objects.create(
            company=self.company,
            name="Proveedor PS3B0",
        )

        self.product = Product.objects.create(
            company=self.company,
            name="Producto PS3B0",
            base_sku="PS3B0-P",
            cost_price=Decimal("10.00"),
            sale_price=Decimal("20.00"),
        )

        self.variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="PS3B0-SKU",
            cost_price=Decimal("10.00"),
            sale_price=Decimal("20.00"),
        )

        self.order = PurchaseOrder.objects.create(
            company=self.company,
            supplier=self.supplier,
            warehouse=self.warehouse,
            status=PurchaseOrder.STATUS_SENT,
        )

    def _item(
        self,
        *,
        quantity=10,
        unit_cost=Decimal("10.00"),
        sku_suffix="A",
    ):
        variant = self.variant

        if sku_suffix != "A":
            product = Product.objects.create(
                company=self.company,
                name=f"Producto {sku_suffix}",
                base_sku=f"PS3B0-{sku_suffix}",
                cost_price=unit_cost,
                sale_price=Decimal("20.00"),
            )
            variant = ProductVariant.objects.create(
                company=self.company,
                product=product,
                sku=f"PS3B0-SKU-{sku_suffix}",
                cost_price=unit_cost,
                sale_price=Decimal("20.00"),
            )

        return PurchaseOrderItem.objects.create(
            purchase_order=self.order,
            variant=variant,
            quantity_ordered=quantity,
            quantity_received=0,
            unit_cost=unit_cost,
        )

    def test_create_item_persists_line_total_and_order_total(self):
        item = self._item(
            quantity=2,
            unit_cost=Decimal("12.50"),
        )

        item.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(
            item.line_total,
            Decimal("25.00"),
        )
        self.assertEqual(
            self.order.subtotal,
            Decimal("25.00"),
        )
        self.assertEqual(
            self.order.total,
            Decimal("25.00"),
        )

    def test_quantity_update_fields_recalculates_line_total(self):
        item = self._item(
            quantity=2,
            unit_cost=Decimal("12.50"),
        )

        item.quantity_ordered = 4
        item.save(
            update_fields=[
                "quantity_ordered",
                "updated_at",
            ]
        )

        item.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(
            item.line_total,
            Decimal("50.00"),
        )
        self.assertEqual(
            self.order.total,
            Decimal("50.00"),
        )

    def test_unit_cost_update_fields_recalculates_line_total(self):
        item = self._item(
            quantity=3,
            unit_cost=Decimal("10.00"),
        )

        item.unit_cost = Decimal("7.25")
        item.save(
            update_fields=[
                "unit_cost",
                "updated_at",
            ]
        )

        item.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(
            item.line_total,
            Decimal("21.75"),
        )
        self.assertEqual(
            self.order.total,
            Decimal("21.75"),
        )

    def test_quantity_received_only_update_preserves_financial_total(self):
        item = self._item(
            quantity=5,
            unit_cost=Decimal("8.00"),
        )

        item.quantity_received = 2
        item.save(
            update_fields=[
                "quantity_received",
                "updated_at",
            ]
        )

        item.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(
            item.line_total,
            Decimal("40.00"),
        )
        self.assertEqual(
            self.order.total,
            Decimal("40.00"),
        )

    def test_multiple_items_are_aggregated_into_purchase_total(self):
        self._item(
            quantity=2,
            unit_cost=Decimal("12.50"),
        )
        self._item(
            quantity=3,
            unit_cost=Decimal("5.00"),
            sku_suffix="B",
        )

        self.order.refresh_from_db()

        self.assertEqual(
            self.order.subtotal,
            Decimal("40.00"),
        )
        self.assertEqual(
            self.order.total,
            Decimal("40.00"),
        )

    def test_supplier_payment_registration_uses_real_purchase_total(self):
        self._item(
            quantity=10,
            unit_cost=Decimal("10.00"),
        )

        payment = register_supplier_payment(
            purchase_order=self.order,
            amount=Decimal("40.00"),
            user=None,
        )

        payment.refresh_from_db()
        self.order.refresh_from_db()

        totals = get_supplier_payment_totals(
            self.order,
        )

        self.assertEqual(
            payment.status,
            SupplierPayment.STATUS_PENDING,
        )
        self.assertEqual(
            self.order.total,
            Decimal("100.00"),
        )
        self.assertEqual(
            totals["pending_total"],
            Decimal("40.00"),
        )
        self.assertEqual(
            totals["available_to_register"],
            Decimal("60.00"),
        )

    def test_negative_unit_cost_is_rejected_before_persisting_item(self):
        before = PurchaseOrderItem.objects.filter(
            purchase_order=self.order,
        ).count()

        with self.assertRaises(ValidationError):
            self._item(
                quantity=1,
                unit_cost=Decimal("-1.00"),
            )

        after = PurchaseOrderItem.objects.filter(
            purchase_order=self.order,
        ).count()

        self.assertEqual(
            after,
            before,
        )
