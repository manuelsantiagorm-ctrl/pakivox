from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from companies.models import Company
from inventory.models import (
    LotBalance,
    LotMovement,
    Product,
    ProductLot,
    ProductVariant,
    Warehouse,
)
from warehouse.models import WarehouseLocation


class LotFoundationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="LT1A Empresa",
            slug="lt1a-empresa",
        )
        self.other_company = Company.objects.create(
            name="LT1A Otra Empresa",
            slug="lt1a-otra-empresa",
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            name="LT1A Bodega",
            code="LT1A-WH",
        )
        self.other_warehouse = Warehouse.objects.create(
            company=self.other_company,
            name="LT1A Otra Bodega",
            code="LT1A-WH-OTHER",
        )
        self.location = WarehouseLocation.objects.create(
            company=self.company,
            warehouse=self.warehouse,
            aisle="A",
            rack="01",
            level="01",
            bin_code="A01-01",
        )
        self.other_location = WarehouseLocation.objects.create(
            company=self.other_company,
            warehouse=self.other_warehouse,
            aisle="B",
            rack="01",
            level="01",
            bin_code="B01-01",
        )
        self.product = Product.objects.create(
            company=self.company,
            name="Proteína LT1A",
            base_sku="PROT-LT1A",
        )
        self.other_product = Product.objects.create(
            company=self.other_company,
            name="Proteína LT1A Otra",
            base_sku="PROT-LT1A-OTHER",
        )
        self.variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="PROT-LT1A-2KG",
            inventory_control=ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY,
        )
        self.other_variant = ProductVariant.objects.create(
            company=self.other_company,
            product=self.other_product,
            sku="PROT-LT1A-OTHER-2KG",
            inventory_control=ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY,
        )

    def make_lot(self, **overrides):
        data = {
            "company": self.company,
            "variant": self.variant,
            "lot_number": "LT1A-LOTE-001",
            "expiration_date": timezone.localdate() + timedelta(days=90),
        }
        data.update(overrides)
        return ProductLot.objects.create(**data)

    def test_normal_is_default_inventory_control(self):
        variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="LT1A-NORMAL-SKU",
        )
        self.assertEqual(
            variant.inventory_control,
            ProductVariant.INVENTORY_CONTROL_NORMAL,
        )

    def test_virtual_bundle_cannot_enable_lot_control(self):
        variant = ProductVariant(
            company=self.company,
            product=self.product,
            sku="LT1A-BUNDLE",
            is_bundle=True,
            inventory_control=ProductVariant.INVENTORY_CONTROL_LOT,
        )
        with self.assertRaises(ValidationError):
            variant.full_clean()

    def test_normal_variant_cannot_register_product_lot(self):
        normal_variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="LT1A-NOLOT",
        )
        lot = ProductLot(
            company=self.company,
            variant=normal_variant,
            lot_number="NO-DEBE-EXISTIR",
        )
        with self.assertRaises(ValidationError):
            lot.full_clean()

    def test_lot_expiry_requires_expiration_date(self):
        lot = ProductLot(
            company=self.company,
            variant=self.variant,
            lot_number="SIN-CADUCIDAD",
        )
        with self.assertRaises(ValidationError):
            lot.full_clean()

    def test_lot_mode_does_not_require_expiration_date(self):
        lot_variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="LT1A-LOTONLY",
            inventory_control=ProductVariant.INVENTORY_CONTROL_LOT,
        )
        lot = ProductLot(
            company=self.company,
            variant=lot_variant,
            lot_number="LOTE-SIN-CAD",
        )
        lot.full_clean()

    def test_lot_number_is_normalized(self):
        lot = self.make_lot(lot_number="  abc-001  ")
        self.assertEqual(lot.lot_number, "ABC-001")

    def test_lot_company_must_match_variant_company(self):
        lot = ProductLot(
            company=self.company,
            variant=self.other_variant,
            lot_number="FOREIGN",
            expiration_date=timezone.localdate() + timedelta(days=30),
        )
        with self.assertRaises(ValidationError):
            lot.full_clean()

    def test_lot_number_unique_per_company_and_variant(self):
        self.make_lot(lot_number="UNICO")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.make_lot(lot_number="UNICO")

    def test_same_lot_number_is_allowed_for_another_company(self):
        self.make_lot(lot_number="FAB-2026")
        other_lot = ProductLot.objects.create(
            company=self.other_company,
            variant=self.other_variant,
            lot_number="FAB-2026",
            expiration_date=timezone.localdate() + timedelta(days=90),
        )
        self.assertIsNotNone(other_lot.pk)

    def test_expired_lot_is_not_allocatable(self):
        lot = self.make_lot(
            lot_number="VENCIDO",
            expiration_date=timezone.localdate() - timedelta(days=1),
        )
        self.assertTrue(lot.is_expired)
        self.assertFalse(lot.is_allocatable)

    def test_lot_expiring_today_is_not_yet_expired(self):
        lot = self.make_lot(
            lot_number="HOY",
            expiration_date=timezone.localdate(),
        )
        self.assertFalse(lot.is_expired)
        self.assertTrue(lot.is_allocatable)

    def test_lot_balance_available_quantity(self):
        lot = self.make_lot()
        balance = LotBalance.objects.create(
            company=self.company,
            lot=lot,
            location=self.location,
            physical_quantity=100,
            reserved_quantity=30,
            committed_quantity=10,
        )
        self.assertEqual(balance.available_quantity, 60)
        self.assertEqual(balance.allocatable_quantity, 60)

    def test_lot_balance_rejects_foreign_company_location(self):
        lot = self.make_lot()
        balance = LotBalance(
            company=self.company,
            lot=lot,
            location=self.other_location,
            physical_quantity=10,
        )
        with self.assertRaises(ValidationError):
            balance.full_clean()

    def test_lot_balance_db_rejects_negative_physical(self):
        lot = self.make_lot()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LotBalance.objects.create(
                    company=self.company,
                    lot=lot,
                    location=self.location,
                    physical_quantity=-1,
                )

    def test_lot_balance_db_rejects_reserved_plus_committed_over_physical(self):
        lot = self.make_lot()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LotBalance.objects.create(
                    company=self.company,
                    lot=lot,
                    location=self.location,
                    physical_quantity=10,
                    reserved_quantity=8,
                    committed_quantity=3,
                )

    def test_quarantine_balance_cannot_keep_reservations(self):
        lot = self.make_lot()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LotBalance.objects.create(
                    company=self.company,
                    lot=lot,
                    location=self.location,
                    status=LotBalance.STATUS_QUARANTINE,
                    physical_quantity=10,
                    reserved_quantity=1,
                )

    def test_lot_movement_event_key_is_idempotent_per_company(self):
        lot = self.make_lot()
        LotMovement.objects.create(
            company=self.company,
            lot=lot,
            movement_type=LotMovement.TYPE_RECEIPT,
            quantity=10,
            event_key="lt1a:receipt:1",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LotMovement.objects.create(
                    company=self.company,
                    lot=lot,
                    movement_type=LotMovement.TYPE_RECEIPT,
                    quantity=10,
                    event_key="lt1a:receipt:1",
                )

    def test_lot_movement_is_immutable(self):
        lot = self.make_lot()
        movement = LotMovement.objects.create(
            company=self.company,
            lot=lot,
            movement_type=LotMovement.TYPE_RECEIPT,
            quantity=10,
        )
        movement.notes = "No debe editarse"
        with self.assertRaises(ValidationError):
            movement.save()
