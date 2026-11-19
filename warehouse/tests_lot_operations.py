from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from accounts.models import UserProfile
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
    TransferLotAllocation,
    VariantLocation,
    WarehouseLocation,
    WarehouseTransfer,
    WarehouseTransferItem,
)
from warehouse.services.lot_operations import (
    dispose_quarantine_stock,
    quarantine_available_stock,
    register_customer_return_to_quarantine,
    release_quarantine_stock,
    set_product_lot_status,
)
from warehouse.services import confirm_picking_item
from warehouse.services.stock_reservation import (
    consume_reserved_stock_for_shipping,
    release_reserved_stock_for_cancelled_picking,
    report_damaged_stock_during_picking,
    reserve_stock_for_picking,
)
from warehouse.transfer_services import (
    cancel_transfer,
    complete_transfer,
    dispatch_transfer,
    receive_transfer,
    reserve_transfer,
    start_dispatch_transfer,
    start_receiving_transfer,
)


User = get_user_model()


class LotOperationsLT1ETests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Empresa LT1E",
            slug="empresa-lt1e",
        )
        self.user = User.objects.create_user(
            "lt1e-owner@test.local",
            password="x",
        )
        UserProfile.objects.create(
            user=self.user,
            company=self.company,
            role="owner",
            is_active=True,
        )
        self.origin = Warehouse.objects.create(
            company=self.company,
            name="Origen LT1E",
            code="L1EO",
        )
        self.destination = Warehouse.objects.create(
            company=self.company,
            name="Destino LT1E",
            code="L1ED",
        )
        self.origin_location = WarehouseLocation.objects.create(
            company=self.company,
            warehouse=self.origin,
            aisle="A",
            rack="1",
            level="1",
            bin_code="A1-1",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )
        self.origin_location_b = WarehouseLocation.objects.create(
            company=self.company,
            warehouse=self.origin,
            aisle="B",
            rack="1",
            level="1",
            bin_code="B1-1",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )
        self.destination_location = WarehouseLocation.objects.create(
            company=self.company,
            warehouse=self.destination,
            aisle="D",
            rack="1",
            level="1",
            bin_code="D1-1",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )
        self.product = Product.objects.create(
            company=self.company,
            name="Producto LT1E",
            base_sku="LT1E",
        )
        self.variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="LT1E-EXP",
            inventory_control=ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY,
        )
        self.customer = Customer.objects.create(
            company=self.company,
            full_name="Cliente LT1E",
        )

    def _lot(self, number, qty, *, days=30, location=None, status=None):
        location = location or self.origin_location
        lot = ProductLot.objects.create(
            company=self.company,
            variant=self.variant,
            lot_number=number,
            expiration_date=timezone.localdate() + timedelta(days=days),
            status=status or ProductLot.STATUS_ACTIVE,
        )
        balance = LotBalance.objects.create(
            company=self.company,
            lot=lot,
            location=location,
            status=LotBalance.STATUS_AVAILABLE,
            physical_quantity=qty,
            reserved_quantity=0,
            committed_quantity=0,
        )
        return lot, balance

    def _stock(self, quantity):
        stock = Stock.objects.create(
            company=self.company,
            warehouse=self.origin,
            variant=self.variant,
            physical_stock=quantity,
            reserved_stock=0,
            committed_stock=0,
        )
        VariantLocation.objects.create(
            company=self.company,
            variant=self.variant,
            location=self.origin_location,
            quantity=quantity,
        )
        return stock

    def _stock_split(self, qty_a, qty_b):
        stock = Stock.objects.create(
            company=self.company,
            warehouse=self.origin,
            variant=self.variant,
            physical_stock=qty_a + qty_b,
            reserved_stock=0,
            committed_stock=0,
        )
        VariantLocation.objects.create(
            company=self.company,
            variant=self.variant,
            location=self.origin_location,
            quantity=qty_a,
        )
        VariantLocation.objects.create(
            company=self.company,
            variant=self.variant,
            location=self.origin_location_b,
            quantity=qty_b,
        )
        return stock

    def _transfer(self, quantity):
        transfer = WarehouseTransfer.objects.create(
            company=self.company,
            folio=f"LT1E-{WarehouseTransfer.objects.count()+1}",
            origin_warehouse=self.origin,
            destination_warehouse=self.destination,
            status=WarehouseTransfer.STATUS_REQUESTED,
            priority=WarehouseTransfer.PRIORITY_NORMAL,
            reason="Prueba LT1E",
            requested_by=self.user,
        )
        item = WarehouseTransferItem.objects.create(
            company=self.company,
            transfer=transfer,
            variant=self.variant,
            requested_quantity=quantity,
        )
        return transfer, item

    def _reserve_and_dispatch(self, quantity):
        transfer, item = self._transfer(quantity)
        reserve_transfer(
            transfer_id=transfer.id,
            user=self.user,
            request_id=uuid4(),
        )
        start_dispatch_transfer(
            transfer_id=transfer.id,
            user=self.user,
            request_id=uuid4(),
        )
        dispatch_transfer(
            transfer_id=transfer.id,
            user=self.user,
            request_id=uuid4(),
        )
        start_receiving_transfer(
            transfer_id=transfer.id,
            user=self.user,
            request_id=uuid4(),
        )
        transfer.refresh_from_db()
        item.refresh_from_db()
        return transfer, item

    def _order_and_picking(self, quantity):
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
            warehouse=self.origin,
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
            warehouse=self.origin,
            assigned_to=self.user,
        )
        return order, picking

    def test_transfer_reservation_is_fefo_and_exact_by_lot(self):
        late, late_balance = self._lot("LATE", 5, days=50)
        early, early_balance = self._lot("EARLY", 3, days=5)
        self._stock(8)

        transfer, item = self._transfer(6)
        reserve_transfer(
            transfer_id=transfer.id,
            user=self.user,
            request_id=uuid4(),
        )

        allocations = list(
            TransferLotAllocation.objects.filter(
                transfer_item=item
            ).order_by("lot__expiration_date", "id")
        )
        self.assertEqual([a.lot_id for a in allocations], [early.id, late.id])
        self.assertEqual([a.quantity for a in allocations], [3, 3])
        early_balance.refresh_from_db()
        late_balance.refresh_from_db()
        self.assertEqual(early_balance.reserved_quantity, 3)
        self.assertEqual(late_balance.reserved_quantity, 3)

    def test_transfer_reservation_rejects_insufficient_eligible_lot(self):
        self._lot("ONLY", 2, days=5)
        self._stock(10)
        transfer, _item = self._transfer(5)

        with self.assertRaises(ValidationError):
            reserve_transfer(
                transfer_id=transfer.id,
                user=self.user,
                request_id=uuid4(),
            )

        self.assertEqual(TransferLotAllocation.objects.count(), 0)
        self.assertEqual(
            Stock.objects.get(
                company=self.company,
                warehouse=self.origin,
                variant=self.variant,
            ).reserved_stock,
            0,
        )

    def test_cancel_releases_exact_transfer_lot_reservation(self):
        lot, balance = self._lot("REL", 5)
        self._stock(5)
        transfer, _item = self._transfer(4)
        reserve_transfer(
            transfer_id=transfer.id,
            user=self.user,
            request_id=uuid4(),
        )

        cancel_transfer(
            transfer_id=transfer.id,
            user=self.user,
            cancellation_reason="Cancelar LT1E",
            request_id=uuid4(),
        )

        balance.refresh_from_db()
        allocation = TransferLotAllocation.objects.get(lot=lot)
        self.assertEqual(balance.reserved_quantity, 0)
        self.assertEqual(
            allocation.status,
            TransferLotAllocation.STATUS_RELEASED,
        )

    def test_dispatch_moves_exact_lot_to_in_transit(self):
        lot, balance = self._lot("OUT", 5)
        stock = self._stock(5)
        transfer, item = self._transfer(4)
        reserve_transfer(
            transfer_id=transfer.id,
            user=self.user,
            request_id=uuid4(),
        )
        start_dispatch_transfer(
            transfer_id=transfer.id,
            user=self.user,
            request_id=uuid4(),
        )
        dispatch_transfer(
            transfer_id=transfer.id,
            user=self.user,
            request_id=uuid4(),
        )

        balance.refresh_from_db()
        stock.refresh_from_db()
        allocation = TransferLotAllocation.objects.get(
            transfer_item=item,
        )
        self.assertEqual(balance.physical_quantity, 1)
        self.assertEqual(balance.reserved_quantity, 0)
        self.assertEqual(stock.physical_stock, 1)
        self.assertEqual(stock.reserved_stock, 0)
        self.assertEqual(
            allocation.status,
            TransferLotAllocation.STATUS_IN_TRANSIT,
        )

    def test_receive_conforming_preserves_same_lot(self):
        lot, _balance = self._lot("IN", 5)
        self._stock(5)
        transfer, item = self._reserve_and_dispatch(4)
        allocation = TransferLotAllocation.objects.get(
            transfer_item=item
        )

        receive_transfer(
            transfer_id=transfer.id,
            user=self.user,
            request_id=uuid4(),
            items=[
                {
                    "transfer_lot_allocation_id": allocation.id,
                    "received_quantity": 4,
                    "damaged_quantity": 0,
                    "rejected_quantity": 0,
                    "missing_quantity": 0,
                    "destination_location_id": self.destination_location.id,
                    "notes": "Conforme",
                }
            ],
        )

        allocation.refresh_from_db()
        self.assertEqual(
            allocation.status,
            TransferLotAllocation.STATUS_CLOSED,
        )
        self.assertEqual(allocation.received_quantity, 4)
        self.assertEqual(allocation.destination_balance.lot_id, lot.id)
        self.assertEqual(
            allocation.destination_balance.location_id,
            self.destination_location.id,
        )
        self.assertEqual(allocation.destination_balance.physical_quantity, 4)

    def test_transfer_damage_and_reject_enter_quarantine(self):
        lot, _balance = self._lot("QUAR-TR", 5)
        self._stock(5)
        transfer, item = self._reserve_and_dispatch(5)
        allocation = TransferLotAllocation.objects.get(
            transfer_item=item
        )

        receive_transfer(
            transfer_id=transfer.id,
            user=self.user,
            request_id=uuid4(),
            items=[
                {
                    "transfer_lot_allocation_id": allocation.id,
                    "received_quantity": 2,
                    "damaged_quantity": 1,
                    "rejected_quantity": 1,
                    "missing_quantity": 1,
                    "destination_location_id": self.destination_location.id,
                    "notes": "Diferencia",
                }
            ],
        )

        quarantine = LotBalance.objects.get(
            company=self.company,
            lot=lot,
            status=LotBalance.STATUS_QUARANTINE,
        )
        self.assertEqual(quarantine.physical_quantity, 2)
        destination_stock = Stock.objects.get(
            company=self.company,
            warehouse=self.destination,
            variant=self.variant,
        )
        self.assertEqual(destination_stock.physical_stock, 4)

    def test_transfer_missing_does_not_create_physical_stock(self):
        self._lot("MISS", 3)
        self._stock(3)
        transfer, item = self._reserve_and_dispatch(3)
        allocation = TransferLotAllocation.objects.get(
            transfer_item=item
        )

        receive_transfer(
            transfer_id=transfer.id,
            user=self.user,
            request_id=uuid4(),
            items=[
                {
                    "transfer_lot_allocation_id": allocation.id,
                    "received_quantity": 0,
                    "damaged_quantity": 0,
                    "rejected_quantity": 0,
                    "missing_quantity": 3,
                    "destination_location_id": "",
                    "notes": "Faltante total",
                }
            ],
        )

        self.assertFalse(
            Stock.objects.filter(
                company=self.company,
                warehouse=self.destination,
                variant=self.variant,
                physical_stock__gt=0,
            ).exists()
        )

    def test_recall_blocks_shipping_of_already_reserved_lot(self):
        lot, _balance = self._lot("RECALL-SHIP", 4)
        self._stock(4)
        _order, picking = self._order_and_picking(4)
        reserve_stock_for_picking(picking, user=self.user)

        set_product_lot_status(
            lot_id=lot.id,
            status=ProductLot.STATUS_RECALLED,
            reason="Recall de prueba",
            user=self.user,
            request_id=uuid4(),
        )

        with self.assertRaisesMessage(
            ValidationError,
            "envío fue detenido",
        ):
            consume_reserved_stock_for_shipping(
                picking,
                user=self.user,
            )

    def test_block_and_unblock_are_audited(self):
        lot, _balance = self._lot("BLOCK", 2)
        self._stock(2)

        set_product_lot_status(
            lot_id=lot.id,
            status=ProductLot.STATUS_BLOCKED,
            reason="Inspección",
            user=self.user,
            request_id=uuid4(),
        )
        lot.refresh_from_db()
        self.assertEqual(lot.status, ProductLot.STATUS_BLOCKED)

        set_product_lot_status(
            lot_id=lot.id,
            status=ProductLot.STATUS_ACTIVE,
            reason="Liberado",
            user=self.user,
            request_id=uuid4(),
        )
        lot.refresh_from_db()
        self.assertEqual(lot.status, ProductLot.STATUS_ACTIVE)
        self.assertTrue(
            LotMovement.objects.filter(
                lot=lot,
                movement_type=LotMovement.TYPE_UNBLOCK,
            ).exists()
        )

    @patch("accounting.services.inventory_integration.post_shipping_cost_entry")
    def test_customer_return_is_limited_to_consumed_order_quantity(
        self,
        _post_cost,
    ):
        lot, _balance = self._lot("RETURN", 3)
        self._stock(3)
        order, picking = self._order_and_picking(3)
        reserve_stock_for_picking(picking, user=self.user)
        consume_reserved_stock_for_shipping(picking, user=self.user)

        register_customer_return_to_quarantine(
            lot_id=lot.id,
            order_id=order.id,
            warehouse_id=self.destination.id,
            quantity=2,
            reason="Cliente devolvió",
            user=self.user,
            request_id=uuid4(),
        )

        with self.assertRaisesMessage(
            ValidationError,
            "supera la cantidad enviada",
        ):
            register_customer_return_to_quarantine(
                lot_id=lot.id,
                order_id=order.id,
                warehouse_id=self.destination.id,
                quantity=2,
                reason="Exceso",
                user=self.user,
                request_id=uuid4(),
            )

    @patch("accounting.services.inventory_integration.post_shipping_cost_entry")
    def test_customer_return_enters_quarantine_and_increments_physical(
        self,
        _post_cost,
    ):
        lot, _balance = self._lot("RETURN-Q", 2)
        self._stock(2)
        order, picking = self._order_and_picking(2)
        reserve_stock_for_picking(picking, user=self.user)
        consume_reserved_stock_for_shipping(picking, user=self.user)

        balance = register_customer_return_to_quarantine(
            lot_id=lot.id,
            order_id=order.id,
            warehouse_id=self.destination.id,
            quantity=1,
            reason="Empaque abierto",
            user=self.user,
            request_id=uuid4(),
        )

        self.assertEqual(balance.status, LotBalance.STATUS_QUARANTINE)
        self.assertEqual(balance.physical_quantity, 1)
        self.assertEqual(
            Stock.objects.get(
                company=self.company,
                warehouse=self.destination,
                variant=self.variant,
            ).physical_stock,
            1,
        )

    def test_local_quarantine_keeps_aggregate_physical(self):
        lot, balance = self._lot("LOCAL-Q", 5)
        stock = self._stock(5)

        quarantine_available_stock(
            balance_id=balance.id,
            quantity=2,
            reason="Inspección",
            user=self.user,
            request_id=uuid4(),
        )

        stock.refresh_from_db()
        balance.refresh_from_db()
        quarantine = LotBalance.objects.get(
            lot=lot,
            status=LotBalance.STATUS_QUARANTINE,
        )
        self.assertEqual(stock.physical_stock, 5)
        self.assertEqual(balance.physical_quantity, 3)
        self.assertEqual(quarantine.physical_quantity, 2)

    def test_release_quarantine_returns_to_pickable_balance(self):
        lot, balance = self._lot("REL-Q", 5)
        self._stock(5)
        quarantine = quarantine_available_stock(
            balance_id=balance.id,
            quantity=2,
            reason="Inspección",
            user=self.user,
            request_id=uuid4(),
        )

        release_quarantine_stock(
            balance_id=quarantine.id,
            destination_location_id=self.origin_location_b.id,
            quantity=2,
            reason="Aprobado calidad",
            user=self.user,
            request_id=uuid4(),
        )

        target = LotBalance.objects.get(
            lot=lot,
            location=self.origin_location_b,
        )
        quarantine.refresh_from_db()
        self.assertEqual(target.physical_quantity, 2)
        self.assertEqual(quarantine.physical_quantity, 0)

    def test_recalled_lot_cannot_leave_quarantine(self):
        lot, balance = self._lot("NO-REL-Q", 3)
        self._stock(3)
        quarantine = quarantine_available_stock(
            balance_id=balance.id,
            quantity=1,
            reason="Inspección",
            user=self.user,
            request_id=uuid4(),
        )
        set_product_lot_status(
            lot_id=lot.id,
            status=ProductLot.STATUS_RECALLED,
            reason="Recall",
            user=self.user,
            request_id=uuid4(),
        )

        with self.assertRaisesMessage(
            ValidationError,
            "no puede liberarse",
        ):
            release_quarantine_stock(
                balance_id=quarantine.id,
                destination_location_id=self.origin_location_b.id,
                quantity=1,
                reason="Intento",
                user=self.user,
                request_id=uuid4(),
            )

    def test_waste_from_quarantine_decrements_aggregate_physical(self):
        lot, balance = self._lot("WASTE-Q", 4)
        stock = self._stock(4)
        quarantine = quarantine_available_stock(
            balance_id=balance.id,
            quantity=2,
            reason="Inspección",
            user=self.user,
            request_id=uuid4(),
        )

        dispose_quarantine_stock(
            balance_id=quarantine.id,
            quantity=1,
            reason="Desecho",
            user=self.user,
            request_id=uuid4(),
        )

        stock.refresh_from_db()
        quarantine.refresh_from_db()
        self.assertEqual(stock.physical_stock, 3)
        self.assertEqual(quarantine.physical_quantity, 1)

    def test_controlled_damage_creates_replacement_and_preserves_history(self):
        damaged_lot, damaged_balance = self._lot(
            "DAMAGE-A",
            2,
            days=5,
            location=self.origin_location,
        )
        replacement_lot, replacement_balance = self._lot(
            "DAMAGE-B",
            3,
            days=20,
            location=self.origin_location_b,
        )
        self._stock_split(2, 3)
        _order, picking = self._order_and_picking(2)
        reserve_stock_for_picking(picking, user=self.user)

        original_item = PickingItem.objects.get(
            picking_order=picking,
            lot_allocation__lot=damaged_lot,
        )

        result = report_damaged_stock_during_picking(
            picking_item=original_item,
            quantity=2,
            reason="Caja rota",
            user=self.user,
        )

        original_allocation = PickingLotAllocation.objects.get(
            picking_item=original_item,
        )
        self.assertEqual(
            original_allocation.status,
            PickingLotAllocation.STATUS_DAMAGED,
        )
        self.assertTrue(result["reassigned"])
        self.assertTrue(
            PickingLotAllocation.objects.filter(
                picking_item__picking_order=picking,
                lot=replacement_lot,
                status=PickingLotAllocation.STATUS_RESERVED,
            ).exists()
        )
        damaged_balance.refresh_from_db()
        replacement_balance.refresh_from_db()
        self.assertEqual(damaged_balance.physical_quantity, 0)
        self.assertEqual(replacement_balance.reserved_quantity, 2)

    def test_controlled_damage_replacement_can_complete_picking(self):
        damaged_lot, _damaged_balance = self._lot(
            "DAMAGE-COMPLETE-A",
            2,
            days=5,
            location=self.origin_location,
        )
        replacement_lot, _replacement_balance = self._lot(
            "DAMAGE-COMPLETE-B",
            3,
            days=20,
            location=self.origin_location_b,
        )
        self._stock_split(2, 3)
        _order, picking = self._order_and_picking(2)
        reserve_stock_for_picking(picking, user=self.user)

        original_item = PickingItem.objects.get(
            picking_order=picking,
            lot_allocation__lot=damaged_lot,
        )
        report_damaged_stock_during_picking(
            picking_item=original_item,
            quantity=2,
            reason="Caja rota",
            user=self.user,
        )

        original_item.refresh_from_db()
        self.assertEqual(
            original_item.status,
            PickingItem.STATUS_DAMAGED_RESOLVED,
        )

        with self.assertRaisesMessage(
            ValidationError,
            "historial de un lote dañado",
        ):
            confirm_picking_item(
                original_item,
                user=self.user,
                scanned_code=original_item.variant.sku,
            )

        replacements = list(
            PickingItem.objects.filter(
                picking_order=picking,
                lot_allocation__lot=replacement_lot,
            ).order_by("id")
        )
        self.assertTrue(replacements)

        for replacement in replacements:
            confirm_picking_item(
                replacement,
                user=self.user,
                scanned_code=replacement.variant.sku,
            )

        picking.refresh_from_db()
        self.assertEqual(
            picking.status,
            PickingOrder.STATUS_COMPLETED,
        )
        self.assertFalse(
            picking.items.exclude(
                status__in=[
                    PickingItem.STATUS_PICKED,
                    PickingItem.STATUS_DAMAGED_RESOLVED,
                ]
            ).exists()
        )

    def test_cancel_after_controlled_damage_releases_only_replacement(self):
        damaged_lot, damaged_balance = self._lot(
            "DAMAGE-CANCEL-A",
            2,
            days=5,
            location=self.origin_location,
        )
        replacement_lot, replacement_balance = self._lot(
            "DAMAGE-CANCEL-B",
            3,
            days=20,
            location=self.origin_location_b,
        )
        stock = self._stock_split(2, 3)
        _order, picking = self._order_and_picking(2)
        reserve_stock_for_picking(picking, user=self.user)

        original_item = PickingItem.objects.get(
            picking_order=picking,
            lot_allocation__lot=damaged_lot,
        )
        report_damaged_stock_during_picking(
            picking_item=original_item,
            quantity=2,
            reason="Caja rota",
            user=self.user,
        )

        replacement_item = PickingItem.objects.get(
            picking_order=picking,
            lot_allocation__lot=replacement_lot,
        )
        replacement_allocation = PickingLotAllocation.objects.get(
            picking_item=replacement_item,
        )
        original_allocation = PickingLotAllocation.objects.get(
            picking_item=original_item,
        )

        release_reserved_stock_for_cancelled_picking(
            picking,
            user=self.user,
            reason="Cancelación posterior a daño",
        )

        stock.refresh_from_db()
        damaged_balance.refresh_from_db()
        replacement_balance.refresh_from_db()
        original_allocation.refresh_from_db()
        replacement_allocation.refresh_from_db()
        picking.refresh_from_db()

        self.assertEqual(stock.reserved_stock, 0)
        self.assertEqual(damaged_balance.physical_quantity, 0)
        self.assertEqual(damaged_balance.reserved_quantity, 0)
        self.assertEqual(replacement_balance.reserved_quantity, 0)
        self.assertEqual(
            original_allocation.status,
            PickingLotAllocation.STATUS_DAMAGED,
        )
        self.assertEqual(
            replacement_allocation.status,
            PickingLotAllocation.STATUS_RELEASED,
        )
        self.assertEqual(
            picking.status,
            PickingOrder.STATUS_CANCELLED,
        )

    def test_partial_damage_of_controlled_picking_item_is_rejected(self):
        self._lot("PARTIAL", 3)
        self._stock(3)
        _order, picking = self._order_and_picking(3)
        reserve_stock_for_picking(picking, user=self.user)
        item = PickingItem.objects.get(picking_order=picking)

        with self.assertRaisesMessage(
            ValidationError,
            "cantidad completa",
        ):
            report_damaged_stock_during_picking(
                picking_item=item,
                quantity=1,
                reason="Parcial",
                user=self.user,
            )

    def test_cross_company_lot_status_change_is_rejected(self):
        other = Company.objects.create(
            name="Otra LT1E",
            slug="otra-lt1e",
        )
        other_user = User.objects.create_user(
            "other-lt1e@test.local",
            password="x",
        )
        UserProfile.objects.create(
            user=other_user,
            company=other,
            role="owner",
            is_active=True,
        )
        lot, _balance = self._lot("OTHER", 1)
        self._stock(1)

        with self.assertRaises(PermissionDenied):
            set_product_lot_status(
                lot_id=lot.id,
                status=ProductLot.STATUS_RECALLED,
                reason="No autorizado",
                user=other_user,
                request_id=uuid4(),
            )
