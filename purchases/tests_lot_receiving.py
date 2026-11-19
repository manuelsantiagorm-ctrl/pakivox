from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.db.models import Sum

from companies.models import Company
from inventory.models import (
    LotBalance,
    LotMovement,
    Product,
    ProductLot,
    ProductVariant,
    Stock,
    Warehouse,
)
from purchases.lot_services import replace_receipt_item_lot_allocations
from purchases.models import (
    GoodsReceiptLot,
    PurchaseOrder,
    PurchaseOrderItem,
    Supplier,
)
from purchases.services import (
    confirm_goods_receipt,
    create_goods_receipt_from_purchase_order,
)
from warehouse.models import VariantLocation, WarehouseLocation


class LotReceivingLT1BTests(TestCase):

    def setUp(self):
        self.accounting_patcher = patch(
            "accounting.services.inventory_integration.post_goods_receipt_entry",
            return_value=None,
        )
        self.accounting_patcher.start()
        self.addCleanup(self.accounting_patcher.stop)

        self.company = Company.objects.create(
            name="LT1B Recepciones",
            slug="lt1b-recepciones",
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            name="Bodega LT1B",
            code="LT1B-R",
        )
        self.supplier = Supplier.objects.create(
            company=self.company,
            name="Proveedor LT1B",
        )
        self.product = Product.objects.create(
            company=self.company,
            name="Producto LT1B",
            base_sku="LT1B",
            cost_price=Decimal("10.00"),
            sale_price=Decimal("20.00"),
        )
        self.location = WarehouseLocation.objects.create(
            company=self.company,
            warehouse=self.warehouse,
            aisle="A",
            rack="1",
            level="1",
            bin_code="A-1-1",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )

    def make_receipt(self, *, mode, quantity=10, sku="LT1B-SKU"):
        variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku=sku,
            cost_price=Decimal("10.00"),
            sale_price=Decimal("20.00"),
            inventory_control=mode,
        )
        order = PurchaseOrder.objects.create(
            company=self.company,
            supplier=self.supplier,
            warehouse=self.warehouse,
            status=PurchaseOrder.STATUS_SENT,
        )
        order_item = PurchaseOrderItem.objects.create(
            purchase_order=order,
            variant=variant,
            quantity_ordered=quantity,
            quantity_received=0,
            unit_cost=Decimal("10.00"),
        )
        receipt = create_goods_receipt_from_purchase_order(
            purchase_order=order,
            user=None,
        )
        return variant, order, order_item, receipt, receipt.items.get()

    def test_normal_receipt_keeps_legacy_flow_without_lots(self):
        variant, order, order_item, receipt, item = self.make_receipt(
            mode=ProductVariant.INVENTORY_CONTROL_NORMAL,
        )
        item.location = self.location
        item.save(update_fields=["location"])

        confirm_goods_receipt(receipt=receipt, user=None)

        self.assertEqual(
            Stock.objects.get(
                company=self.company,
                warehouse=self.warehouse,
                variant=variant,
            ).physical_stock,
            10,
        )
        self.assertFalse(ProductLot.objects.filter(variant=variant).exists())
        self.assertFalse(GoodsReceiptLot.objects.filter(receipt_item=item).exists())

    def test_lot_receipt_requires_allocations(self):
        variant, order, order_item, receipt, item = self.make_receipt(
            mode=ProductVariant.INVENTORY_CONTROL_LOT,
        )
        item.location = self.location
        item.save(update_fields=["location"])

        with self.assertRaises(ValidationError):
            confirm_goods_receipt(receipt=receipt, user=None)

        self.assertFalse(
            Stock.objects.filter(
                company=self.company,
                warehouse=self.warehouse,
                variant=variant,
            ).exists()
        )

    def test_lot_allocation_must_cover_exact_quantity(self):
        variant, order, order_item, receipt, item = self.make_receipt(
            mode=ProductVariant.INVENTORY_CONTROL_LOT,
        )

        with self.assertRaises(ValidationError):
            replace_receipt_item_lot_allocations(
                receipt_item=item,
                allocations=[
                    {
                        "lot_number": "A",
                        "quantity_received": 9,
                        "manufactured_date": None,
                        "expiration_date": None,
                    }
                ],
            )

    def test_lot_receipt_supports_multiple_lots_atomically(self):
        variant, order, order_item, receipt, item = self.make_receipt(
            mode=ProductVariant.INVENTORY_CONTROL_LOT,
        )
        item.location = self.location
        item.save(update_fields=["location"])

        replace_receipt_item_lot_allocations(
            receipt_item=item,
            allocations=[
                {
                    "lot_number": "lote-a",
                    "quantity_received": 4,
                    "manufactured_date": None,
                    "expiration_date": None,
                },
                {
                    "lot_number": "lote-b",
                    "quantity_received": 6,
                    "manufactured_date": None,
                    "expiration_date": None,
                },
            ],
        )

        confirm_goods_receipt(receipt=receipt, user=None)

        lots = ProductLot.objects.filter(
            company=self.company,
            variant=variant,
        ).order_by("lot_number")

        self.assertEqual(list(lots.values_list("lot_number", flat=True)), ["LOTE-A", "LOTE-B"])
        self.assertEqual(
            LotBalance.objects.filter(
                company=self.company,
                lot__variant=variant,
                location=self.location,
            ).aggregate(total=Sum("physical_quantity"))["total"],
            10,
        )
        self.assertEqual(
            LotMovement.objects.filter(
                company=self.company,
                lot__variant=variant,
                movement_type=LotMovement.TYPE_RECEIPT,
            ).count(),
            2,
        )
        self.assertEqual(
            GoodsReceiptLot.objects.filter(
                receipt_item=item,
                product_lot__isnull=False,
            ).count(),
            2,
        )

    def test_lot_expiry_requires_expiration_date(self):
        variant, order, order_item, receipt, item = self.make_receipt(
            mode=ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY,
        )

        with self.assertRaises(ValidationError):
            replace_receipt_item_lot_allocations(
                receipt_item=item,
                allocations=[
                    {
                        "lot_number": "EXP-1",
                        "quantity_received": 10,
                        "manufactured_date": None,
                        "expiration_date": None,
                    }
                ],
            )

    def test_staging_receipt_keeps_lot_balance_in_staging(self):
        variant, order, order_item, receipt, item = self.make_receipt(
            mode=ProductVariant.INVENTORY_CONTROL_LOT,
            sku="LT1B-STAGING",
        )
        replace_receipt_item_lot_allocations(
            receipt_item=item,
            allocations=[
                {
                    "lot_number": "STG-1",
                    "quantity_received": 10,
                    "manufactured_date": date(2026, 1, 1),
                    "expiration_date": None,
                }
            ],
        )

        confirm_goods_receipt(receipt=receipt, user=None)

        item.refresh_from_db()
        self.assertEqual(item.location.status, WarehouseLocation.STATUS_STAGING)
        balance = LotBalance.objects.get(
            company=self.company,
            lot__variant=variant,
        )
        self.assertEqual(balance.location_id, item.location_id)
        self.assertEqual(balance.physical_quantity, 10)

    def test_existing_lot_cannot_change_expiration_date(self):
        variant, order, order_item, receipt, item = self.make_receipt(
            mode=ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY,
            quantity=5,
            sku="LT1B-EXISTING",
        )
        item.location = self.location
        item.save(update_fields=["location"])

        ProductLot.objects.create(
            company=self.company,
            variant=variant,
            lot_number="SAME",
            expiration_date=date(2027, 1, 1),
        )

        replace_receipt_item_lot_allocations(
            receipt_item=item,
            allocations=[
                {
                    "lot_number": "SAME",
                    "quantity_received": 5,
                    "manufactured_date": None,
                    "expiration_date": date(2028, 1, 1),
                }
            ],
        )

        with self.assertRaises(ValidationError):
            confirm_goods_receipt(receipt=receipt, user=None)
