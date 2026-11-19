from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from companies.models import Company
from crm.models import Customer
from inventory.models import (
    LotBalance,
    LotMovement,
    Product,
    ProductLot,
    ProductVariant,
    Stock,
    Warehouse,
)
from sales.models import Order, OrderItem
from warehouse.models import (
    OrderFulfillment,
    OrderItemAllocation,
    PickingItem,
    PickingLotAllocation,
    PickingOrder,
    VariantLocation,
    WarehouseLocation,
)
from warehouse.services.fulfillment_planning import (
    get_pickable_stock_for_variant,
)
from warehouse.services.stock_reservation import (
    consume_reserved_stock_for_shipping,
    release_reserved_stock_for_cancelled_picking,
    report_damaged_stock_during_picking,
    reserve_stock_for_picking,
)


User = get_user_model()


class LotFEFOReservationTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Empresa LT1C",
            slug="empresa-lt1c",
        )
        self.customer = Customer.objects.create(
            company=self.company,
            full_name="Cliente LT1C",
        )
        self.user = User.objects.create_user(
            "lt1c-owner@test.local",
            password="x",
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            name="Bodega LT1C",
            code="LT1C",
        )
        self.location_a = WarehouseLocation.objects.create(
            company=self.company,
            warehouse=self.warehouse,
            aisle="A",
            rack="1",
            level="1",
            bin_code="A1-1",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )
        self.location_b = WarehouseLocation.objects.create(
            company=self.company,
            warehouse=self.warehouse,
            aisle="B",
            rack="1",
            level="1",
            bin_code="B1-1",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )
        self.product = Product.objects.create(
            company=self.company,
            name="Producto LT1C",
            base_sku="LT1C",
        )
        self.variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="LT1C-EXP",
            inventory_control=ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY,
        )

    def _lot(
        self,
        number,
        qty,
        *,
        days=30,
        location=None,
        lot_status=ProductLot.STATUS_ACTIVE,
        balance_status=LotBalance.STATUS_AVAILABLE,
    ):
        location = location or self.location_a
        lot = ProductLot.objects.create(
            company=self.company,
            variant=self.variant,
            lot_number=number,
            expiration_date=timezone.localdate() + timedelta(days=days),
            status=lot_status,
        )
        balance = LotBalance.objects.create(
            company=self.company,
            lot=lot,
            location=location,
            status=balance_status,
            physical_quantity=qty,
            reserved_quantity=0,
            committed_quantity=0,
        )
        return lot, balance

    def _stock_and_locations(self, qty_a, qty_b=0):
        Stock.objects.create(
            company=self.company,
            warehouse=self.warehouse,
            variant=self.variant,
            physical_stock=qty_a + qty_b,
            reserved_stock=0,
            committed_stock=0,
        )
        if qty_a:
            VariantLocation.objects.create(
                company=self.company,
                variant=self.variant,
                location=self.location_a,
                quantity=qty_a,
            )
        if qty_b:
            VariantLocation.objects.create(
                company=self.company,
                variant=self.variant,
                location=self.location_b,
                quantity=qty_b,
            )

    def _picking(self, quantity):
        order = Order.objects.create(
            company=self.company,
            customer=self.customer,
            created_by=self.user,
            status="paid",
            total=quantity,
            amount_paid=quantity,
            balance_due=0,
        )
        order_item = OrderItem.objects.create(
            order=order,
            variant=self.variant,
            quantity=quantity,
            unit_price=1,
        )
        fulfillment = OrderFulfillment.objects.create(
            company=self.company,
            order=order,
            warehouse=self.warehouse,
            sequence=1,
            created_by=self.user,
        )
        OrderItemAllocation.objects.create(
            company=self.company,
            fulfillment=fulfillment,
            order_item=order_item,
            variant=self.variant,
            commercial_quantity=quantity,
            internal_quantity=quantity,
        )
        picking = PickingOrder.objects.create(
            company=self.company,
            order=order,
            fulfillment=fulfillment,
            warehouse=self.warehouse,
            assigned_to=self.user,
        )
        return picking

    def test_fefo_selects_earliest_expiration_first(self):
        late, late_balance = self._lot("LATE", 10, days=60)
        early, early_balance = self._lot("EARLY", 10, days=10)
        self._stock_and_locations(20)

        picking = reserve_stock_for_picking(self._picking(6), user=self.user)

        allocation = PickingLotAllocation.objects.get(
            picking_item__picking_order=picking
        )
        self.assertEqual(allocation.lot_id, early.id)
        self.assertEqual(allocation.quantity, 6)
        early_balance.refresh_from_db()
        late_balance.refresh_from_db()
        self.assertEqual(early_balance.reserved_quantity, 6)
        self.assertEqual(late_balance.reserved_quantity, 0)

    def test_fefo_splits_across_lots_without_overbooking(self):
        lot1, balance1 = self._lot("A", 3, days=5)
        lot2, balance2 = self._lot("B", 5, days=10)
        self._stock_and_locations(8)

        picking = reserve_stock_for_picking(self._picking(6), user=self.user)

        allocations = list(
            PickingLotAllocation.objects.filter(
                picking_item__picking_order=picking
            ).order_by("lot__expiration_date", "id")
        )
        self.assertEqual([row.lot_id for row in allocations], [lot1.id, lot2.id])
        self.assertEqual([row.quantity for row in allocations], [3, 3])
        self.assertEqual(
            sum(row.quantity for row in allocations),
            6,
        )
        balance1.refresh_from_db()
        balance2.refresh_from_db()
        self.assertEqual(balance1.reserved_quantity, 3)
        self.assertEqual(balance2.reserved_quantity, 3)

    def test_expired_lot_is_never_reserved(self):
        expired, expired_balance = self._lot("EXP", 10, days=-1)
        fresh, fresh_balance = self._lot("FRESH", 10, days=20)
        self._stock_and_locations(20)

        picking = reserve_stock_for_picking(self._picking(5), user=self.user)

        allocation = PickingLotAllocation.objects.get(
            picking_item__picking_order=picking
        )
        self.assertEqual(allocation.lot_id, fresh.id)
        expired_balance.refresh_from_db()
        self.assertEqual(expired_balance.reserved_quantity, 0)

    def test_recalled_lot_is_never_reserved(self):
        recalled, recalled_balance = self._lot(
            "RECALL",
            10,
            days=5,
            lot_status=ProductLot.STATUS_RECALLED,
        )
        fresh, _ = self._lot("FRESH", 10, days=20)
        self._stock_and_locations(20)

        picking = reserve_stock_for_picking(self._picking(5), user=self.user)

        allocation = PickingLotAllocation.objects.get(
            picking_item__picking_order=picking
        )
        self.assertEqual(allocation.lot_id, fresh.id)
        recalled_balance.refresh_from_db()
        self.assertEqual(recalled_balance.reserved_quantity, 0)

    def test_blocked_lot_is_never_reserved(self):
        blocked, blocked_balance = self._lot(
            "BLOCK",
            10,
            days=5,
            lot_status=ProductLot.STATUS_BLOCKED,
        )
        fresh, _ = self._lot("FRESH", 10, days=20)
        self._stock_and_locations(20)

        picking = reserve_stock_for_picking(self._picking(5), user=self.user)

        allocation = PickingLotAllocation.objects.get(
            picking_item__picking_order=picking
        )
        self.assertEqual(allocation.lot_id, fresh.id)
        blocked_balance.refresh_from_db()
        self.assertEqual(blocked_balance.reserved_quantity, 0)

    def test_quarantine_balance_is_never_reserved(self):
        quarantine, quarantine_balance = self._lot(
            "QUAR",
            10,
            days=5,
            balance_status=LotBalance.STATUS_QUARANTINE,
        )
        fresh, _ = self._lot("FRESH", 10, days=20)
        self._stock_and_locations(20)

        picking = reserve_stock_for_picking(self._picking(5), user=self.user)

        allocation = PickingLotAllocation.objects.get(
            picking_item__picking_order=picking
        )
        self.assertEqual(allocation.lot_id, fresh.id)
        quarantine_balance.refresh_from_db()
        self.assertEqual(quarantine_balance.reserved_quantity, 0)

    def test_blocked_balance_is_never_reserved(self):
        blocked, blocked_balance = self._lot(
            "BAL-BLOCK",
            10,
            days=5,
            balance_status=LotBalance.STATUS_BLOCKED,
        )
        fresh, _ = self._lot("FRESH", 10, days=20)
        self._stock_and_locations(20)

        picking = reserve_stock_for_picking(self._picking(5), user=self.user)

        allocation = PickingLotAllocation.objects.get(
            picking_item__picking_order=picking
        )
        self.assertEqual(allocation.lot_id, fresh.id)
        blocked_balance.refresh_from_db()
        self.assertEqual(blocked_balance.reserved_quantity, 0)

    def test_reservation_updates_aggregate_and_lot_ledger(self):
        lot, balance = self._lot("LEDGER", 10, days=10)
        self._stock_and_locations(10)

        picking = reserve_stock_for_picking(self._picking(4), user=self.user)

        stock = Stock.objects.get(
            company=self.company,
            warehouse=self.warehouse,
            variant=self.variant,
        )
        balance.refresh_from_db()
        allocation = PickingLotAllocation.objects.get(
            picking_item__picking_order=picking
        )
        self.assertEqual(stock.reserved_stock, 4)
        self.assertEqual(balance.reserved_quantity, 4)
        self.assertEqual(allocation.status, PickingLotAllocation.STATUS_RESERVED)
        self.assertEqual(
            LotMovement.objects.filter(
                company=self.company,
                lot=lot,
                movement_type=LotMovement.TYPE_RESERVATION,
            ).count(),
            1,
        )

    def test_insufficient_eligible_lot_rolls_back_aggregate_reservation(self):
        self._lot("ONLY", 3, days=10)
        self._stock_and_locations(10)

        with self.assertRaisesMessage(
            ValidationError,
            "No hay lote elegible suficiente",
        ):
            reserve_stock_for_picking(self._picking(5), user=self.user)

        stock = Stock.objects.get(
            company=self.company,
            warehouse=self.warehouse,
            variant=self.variant,
        )
        self.assertEqual(stock.reserved_stock, 0)
        self.assertEqual(PickingItem.objects.count(), 0)
        self.assertEqual(PickingLotAllocation.objects.count(), 0)

    def test_get_pickable_stock_uses_eligible_lot_quantity(self):
        self._lot("GOOD", 4, days=10)
        self._lot("EXP", 20, days=-1)
        self._stock_and_locations(24)

        available = get_pickable_stock_for_variant(
            company=self.company,
            variant=self.variant,
        )
        self.assertEqual(available, 4)

    def test_get_pickable_stock_ignores_quarantine(self):
        self._lot(
            "QUAR",
            8,
            days=10,
            balance_status=LotBalance.STATUS_QUARANTINE,
        )
        self._stock_and_locations(8)

        available = get_pickable_stock_for_variant(
            company=self.company,
            variant=self.variant,
        )
        self.assertEqual(available, 0)

    def test_release_restores_lot_and_aggregate_availability(self):
        lot, balance = self._lot("REL", 10, days=10)
        self._stock_and_locations(10)
        picking = reserve_stock_for_picking(self._picking(4), user=self.user)

        release_reserved_stock_for_cancelled_picking(
            picking,
            user=self.user,
            reason="Cancelación de prueba LT1C",
        )

        stock = Stock.objects.get(
            company=self.company,
            warehouse=self.warehouse,
            variant=self.variant,
        )
        balance.refresh_from_db()
        allocation = PickingLotAllocation.objects.get(
            picking_item__picking_order=picking
        )
        self.assertEqual(stock.reserved_stock, 0)
        self.assertEqual(balance.reserved_quantity, 0)
        self.assertEqual(allocation.status, PickingLotAllocation.STATUS_RELEASED)
        self.assertEqual(
            LotMovement.objects.filter(
                lot=lot,
                movement_type=LotMovement.TYPE_RELEASE,
            ).count(),
            1,
        )

    @patch("accounting.services.inventory_integration.post_shipping_cost_entry")
    def test_shipping_consumes_exact_reserved_lot(self, _post_cost):
        lot, balance = self._lot("SHIP", 10, days=10)
        self._stock_and_locations(10)
        picking = reserve_stock_for_picking(self._picking(4), user=self.user)

        consume_reserved_stock_for_shipping(
            picking,
            user=self.user,
        )

        stock = Stock.objects.get(
            company=self.company,
            warehouse=self.warehouse,
            variant=self.variant,
        )
        variant_location = VariantLocation.objects.get(
            company=self.company,
            variant=self.variant,
            location=self.location_a,
        )
        balance.refresh_from_db()
        allocation = PickingLotAllocation.objects.get(
            picking_item__picking_order=picking
        )

        self.assertEqual(stock.physical_stock, 6)
        self.assertEqual(stock.reserved_stock, 0)
        self.assertEqual(variant_location.quantity, 6)
        self.assertEqual(balance.physical_quantity, 6)
        self.assertEqual(balance.reserved_quantity, 0)
        self.assertEqual(allocation.status, PickingLotAllocation.STATUS_CONSUMED)
        self.assertEqual(
            LotMovement.objects.filter(
                lot=lot,
                movement_type=LotMovement.TYPE_CONSUME,
            ).count(),
            1,
        )

    @patch("accounting.services.inventory_integration.post_shipping_cost_entry")
    def test_duplicate_shipping_cannot_consume_same_lot_twice(self, _post_cost):
        self._lot("SHIP2", 10, days=10)
        self._stock_and_locations(10)
        picking = reserve_stock_for_picking(self._picking(4), user=self.user)

        consume_reserved_stock_for_shipping(picking, user=self.user)

        with self.assertRaises(ValidationError):
            consume_reserved_stock_for_shipping(picking, user=self.user)

        stock = Stock.objects.get(
            company=self.company,
            warehouse=self.warehouse,
            variant=self.variant,
        )
        self.assertEqual(stock.physical_stock, 6)

    def test_damage_flow_blocks_controlled_sku_without_mutating_stock(self):
        lot, balance = self._lot("DMG", 10, days=10)
        self._stock_and_locations(10)
        picking = reserve_stock_for_picking(self._picking(4), user=self.user)
        item = picking.items.get()

        with self.assertRaisesMessage(
            ValidationError,
            "trazabilidad",
        ):
            report_damaged_stock_during_picking(
                item,
                quantity=1,
                reason="Daño físico",
                user=self.user,
            )

        stock = Stock.objects.get(
            company=self.company,
            warehouse=self.warehouse,
            variant=self.variant,
        )
        balance.refresh_from_db()
        self.assertEqual(stock.physical_stock, 10)
        self.assertEqual(stock.reserved_stock, 4)
        self.assertEqual(balance.physical_quantity, 10)
        self.assertEqual(balance.reserved_quantity, 4)

    def test_foreign_company_lot_never_counts(self):
        other_company = Company.objects.create(
            name="Otra LT1C",
            slug="otra-lt1c",
        )
        other_warehouse = Warehouse.objects.create(
            company=other_company,
            name="Otra bodega",
            code="OTRA-LT1C",
        )
        other_product = Product.objects.create(
            company=other_company,
            name="Otro producto",
            base_sku="OTRO-LT1C",
        )
        other_variant = ProductVariant.objects.create(
            company=other_company,
            product=other_product,
            sku="OTRO-LT1C",
            inventory_control=ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY,
        )
        other_location = WarehouseLocation.objects.create(
            company=other_company,
            warehouse=other_warehouse,
            aisle="O",
            rack="1",
            level="1",
            bin_code="O1-1",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )
        other_lot = ProductLot.objects.create(
            company=other_company,
            variant=other_variant,
            lot_number="FOREIGN",
            expiration_date=timezone.localdate() + timedelta(days=1),
        )
        LotBalance.objects.create(
            company=other_company,
            lot=other_lot,
            location=other_location,
            physical_quantity=100,
        )

        self._stock_and_locations(5)
        self._lot("LOCAL", 2, days=10)

        available = get_pickable_stock_for_variant(
            company=self.company,
            variant=self.variant,
        )
        self.assertEqual(available, 2)


    def test_two_order_lines_same_sku_accumulate_same_lot_reservation(self):
        lot, balance = self._lot("SAME", 10, days=10)
        self._stock_and_locations(10)

        order = Order.objects.create(
            company=self.company,
            customer=self.customer,
            created_by=self.user,
            status="paid",
            total=6,
            amount_paid=6,
            balance_due=0,
        )
        item1 = OrderItem.objects.create(
            order=order,
            variant=self.variant,
            quantity=3,
            unit_price=1,
        )
        item2 = OrderItem.objects.create(
            order=order,
            variant=self.variant,
            quantity=3,
            unit_price=1,
        )
        fulfillment = OrderFulfillment.objects.create(
            company=self.company,
            order=order,
            warehouse=self.warehouse,
        )
        for item in (item1, item2):
            OrderItemAllocation.objects.create(
                company=self.company,
                fulfillment=fulfillment,
                order_item=item,
                variant=self.variant,
                commercial_quantity=3,
                internal_quantity=3,
            )
        picking = PickingOrder.objects.create(
            company=self.company,
            order=order,
            fulfillment=fulfillment,
            warehouse=self.warehouse,
        )

        reserve_stock_for_picking(picking, user=self.user)

        balance.refresh_from_db()
        self.assertEqual(balance.reserved_quantity, 6)
        self.assertEqual(
            PickingLotAllocation.objects.filter(
                picking_item__picking_order=picking,
                lot=lot,
            ).count(),
            2,
        )
        self.assertEqual(
            sum(
                PickingLotAllocation.objects.filter(
                    picking_item__picking_order=picking,
                    lot=lot,
                ).values_list("quantity", flat=True)
            ),
            6,
        )


class LotWithoutExpiryFEFOTests(TestCase):
    def test_dated_lot_precedes_non_expiring_lot(self):
        company = Company.objects.create(
            name="Empresa LT1C LOT",
            slug="empresa-lt1c-lot",
        )
        customer = Customer.objects.create(
            company=company,
            full_name="Cliente LOT",
        )
        user = User.objects.create_user("lt1c-lot@test.local", password="x")
        warehouse = Warehouse.objects.create(
            company=company,
            name="Bodega LOT",
            code="LOT-LT1C",
        )
        location = WarehouseLocation.objects.create(
            company=company,
            warehouse=warehouse,
            aisle="A",
            rack="1",
            level="1",
            bin_code="A1",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )
        product = Product.objects.create(
            company=company,
            name="Producto LOT",
            base_sku="LOT-LT1C",
        )
        variant = ProductVariant.objects.create(
            company=company,
            product=product,
            sku="LOT-LT1C",
            inventory_control=ProductVariant.INVENTORY_CONTROL_LOT,
        )
        Stock.objects.create(
            company=company,
            warehouse=warehouse,
            variant=variant,
            physical_stock=10,
        )
        VariantLocation.objects.create(
            company=company,
            variant=variant,
            location=location,
            quantity=10,
        )
        no_expiry = ProductLot.objects.create(
            company=company,
            variant=variant,
            lot_number="NOEXP",
        )
        dated = ProductLot.objects.create(
            company=company,
            variant=variant,
            lot_number="DATED",
            expiration_date=timezone.localdate() + timedelta(days=20),
        )
        LotBalance.objects.create(
            company=company,
            lot=no_expiry,
            location=location,
            physical_quantity=5,
        )
        LotBalance.objects.create(
            company=company,
            lot=dated,
            location=location,
            physical_quantity=5,
        )

        order = Order.objects.create(
            company=company,
            customer=customer,
            created_by=user,
            status="paid",
            total=2,
            amount_paid=2,
            balance_due=0,
        )
        order_item = OrderItem.objects.create(
            order=order,
            variant=variant,
            quantity=2,
            unit_price=1,
        )
        fulfillment = OrderFulfillment.objects.create(
            company=company,
            order=order,
            warehouse=warehouse,
        )
        OrderItemAllocation.objects.create(
            company=company,
            fulfillment=fulfillment,
            order_item=order_item,
            variant=variant,
            commercial_quantity=2,
            internal_quantity=2,
        )
        picking = PickingOrder.objects.create(
            company=company,
            order=order,
            fulfillment=fulfillment,
            warehouse=warehouse,
        )

        reserve_stock_for_picking(picking, user=user)

        allocation = PickingLotAllocation.objects.get(
            picking_item__picking_order=picking
        )
        self.assertEqual(allocation.lot_id, dated.id)
