from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from companies.models import Company
from inventory.lot_adoption import adopt_variant_to_lot_control
from inventory.models import (
    LotBalance,
    LotMovement,
    Product,
    ProductVariant,
    Stock,
    Warehouse,
)
from warehouse.models import VariantLocation, WarehouseLocation


class LotAdoptionLT1BTests(TestCase):

    def setUp(self):
        self.company = Company.objects.create(
            name="LT1B Adopcion",
            slug="lt1b-adopcion",
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            name="Bodega adopcion",
            code="LT1B-A",
        )
        self.product = Product.objects.create(
            company=self.company,
            name="Producto adopcion",
            base_sku="ADOPT",
            cost_price=Decimal("10.00"),
            sale_price=Decimal("20.00"),
        )
        self.variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="ADOPT-SKU",
            inventory_control=ProductVariant.INVENTORY_CONTROL_NORMAL,
        )
        self.location = WarehouseLocation.objects.create(
            company=self.company,
            warehouse=self.warehouse,
            aisle="A",
            rack="1",
            level="1",
            bin_code="ADOPT-A1",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )
        self.stock = Stock.objects.create(
            company=self.company,
            warehouse=self.warehouse,
            variant=self.variant,
            physical_stock=10,
            reserved_stock=0,
            committed_stock=0,
        )
        self.variant_location = VariantLocation.objects.create(
            company=self.company,
            variant=self.variant,
            location=self.location,
            quantity=10,
        )

    def test_exact_adoption_switches_mode_and_builds_balances(self):
        adopt_variant_to_lot_control(
            company=self.company,
            variant=self.variant,
            target_mode=ProductVariant.INVENTORY_CONTROL_LOT,
            allocations=[
                {
                    "location_id": self.location.id,
                    "lot_number": "A",
                    "quantity": 4,
                    "manufactured_date": None,
                    "expiration_date": None,
                },
                {
                    "location_id": self.location.id,
                    "lot_number": "B",
                    "quantity": 6,
                    "manufactured_date": None,
                    "expiration_date": None,
                },
            ],
            allowed_warehouse_ids={self.warehouse.id},
        )

        self.variant.refresh_from_db()
        self.assertEqual(
            self.variant.inventory_control,
            ProductVariant.INVENTORY_CONTROL_LOT,
        )
        self.assertEqual(
            sum(
                LotBalance.objects.filter(
                    company=self.company,
                    lot__variant=self.variant,
                    location=self.location,
                ).values_list("physical_quantity", flat=True)
            ),
            10,
        )
        self.assertEqual(
            LotMovement.objects.filter(
                company=self.company,
                lot__variant=self.variant,
                movement_type=LotMovement.TYPE_RECONCILIATION,
            ).count(),
            2,
        )

    def test_partial_adoption_is_rejected_and_mode_remains_normal(self):
        with self.assertRaises(ValidationError):
            adopt_variant_to_lot_control(
                company=self.company,
                variant=self.variant,
                target_mode=ProductVariant.INVENTORY_CONTROL_LOT,
                allocations=[
                    {
                        "location_id": self.location.id,
                        "lot_number": "A",
                        "quantity": 9,
                        "manufactured_date": None,
                        "expiration_date": None,
                    }
                ],
                allowed_warehouse_ids={self.warehouse.id},
            )

        self.variant.refresh_from_db()
        self.assertEqual(
            self.variant.inventory_control,
            ProductVariant.INVENTORY_CONTROL_NORMAL,
        )
        self.assertFalse(
            LotBalance.objects.filter(lot__variant=self.variant).exists()
        )

    def test_adoption_blocks_reserved_stock(self):
        self.stock.reserved_stock = 1
        self.stock.save(update_fields=["reserved_stock"])

        with self.assertRaises(ValidationError):
            adopt_variant_to_lot_control(
                company=self.company,
                variant=self.variant,
                target_mode=ProductVariant.INVENTORY_CONTROL_LOT,
                allocations=[],
                allowed_warehouse_ids={self.warehouse.id},
            )

    def test_adoption_blocks_stock_location_drift(self):
        self.variant_location.quantity = 9
        self.variant_location.save(update_fields=["quantity"])

        with self.assertRaises(ValidationError):
            adopt_variant_to_lot_control(
                company=self.company,
                variant=self.variant,
                target_mode=ProductVariant.INVENTORY_CONTROL_LOT,
                allocations=[],
                allowed_warehouse_ids={self.warehouse.id},
            )

    def test_lot_expiry_adoption_requires_expiration(self):
        with self.assertRaises(ValidationError):
            adopt_variant_to_lot_control(
                company=self.company,
                variant=self.variant,
                target_mode=ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY,
                allocations=[
                    {
                        "location_id": self.location.id,
                        "lot_number": "EXP",
                        "quantity": 10,
                        "manufactured_date": date(2026, 1, 1),
                        "expiration_date": None,
                    }
                ],
                allowed_warehouse_ids={self.warehouse.id},
            )

    def test_direct_mode_change_is_blocked_outside_adoption(self):
        self.variant.inventory_control = ProductVariant.INVENTORY_CONTROL_LOT

        with self.assertRaises(ValidationError):
            self.variant.full_clean()

        self.variant.refresh_from_db()
        self.assertEqual(
            self.variant.inventory_control,
            ProductVariant.INVENTORY_CONTROL_NORMAL,
        )

    def test_adoption_requires_access_to_all_stock_warehouses(self):
        with self.assertRaises(ValidationError):
            adopt_variant_to_lot_control(
                company=self.company,
                variant=self.variant,
                target_mode=ProductVariant.INVENTORY_CONTROL_LOT,
                allocations=[
                    {
                        "location_id": self.location.id,
                        "lot_number": "A",
                        "quantity": 10,
                        "manufactured_date": None,
                        "expiration_date": None,
                    }
                ],
                allowed_warehouse_ids=set(),
            )
