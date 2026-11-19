from datetime import timedelta

from django.contrib.auth import get_user_model
from django.template.loader import get_template
from django.test import TestCase
from django.utils import timezone

from companies.models import Company
from crm.models import Customer
from inventory.models import (
    LotBalance,
    Product,
    ProductLot,
    ProductVariant,
    Warehouse,
)
from sales.models import Order, OrderItem
from warehouse.models import (
    PickingItem,
    PickingLotAllocation,
    PickingOrder,
    WarehouseLocation,
)
from warehouse.services.lot_traceability import (
    get_lot_impact,
    get_order_lot_traceability,
    load_picking_items_with_lot_traceability,
    lot_is_visible,
    search_visible_lots,
)


User = get_user_model()


class LotTraceabilityLT1DTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Empresa LT1D",
            slug="empresa-lt1d",
        )
        self.other_company = Company.objects.create(
            name="Empresa LT1D Otra",
            slug="empresa-lt1d-otra",
        )
        self.user = User.objects.create_user(
            "lt1d-owner@test.local",
            password="x",
        )
        self.customer = Customer.objects.create(
            company=self.company,
            full_name="Cliente LT1D",
        )
        self.warehouse_a = Warehouse.objects.create(
            company=self.company,
            name="Bodega LT1D A",
            code="L1DA",
        )
        self.warehouse_b = Warehouse.objects.create(
            company=self.company,
            name="Bodega LT1D B",
            code="L1DB",
        )
        self.other_warehouse = Warehouse.objects.create(
            company=self.other_company,
            name="Bodega LT1D Otra",
            code="L1DX",
        )
        self.location_a = WarehouseLocation.objects.create(
            company=self.company,
            warehouse=self.warehouse_a,
            aisle="A",
            rack="1",
            level="1",
            bin_code="LT1D-A1",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )
        self.location_b = WarehouseLocation.objects.create(
            company=self.company,
            warehouse=self.warehouse_b,
            aisle="B",
            rack="1",
            level="1",
            bin_code="LT1D-B1",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )
        self.other_location = WarehouseLocation.objects.create(
            company=self.other_company,
            warehouse=self.other_warehouse,
            aisle="X",
            rack="1",
            level="1",
            bin_code="LT1D-X1",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )
        self.product = Product.objects.create(
            company=self.company,
            name="Producto trazable LT1D",
            base_sku="LT1D",
        )
        self.variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="LT1D-LOT",
            inventory_control=ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY,
        )
        self.normal_variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="LT1D-NORMAL",
            inventory_control=ProductVariant.INVENTORY_CONTROL_NORMAL,
        )
        self.other_product = Product.objects.create(
            company=self.other_company,
            name="Producto otra empresa",
            base_sku="LT1DX",
        )
        self.other_variant = ProductVariant.objects.create(
            company=self.other_company,
            product=self.other_product,
            sku="LT1DX-LOT",
            inventory_control=ProductVariant.INVENTORY_CONTROL_LOT,
        )

    def _lot(self, number="TRACE-001", location=None, qty=10):
        location = location or self.location_a
        lot = ProductLot.objects.create(
            company=self.company,
            variant=self.variant,
            lot_number=number,
            expiration_date=timezone.localdate() + timedelta(days=30),
        )
        balance = LotBalance.objects.create(
            company=self.company,
            lot=lot,
            location=location,
            physical_quantity=qty,
            reserved_quantity=0,
            committed_quantity=0,
        )
        return lot, balance

    def _allocation(
        self,
        *,
        warehouse=None,
        location=None,
        lot=None,
        balance=None,
        status=PickingLotAllocation.STATUS_RESERVED,
        quantity=4,
    ):
        warehouse = warehouse or self.warehouse_a
        location = location or self.location_a

        if lot is None or balance is None:
            lot, balance = self._lot(
                number=f"TRACE-{warehouse.code}",
                location=location,
                qty=max(quantity, 10),
            )

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
        picking = PickingOrder.objects.create(
            company=self.company,
            order=order,
            warehouse=warehouse,
            assigned_to=self.user,
        )
        item = PickingItem.objects.create(
            picking_order=picking,
            order_item=order_item,
            variant=self.variant,
            location=location,
            required_quantity=quantity,
        )
        allocation = PickingLotAllocation.objects.create(
            company=self.company,
            picking_item=item,
            lot=lot,
            balance=balance,
            quantity=quantity,
            status=status,
        )
        return order, picking, item, allocation

    def test_picking_items_expose_exact_lot_allocation(self):
        lot, balance = self._lot()
        _, picking, item, allocation = self._allocation(
            lot=lot,
            balance=balance,
        )

        rows = load_picking_items_with_lot_traceability(
            picking_order=picking,
            company=self.company,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].trace_allocation.pk, allocation.pk)
        self.assertEqual(rows[0].trace_allocation.lot_id, lot.pk)

    def test_normal_picking_item_has_no_trace_allocation(self):
        order = Order.objects.create(
            company=self.company,
            customer=self.customer,
            created_by=self.user,
            status="paid",
            total=1,
            amount_paid=1,
            balance_due=0,
        )
        order_item = OrderItem.objects.create(
            order=order,
            variant=self.normal_variant,
            quantity=1,
            unit_price=1,
        )
        picking = PickingOrder.objects.create(
            company=self.company,
            order=order,
            warehouse=self.warehouse_a,
        )
        PickingItem.objects.create(
            picking_order=picking,
            order_item=order_item,
            variant=self.normal_variant,
            location=self.location_a,
            required_quantity=1,
        )

        rows = load_picking_items_with_lot_traceability(
            picking_order=picking,
            company=self.company,
        )

        self.assertIsNone(rows[0].trace_allocation)

    def test_consumed_allocation_remains_historically_visible(self):
        order, _, _, allocation = self._allocation(
            status=PickingLotAllocation.STATUS_CONSUMED,
        )

        rows = get_order_lot_traceability(
            company=self.company,
            order=order,
            warehouses=[self.warehouse_a],
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0].status,
            PickingLotAllocation.STATUS_CONSUMED,
        )

    def test_released_allocation_remains_historically_visible(self):
        order, _, _, allocation = self._allocation(
            status=PickingLotAllocation.STATUS_RELEASED,
        )

        rows = get_order_lot_traceability(
            company=self.company,
            order=order,
            warehouses=[self.warehouse_a],
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0].status,
            PickingLotAllocation.STATUS_RELEASED,
        )

    def test_order_traceability_respects_warehouse_scope(self):
        order_a, _, _, _ = self._allocation(
            warehouse=self.warehouse_a,
            location=self.location_a,
        )

        rows = get_order_lot_traceability(
            company=self.company,
            order=order_a,
            warehouses=[self.warehouse_b],
        )

        self.assertEqual(rows, [])

    def test_visible_lot_search_matches_lot_number(self):
        lot, _ = self._lot(number="SEARCH-LOT-777")

        rows = list(
            search_visible_lots(
                company=self.company,
                warehouses=[self.warehouse_a],
                query="777",
            )
        )

        self.assertEqual([row.pk for row in rows], [lot.pk])

    def test_visible_lot_search_matches_sku(self):
        lot, _ = self._lot(number="SKU-SEARCH")

        rows = list(
            search_visible_lots(
                company=self.company,
                warehouses=[self.warehouse_a],
                query="LT1D-LOT",
            )
        )

        self.assertIn(lot, rows)

    def test_visible_lot_search_excludes_other_company(self):
        other_lot = ProductLot.objects.create(
            company=self.other_company,
            variant=self.other_variant,
            lot_number="FOREIGN-TRACE",
        )
        LotBalance.objects.create(
            company=self.other_company,
            lot=other_lot,
            location=self.other_location,
            physical_quantity=10,
        )

        rows = list(
            search_visible_lots(
                company=self.company,
                warehouses=[self.warehouse_a],
                query="FOREIGN",
            )
        )

        self.assertEqual(rows, [])

    def test_lot_visibility_requires_authorized_warehouse(self):
        lot, _ = self._lot(
            number="WH-SCOPE",
            location=self.location_a,
        )

        self.assertTrue(
            lot_is_visible(
                company=self.company,
                lot=lot,
                warehouses=[self.warehouse_a],
            )
        )
        self.assertFalse(
            lot_is_visible(
                company=self.company,
                lot=lot,
                warehouses=[self.warehouse_b],
            )
        )

    def test_lot_impact_identifies_order_and_customer(self):
        lot, balance = self._lot(number="IMPACT")
        order, _, _, _ = self._allocation(
            lot=lot,
            balance=balance,
            status=PickingLotAllocation.STATUS_CONSUMED,
        )

        impact = get_lot_impact(
            company=self.company,
            lot=lot,
            warehouses=[self.warehouse_a],
        )

        self.assertEqual([row.pk for row in impact["orders"]], [order.pk])
        self.assertEqual(
            impact["orders"][0].customer_id,
            self.customer.pk,
        )

    def test_lot_impact_summarizes_allocation_statuses(self):
        lot, balance = self._lot(number="SUMMARY", qty=30)
        self._allocation(
            lot=lot,
            balance=balance,
            status=PickingLotAllocation.STATUS_RESERVED,
            quantity=3,
        )
        self._allocation(
            lot=lot,
            balance=balance,
            status=PickingLotAllocation.STATUS_RELEASED,
            quantity=4,
        )
        self._allocation(
            lot=lot,
            balance=balance,
            status=PickingLotAllocation.STATUS_CONSUMED,
            quantity=5,
        )

        impact = get_lot_impact(
            company=self.company,
            lot=lot,
            warehouses=[self.warehouse_a],
        )

        self.assertEqual(impact["summary"]["allocation_reserved"], 3)
        self.assertEqual(impact["summary"]["allocation_released"], 4)
        self.assertEqual(impact["summary"]["allocation_consumed"], 5)

    def test_recalled_status_is_available_to_traceability(self):
        lot, _ = self._lot(number="RECALL-TRACE")
        lot.status = ProductLot.STATUS_RECALLED
        lot.status_reason = "Recall de prueba LT1D"
        lot.save(update_fields=["status", "status_reason"])

        rows = list(
            search_visible_lots(
                company=self.company,
                warehouses=[self.warehouse_a],
                query="RECALL-TRACE",
            )
        )

        self.assertEqual(rows[0].status, ProductLot.STATUS_RECALLED)

    def test_lt1d_templates_compile(self):
        for template_name in [
            "warehouse/detalle_picking.html",
            "warehouse/modo_pickeo.html",
            "warehouse/detalle_packing.html",
            "warehouse/detalle_shipping.html",
            "warehouse/timeline_pedido.html",
            "warehouse/lot_traceability_list.html",
            "warehouse/lot_traceability_detail.html",
        ]:
            self.assertIsNotNone(get_template(template_name))
