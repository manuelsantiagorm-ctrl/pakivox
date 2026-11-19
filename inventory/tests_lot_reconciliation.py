from __future__ import annotations

import queue
import threading
from datetime import timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection, connections
from django.db.models import Sum
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company
from crm.models import Customer
from inventory.models import (
    LotBalance,
    LotMovement,
    LotReconciliationRun,
    Product,
    ProductLot,
    ProductVariant,
    Stock,
    Warehouse,
)
from inventory.reconciliation_services import (
    repair_lot_aggregate_compatibility,
    scan_lot_inventory,
    validate_postgres_lot_indexes,
)
from sales.models import Order, OrderItem
from warehouse.models import (
    OrderFulfillment,
    OrderItemAllocation,
    PickingLotAllocation,
    PickingOrder,
    TransferLotAllocation,
    VariantLocation,
    WarehouseLocation,
    WarehouseTransfer,
    WarehouseTransferItem,
)
from warehouse.services.stock_reservation import reserve_stock_for_picking
from warehouse.transfer_services import reserve_transfer


User = get_user_model()


class LotReconciliationLT1GTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="Empresa LT1G",
            slug="empresa-lt1g",
        )
        self.user = User.objects.create_user(
            "lt1g@test.local",
            password="x",
        )
        UserProfile.objects.create(
            user=self.user,
            company=self.company,
            role="owner",
            is_active=True,
        )
        self.warehouse = Warehouse.objects.create(
            company=self.company,
            name="Bodega LT1G",
            code="LT1G",
        )
        self.location = WarehouseLocation.objects.create(
            company=self.company,
            warehouse=self.warehouse,
            aisle="A",
            rack="1",
            level="1",
            bin_code="A1",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )
        self.product = Product.objects.create(
            company=self.company,
            name="Producto LT1G",
            base_sku="LT1G",
        )
        self.variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="LT1G-EXP",
            inventory_control=ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY,
        )
        self.lot = ProductLot.objects.create(
            company=self.company,
            variant=self.variant,
            lot_number="LT1G-LOT",
            expiration_date=timezone.localdate() + timedelta(days=90),
        )
        self.balance = LotBalance.objects.create(
            company=self.company,
            lot=self.lot,
            location=self.location,
            status=LotBalance.STATUS_AVAILABLE,
            physical_quantity=10,
            reserved_quantity=0,
            committed_quantity=0,
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
        LotMovement.objects.create(
            company=self.company,
            lot=self.lot,
            balance=self.balance,
            movement_type=LotMovement.TYPE_RECEIPT,
            quantity=10,
            reference_type="lt1g_fixture",
            reference_id=self.balance.id,
            event_key=f"lt1g-fixture:{self.balance.id}",
            physical_before=0,
            physical_after=10,
            reserved_before=0,
            reserved_after=0,
            committed_before=0,
            committed_after=0,
        )

    def test_clean_reconciliation_matches_all_aggregates(self):
        report = scan_lot_inventory(company=self.company)
        self.assertTrue(report["summary"]["clean"])
        self.assertEqual(
            report["aggregates"]["stock_mismatch_count"],
            0,
        )
        self.assertEqual(
            report["aggregates"]["location_mismatch_count"],
            0,
        )

    def test_stock_mismatch_is_detected(self):
        Stock.objects.filter(pk=self.stock.pk).update(
            physical_stock=9,
        )
        report = scan_lot_inventory(company=self.company)
        self.assertEqual(
            report["aggregates"]["stock_mismatch_count"],
            1,
        )
        row = report["aggregates"]["stock_mismatches"][0]
        self.assertTrue(row["repairable"])
        self.assertEqual(row["lot_balance"]["physical"], 10)

    def test_location_mismatch_is_detected(self):
        VariantLocation.objects.filter(
            pk=self.variant_location.pk
        ).update(quantity=8)
        report = scan_lot_inventory(company=self.company)
        self.assertEqual(
            report["aggregates"]["location_mismatch_count"],
            1,
        )

    def test_missing_stock_row_with_lot_detail_is_repairable(self):
        self.stock.delete()
        report = scan_lot_inventory(company=self.company)
        self.assertEqual(
            report["aggregates"]["stock_mismatch_count"],
            1,
        )
        self.assertTrue(
            report["aggregates"]["stock_mismatches"][0]["repairable"]
        )

    def test_missing_variant_location_with_lot_detail_is_repairable(self):
        self.variant_location.delete()
        report = scan_lot_inventory(company=self.company)
        self.assertEqual(
            report["aggregates"]["location_mismatch_count"],
            1,
        )
        self.assertTrue(
            report["aggregates"]["location_mismatches"][0]["repairable"]
        )

    def test_unclassified_controlled_stock_is_blocker_not_auto_repair(self):
        product = Product.objects.create(
            company=self.company,
            name="Sin clasificar LT1G",
            base_sku="LT1G-U",
        )
        variant = ProductVariant.objects.create(
            company=self.company,
            product=product,
            sku="LT1G-U",
            inventory_control=ProductVariant.INVENTORY_CONTROL_LOT,
        )
        Stock.objects.create(
            company=self.company,
            warehouse=self.warehouse,
            variant=variant,
            physical_stock=5,
            reserved_stock=0,
            committed_stock=0,
        )

        report = scan_lot_inventory(company=self.company)
        self.assertEqual(
            report["aggregates"]["unclassified_controlled_stock_count"],
            1,
        )
        with self.assertRaisesMessage(
            ValidationError,
            "no es seguro",
        ):
            repair_lot_aggregate_compatibility(
                company=self.company,
            )

        self.assertEqual(
            Stock.objects.get(
                company=self.company,
                warehouse=self.warehouse,
                variant=variant,
            ).physical_stock,
            5,
        )

    def test_normal_sku_is_not_reconciled_against_lot_balance(self):
        product = Product.objects.create(
            company=self.company,
            name="Normal LT1G",
            base_sku="LT1G-N",
        )
        normal = ProductVariant.objects.create(
            company=self.company,
            product=product,
            sku="LT1G-N",
            inventory_control=ProductVariant.INVENTORY_CONTROL_NORMAL,
        )
        Stock.objects.create(
            company=self.company,
            warehouse=self.warehouse,
            variant=normal,
            physical_stock=99,
            reserved_stock=0,
            committed_stock=0,
        )

        report = scan_lot_inventory(company=self.company)
        self.assertEqual(
            report["summary"]["controlled_variants"],
            1,
        )
        self.assertTrue(report["summary"]["clean"])

    def test_company_scope_does_not_include_other_tenant_issues(self):
        other = Company.objects.create(
            name="Otra LT1G",
            slug="otra-lt1g",
        )
        other_wh = Warehouse.objects.create(
            company=other,
            name="Otra",
            code="OT-LT1G",
        )
        other_product = Product.objects.create(
            company=other,
            name="Otro",
            base_sku="OT-LT1G",
        )
        other_variant = ProductVariant.objects.create(
            company=other,
            product=other_product,
            sku="OT-LT1G",
            inventory_control=ProductVariant.INVENTORY_CONTROL_LOT,
        )
        Stock.objects.create(
            company=other,
            warehouse=other_wh,
            variant=other_variant,
            physical_stock=50,
            reserved_stock=0,
            committed_stock=0,
        )

        report = scan_lot_inventory(company=self.company)
        self.assertTrue(report["summary"]["clean"])
        self.assertEqual(
            report["scope"]["company_id"],
            self.company.id,
        )

    def test_cross_company_lot_balance_is_reported_as_structural(self):
        other = Company.objects.create(
            name="Otra tenant LT1G",
            slug="otra-tenant-lt1g",
        )
        other_product = Product.objects.create(
            company=other,
            name="Producto ajeno",
            base_sku="AJ-LT1G",
        )
        other_variant = ProductVariant.objects.create(
            company=other,
            product=other_product,
            sku="AJ-LT1G",
            inventory_control=ProductVariant.INVENTORY_CONTROL_LOT,
        )
        other_lot = ProductLot.objects.create(
            company=other,
            variant=other_variant,
            lot_number="AJENO",
        )
        LotBalance.objects.create(
            company=self.company,
            lot=other_lot,
            location=self.location,
            status=LotBalance.STATUS_AVAILABLE,
            physical_quantity=1,
        )

        report = scan_lot_inventory(company=self.company)
        self.assertGreater(
            report["structural"]["counts"][
                "lot_balance_tenant_mismatches"
            ],
            0,
        )
        self.assertGreater(report["summary"]["blockers"], 0)

    def test_movement_tenant_mismatch_is_reported(self):
        other = Company.objects.create(
            name="Otra movement LT1G",
            slug="otra-movement-lt1g",
        )
        LotMovement.objects.create(
            company=other,
            lot=self.lot,
            balance=self.balance,
            movement_type=LotMovement.TYPE_ADJUSTMENT,
            quantity=0,
            reference_type="bad",
        )

        report = scan_lot_inventory(company=None)
        self.assertGreater(
            report["structural"]["counts"][
                "lot_movement_tenant_mismatches"
            ],
            0,
        )

    def test_ledger_tail_mismatch_detects_unledgered_change(self):
        LotBalance.objects.filter(pk=self.balance.pk).update(
            physical_quantity=9,
        )
        Stock.objects.filter(pk=self.stock.pk).update(
            physical_stock=9,
        )
        VariantLocation.objects.filter(
            pk=self.variant_location.pk
        ).update(quantity=9)

        report = scan_lot_inventory(company=self.company)
        self.assertEqual(
            report["ledger"]["ledger_tail_mismatch_count"],
            1,
        )
        self.assertEqual(
            report["aggregates"]["stock_mismatch_count"],
            0,
        )

    def test_apply_repairs_stock_from_lot_balance(self):
        Stock.objects.filter(pk=self.stock.pk).update(
            physical_stock=7,
        )
        result = repair_lot_aggregate_compatibility(
            company=self.company,
        )
        self.stock.refresh_from_db()
        self.assertEqual(self.stock.physical_stock, 10)
        self.assertEqual(result["repairs_applied"], 1)
        self.assertTrue(result["after"]["summary"]["clean"])

    def test_apply_repairs_variant_location_from_lot_balance(self):
        VariantLocation.objects.filter(
            pk=self.variant_location.pk
        ).update(quantity=3)
        result = repair_lot_aggregate_compatibility(
            company=self.company,
        )
        self.variant_location.refresh_from_db()
        self.assertEqual(self.variant_location.quantity, 10)
        self.assertEqual(result["repairs_applied"], 1)

    def test_apply_creates_missing_compatibility_rows(self):
        self.stock.delete()
        self.variant_location.delete()

        result = repair_lot_aggregate_compatibility(
            company=self.company,
        )

        stock = Stock.objects.get(
            company=self.company,
            warehouse=self.warehouse,
            variant=self.variant,
        )
        location = VariantLocation.objects.get(
            company=self.company,
            location=self.location,
            variant=self.variant,
        )
        self.assertEqual(stock.physical_stock, 10)
        self.assertEqual(location.quantity, 10)
        self.assertEqual(result["repairs_applied"], 2)

    def test_apply_refuses_structural_tenant_mismatch(self):
        other = Company.objects.create(
            name="Bloqueo LT1G",
            slug="bloqueo-lt1g",
        )
        other_product = Product.objects.create(
            company=other,
            name="Bloqueo producto",
            base_sku="BL-LT1G",
        )
        other_variant = ProductVariant.objects.create(
            company=other,
            product=other_product,
            sku="BL-LT1G",
            inventory_control=ProductVariant.INVENTORY_CONTROL_LOT,
        )
        other_lot = ProductLot.objects.create(
            company=other,
            variant=other_variant,
            lot_number="BLK",
        )
        LotBalance.objects.create(
            company=self.company,
            lot=other_lot,
            location=self.location,
            physical_quantity=1,
        )

        with self.assertRaises(ValidationError):
            repair_lot_aggregate_compatibility(
                company=self.company,
            )

    def test_apply_is_idempotent_after_successful_repair(self):
        Stock.objects.filter(pk=self.stock.pk).update(
            physical_stock=8,
        )
        VariantLocation.objects.filter(
            pk=self.variant_location.pk
        ).update(quantity=8)

        first = repair_lot_aggregate_compatibility(
            company=self.company,
        )
        second = repair_lot_aggregate_compatibility(
            company=self.company,
        )

        self.assertEqual(first["repairs_applied"], 2)
        self.assertEqual(second["repairs_applied"], 0)
        self.assertTrue(second["after"]["summary"]["clean"])

    def test_cross_company_stock_row_is_structural_blocker(self):
        other = Company.objects.create(
            name="Stock ajeno LT1G",
            slug="stock-ajeno-lt1g",
        )
        other_wh = Warehouse.objects.create(
            company=other,
            name="Bodega ajena",
            code="ST-AJ",
        )
        Stock.objects.create(
            company=self.company,
            warehouse=other_wh,
            variant=self.variant,
            physical_stock=0,
            reserved_stock=0,
            committed_stock=0,
        )

        report = scan_lot_inventory(company=self.company)
        self.assertGreater(
            report["structural"]["counts"]["stock_tenant_mismatches"],
            0,
        )
        self.assertGreater(report["summary"]["blockers"], 0)

    def test_cross_company_variant_location_is_structural_blocker(self):
        other = Company.objects.create(
            name="Location ajena LT1G",
            slug="location-ajena-lt1g",
        )
        other_wh = Warehouse.objects.create(
            company=other,
            name="Bodega location ajena",
            code="LOC-AJ",
        )
        other_location = WarehouseLocation.objects.create(
            company=other,
            warehouse=other_wh,
            aisle="Z",
            rack="1",
            level="1",
            bin_code="Z1",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )
        VariantLocation.objects.create(
            company=self.company,
            variant=self.variant,
            location=other_location,
            quantity=0,
        )

        report = scan_lot_inventory(company=self.company)
        self.assertGreater(
            report["structural"]["counts"][
                "variant_location_tenant_mismatches"
            ],
            0,
        )
        self.assertGreater(report["summary"]["blockers"], 0)

    def test_apply_refuses_ledger_tail_mismatch(self):
        LotBalance.objects.filter(pk=self.balance.pk).update(
            physical_quantity=9,
        )
        Stock.objects.filter(pk=self.stock.pk).update(
            physical_stock=9,
        )
        VariantLocation.objects.filter(
            pk=self.variant_location.pk
        ).update(quantity=9)

        with self.assertRaisesMessage(
            ValidationError,
            "ledger sospechoso",
        ):
            repair_lot_aggregate_compatibility(
                company=self.company,
            )

    def test_management_command_dry_run_does_not_repair_inventory(self):
        Stock.objects.filter(pk=self.stock.pk).update(
            physical_stock=9,
        )

        call_command(
            "reconcile_lot_inventory",
            company_id=self.company.id,
            verbosity=0,
        )

        self.stock.refresh_from_db()
        self.assertEqual(self.stock.physical_stock, 9)
        run = LotReconciliationRun.objects.latest("id")
        self.assertEqual(
            run.mode,
            LotReconciliationRun.MODE_DRY_RUN,
        )
        self.assertEqual(
            run.status,
            LotReconciliationRun.STATUS_ISSUES,
        )

    def test_management_command_apply_requires_company_id(self):
        with self.assertRaises(CommandError):
            call_command(
                "reconcile_lot_inventory",
                apply=True,
                verbosity=0,
            )

    def test_reconciliation_run_is_persisted_for_clean_dry_run(self):
        call_command(
            "reconcile_lot_inventory",
            company_id=self.company.id,
            verbosity=0,
        )

        run = LotReconciliationRun.objects.get()
        self.assertEqual(run.company_id, self.company.id)
        self.assertEqual(
            run.status,
            LotReconciliationRun.STATUS_CLEAN,
        )
        self.assertEqual(run.issues_found, 0)
        self.assertIsNotNone(run.finished_at)
        self.assertEqual(
            run.report["summary"]["controlled_variants"],
            1,
        )

    def test_expected_postgresql_lot_indexes_are_present(self):
        result = validate_postgres_lot_indexes()
        self.assertEqual(connection.vendor, "postgresql")
        self.assertTrue(result["checked"])
        self.assertEqual(result["missing"], [])


class LotConcurrencyLT1GTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.company = Company.objects.create(
            name="Empresa LT1G Concurrente",
            slug="empresa-lt1g-concurrente",
        )
        self.user = User.objects.create_user(
            "lt1g-concurrency@test.local",
            password="x",
        )
        UserProfile.objects.create(
            user=self.user,
            company=self.company,
            role="owner",
            is_active=True,
        )
        self.customer = Customer.objects.create(
            company=self.company,
            full_name="Cliente LT1G Concurrente",
        )
        self.origin = Warehouse.objects.create(
            company=self.company,
            name="Origen LT1G C",
            code="LT1GC-O",
        )
        self.destination = Warehouse.objects.create(
            company=self.company,
            name="Destino LT1G C",
            code="LT1GC-D",
        )
        self.location = WarehouseLocation.objects.create(
            company=self.company,
            warehouse=self.origin,
            aisle="A",
            rack="1",
            level="1",
            bin_code="A1",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )
        self.product = Product.objects.create(
            company=self.company,
            name="Producto LT1G C",
            base_sku="LT1GC",
        )
        self.variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="LT1GC-1",
            inventory_control=ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY,
        )
        self.lot = ProductLot.objects.create(
            company=self.company,
            variant=self.variant,
            lot_number="CONC-1",
            expiration_date=timezone.localdate() + timedelta(days=60),
        )
        self.balance = LotBalance.objects.create(
            company=self.company,
            lot=self.lot,
            location=self.location,
            status=LotBalance.STATUS_AVAILABLE,
            physical_quantity=10,
        )
        Stock.objects.create(
            company=self.company,
            warehouse=self.origin,
            variant=self.variant,
            physical_stock=10,
            reserved_stock=0,
            committed_stock=0,
        )
        VariantLocation.objects.create(
            company=self.company,
            variant=self.variant,
            location=self.location,
            quantity=10,
        )

    def _picking(self, quantity, *, variant=None):
        variant = variant or self.variant
        order = Order.objects.create(
            company=self.company,
            customer=self.customer,
            created_by=self.user,
            status="paid",
            total=quantity,
            amount_paid=quantity,
            balance_due=0,
        )
        item = OrderItem.objects.create(
            order=order,
            variant=variant,
            quantity=quantity,
            unit_price=1,
        )
        fulfillment = OrderFulfillment.objects.create(
            company=self.company,
            order=order,
            warehouse=self.origin,
            created_by=self.user,
        )
        OrderItemAllocation.objects.create(
            company=self.company,
            fulfillment=fulfillment,
            order_item=item,
            variant=variant,
            commercial_quantity=quantity,
            internal_quantity=quantity,
        )
        return PickingOrder.objects.create(
            company=self.company,
            order=order,
            fulfillment=fulfillment,
            warehouse=self.origin,
            assigned_to=self.user,
        )

    def _multi_picking(self, variants):
        order = Order.objects.create(
            company=self.company,
            customer=self.customer,
            created_by=self.user,
            status="paid",
            total=len(variants),
            amount_paid=len(variants),
            balance_due=0,
        )
        fulfillment = OrderFulfillment.objects.create(
            company=self.company,
            order=order,
            warehouse=self.origin,
            created_by=self.user,
        )
        for variant in variants:
            item = OrderItem.objects.create(
                order=order,
                variant=variant,
                quantity=1,
                unit_price=1,
            )
            OrderItemAllocation.objects.create(
                company=self.company,
                fulfillment=fulfillment,
                order_item=item,
                variant=variant,
                commercial_quantity=1,
                internal_quantity=1,
            )
        return PickingOrder.objects.create(
            company=self.company,
            order=order,
            fulfillment=fulfillment,
            warehouse=self.origin,
            assigned_to=self.user,
        )

    def _transfer(self, quantity):
        transfer = WarehouseTransfer.objects.create(
            company=self.company,
            folio=f"LT1G-C-{WarehouseTransfer.objects.count()+1}",
            origin_warehouse=self.origin,
            destination_warehouse=self.destination,
            status=WarehouseTransfer.STATUS_REQUESTED,
            priority=WarehouseTransfer.PRIORITY_NORMAL,
            reason="Concurrencia LT1G",
            requested_by=self.user,
        )
        WarehouseTransferItem.objects.create(
            company=self.company,
            transfer=transfer,
            variant=self.variant,
            requested_quantity=quantity,
        )
        return transfer

    def _run_threads(self, callables):
        barrier = threading.Barrier(len(callables))
        results = queue.Queue()

        def worker(index, callback):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout TO '5s'")
                barrier.wait(timeout=5)
                callback()
                results.put((index, "ok", ""))
            except Exception as exc:
                results.put(
                    (
                        index,
                        exc.__class__.__name__,
                        str(exc),
                    )
                )
            finally:
                # close_old_connections() no cierra una conexión sana mientras
                # CONN_MAX_AGE siga vigente. En estos workers de test debemos
                # cerrar explícitamente las conexiones creadas por el hilo
                # antes de que termine, para que PostgreSQL pueda destruir
                # test_wms360 limpiamente.
                connections.close_all()

        threads = [
            threading.Thread(
                target=worker,
                args=(index, callback),
                daemon=True,
            )
            for index, callback in enumerate(callables)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=12)
        self.assertTrue(
            all(not thread.is_alive() for thread in threads),
            "Un hilo quedó bloqueado: posible deadlock.",
        )

        rows = []
        while not results.empty():
            rows.append(results.get())
        return sorted(rows)

    def _reserve_picking_callback(self, picking_id):
        user_id = self.user.id

        def callback():
            user = User.objects.get(pk=user_id)
            picking = PickingOrder.objects.get(pk=picking_id)
            reserve_stock_for_picking(
                picking,
                user=user,
            )

        return callback

    def test_concurrent_picking_reservations_never_overbook(self):
        p1 = self._picking(6)
        p2 = self._picking(6)

        results = self._run_threads(
            [
                self._reserve_picking_callback(p1.id),
                self._reserve_picking_callback(p2.id),
            ]
        )

        statuses = [row[1] for row in results]
        self.assertEqual(statuses.count("ok"), 1)
        self.assertEqual(
            statuses.count("ValidationError"),
            1,
        )

        stock = Stock.objects.get(
            company=self.company,
            warehouse=self.origin,
            variant=self.variant,
        )
        self.balance.refresh_from_db()
        self.assertEqual(stock.reserved_stock, 6)
        self.assertEqual(self.balance.reserved_quantity, 6)
        self.assertLessEqual(
            stock.reserved_stock + stock.committed_stock,
            stock.physical_stock,
        )

    def test_concurrent_exact_capacity_has_no_lost_update(self):
        p1 = self._picking(5)
        p2 = self._picking(5)

        results = self._run_threads(
            [
                self._reserve_picking_callback(p1.id),
                self._reserve_picking_callback(p2.id),
            ]
        )

        self.assertEqual(
            [row[1] for row in results].count("ok"),
            2,
        )
        stock = Stock.objects.get(
            company=self.company,
            warehouse=self.origin,
            variant=self.variant,
        )
        self.balance.refresh_from_db()
        self.assertEqual(stock.reserved_stock, 10)
        self.assertEqual(self.balance.reserved_quantity, 10)
        self.assertEqual(
            PickingLotAllocation.objects.aggregate(
                total=Sum("quantity")
            )["total"],
            10,
        )

    def test_transfer_and_picking_compete_without_overbooking(self):
        picking = self._picking(6)
        transfer = self._transfer(6)
        user_id = self.user.id

        def transfer_callback():
            reserve_transfer(
                transfer_id=transfer.id,
                user=User.objects.get(pk=user_id),
                request_id=uuid4(),
            )

        results = self._run_threads(
            [
                self._reserve_picking_callback(picking.id),
                transfer_callback,
            ]
        )

        statuses = [row[1] for row in results]
        self.assertEqual(statuses.count("ok"), 1)
        self.assertEqual(
            statuses.count("ValidationError"),
            1,
        )

        stock = Stock.objects.get(
            company=self.company,
            warehouse=self.origin,
            variant=self.variant,
        )
        self.balance.refresh_from_db()
        self.assertEqual(stock.reserved_stock, 6)
        self.assertEqual(self.balance.reserved_quantity, 6)
        self.assertLessEqual(
            stock.reserved_stock,
            stock.physical_stock,
        )

    def test_two_variant_opposite_order_does_not_deadlock(self):
        product2 = Product.objects.create(
            company=self.company,
            name="Producto LT1G C2",
            base_sku="LT1GC2",
        )
        variant2 = ProductVariant.objects.create(
            company=self.company,
            product=product2,
            sku="LT1GC-2",
            inventory_control=ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY,
        )
        lot2 = ProductLot.objects.create(
            company=self.company,
            variant=variant2,
            lot_number="CONC-2",
            expiration_date=timezone.localdate() + timedelta(days=70),
        )
        balance2 = LotBalance.objects.create(
            company=self.company,
            lot=lot2,
            location=self.location,
            physical_quantity=2,
        )
        Stock.objects.create(
            company=self.company,
            warehouse=self.origin,
            variant=variant2,
            physical_stock=2,
            reserved_stock=0,
            committed_stock=0,
        )
        VariantLocation.objects.create(
            company=self.company,
            variant=variant2,
            location=self.location,
            quantity=2,
        )

        # La variante original también necesita 2 para que ambos pickings
        # puedan reservar una unidad sin competir por insuficiencia.
        self.balance.physical_quantity = 2
        self.balance.save(update_fields=["physical_quantity", "updated_at"])
        Stock.objects.filter(
            company=self.company,
            warehouse=self.origin,
            variant=self.variant,
        ).update(physical_stock=2)
        VariantLocation.objects.filter(
            company=self.company,
            variant=self.variant,
            location=self.location,
        ).update(quantity=2)

        p1 = self._multi_picking([self.variant, variant2])
        p2 = self._multi_picking([variant2, self.variant])

        results = self._run_threads(
            [
                self._reserve_picking_callback(p1.id),
                self._reserve_picking_callback(p2.id),
            ]
        )

        self.assertEqual(
            [row[1] for row in results].count("ok"),
            2,
        )

        stock1 = Stock.objects.get(
            company=self.company,
            warehouse=self.origin,
            variant=self.variant,
        )
        stock2 = Stock.objects.get(
            company=self.company,
            warehouse=self.origin,
            variant=variant2,
        )
        balance2.refresh_from_db()
        self.balance.refresh_from_db()

        self.assertEqual(stock1.reserved_stock, 2)
        self.assertEqual(stock2.reserved_stock, 2)
        self.assertEqual(self.balance.reserved_quantity, 2)
        self.assertEqual(balance2.reserved_quantity, 2)
