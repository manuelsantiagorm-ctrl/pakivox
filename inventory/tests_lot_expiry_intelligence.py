from datetime import timedelta
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile
from companies.models import Company
from inventory.expiry_services import (
    classify_expiry,
    get_expiry_summary,
    scan_expiry_risks,
    sync_lot_expiry_alerts,
)
from inventory.models import (
    LotBalance,
    LotExpiryAlert,
    Product,
    ProductLot,
    ProductVariant,
    Warehouse,
)
from warehouse.models import (
    TransferLotAllocation,
    WarehouseLocation,
    WarehouseTransfer,
    WarehouseTransferItem,
)
from warehouse.services.transfer_lots import receive_transfer_lot_allocation


User = get_user_model()


class LotExpiryIntelligenceLT1FTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()
        self.company = Company.objects.create(
            name="Empresa LT1F",
            slug="empresa-lt1f",
        )
        self.other_company = Company.objects.create(
            name="Empresa LT1F B",
            slug="empresa-lt1f-b",
        )
        self.user = User.objects.create_user(
            "lt1f-owner@test.local",
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
            name="Bodega LT1F",
            code="L1F-A",
        )
        self.warehouse_b = Warehouse.objects.create(
            company=self.company,
            name="Bodega LT1F B",
            code="L1F-B",
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
        self.location_b = WarehouseLocation.objects.create(
            company=self.company,
            warehouse=self.warehouse_b,
            aisle="B",
            rack="1",
            level="1",
            bin_code="B1",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )
        self.product = Product.objects.create(
            company=self.company,
            name="Producto LT1F",
            base_sku="LT1F",
        )
        self.variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="LT1F-EXP",
            inventory_control=ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY,
        )

    def _lot(self, number, days, qty=5, location=None, balance_status=None):
        lot = ProductLot.objects.create(
            company=self.company,
            variant=self.variant,
            lot_number=number,
            expiration_date=self.today + timedelta(days=days),
        )
        balance = LotBalance.objects.create(
            company=self.company,
            lot=lot,
            location=location or self.location,
            status=balance_status or LotBalance.STATUS_AVAILABLE,
            physical_quantity=qty,
            reserved_quantity=0,
            committed_quantity=0,
        )
        return lot, balance

    def test_no_expiry_has_no_alert(self):
        state = classify_expiry(None, today=self.today)
        self.assertFalse(state["requires_alert"])
        self.assertEqual(state["label"], "Sin caducidad")

    def test_over_60_days_is_normal(self):
        state = classify_expiry(self.today + timedelta(days=61), today=self.today)
        self.assertIsNone(state["severity"])
        self.assertEqual(state["label"], "Normal")

    def test_31_to_60_days_is_info(self):
        state = classify_expiry(self.today + timedelta(days=45), today=self.today)
        self.assertEqual(state["severity"], LotExpiryAlert.SEVERITY_INFO)

    def test_16_to_30_days_is_notice(self):
        state = classify_expiry(self.today + timedelta(days=20), today=self.today)
        self.assertEqual(state["severity"], LotExpiryAlert.SEVERITY_NOTICE)

    def test_8_to_15_days_is_warning(self):
        state = classify_expiry(self.today + timedelta(days=10), today=self.today)
        self.assertEqual(state["severity"], LotExpiryAlert.SEVERITY_WARNING)

    def test_0_to_7_days_is_critical(self):
        state = classify_expiry(self.today + timedelta(days=3), today=self.today)
        self.assertEqual(state["severity"], LotExpiryAlert.SEVERITY_CRITICAL)

    def test_past_date_is_expired(self):
        state = classify_expiry(self.today - timedelta(days=1), today=self.today)
        self.assertEqual(state["severity"], LotExpiryAlert.SEVERITY_EXPIRED)
        self.assertTrue(state["is_expired"])

    def test_product_lot_properties_are_derived(self):
        lot, _ = self._lot("PROP", 5)
        self.assertEqual(lot.days_to_expiry, 5)
        self.assertEqual(lot.expiry_severity, LotExpiryAlert.SEVERITY_CRITICAL)
        self.assertFalse(lot.is_expired)

    def test_scan_aggregates_multiple_balances_same_warehouse(self):
        lot, _ = self._lot("AGG", 10, qty=2)
        second = WarehouseLocation.objects.create(
            company=self.company,
            warehouse=self.warehouse,
            aisle="A",
            rack="2",
            level="1",
            bin_code="A2",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )
        LotBalance.objects.create(
            company=self.company,
            lot=lot,
            location=second,
            status=LotBalance.STATUS_QUARANTINE,
            physical_quantity=3,
        )
        rows = scan_expiry_risks(company=self.company, today=self.today)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["affected_quantity"], 5)
        self.assertEqual(rows[0]["quarantine_quantity"], 3)

    def test_scan_separates_same_lot_by_warehouse(self):
        lot, _ = self._lot("WH", 10, qty=2)
        LotBalance.objects.create(
            company=self.company,
            lot=lot,
            location=self.location_b,
            status=LotBalance.STATUS_AVAILABLE,
            physical_quantity=4,
        )
        rows = scan_expiry_risks(company=self.company, today=self.today)
        self.assertEqual(len(rows), 2)
        self.assertEqual(
            {row["warehouse"].id for row in rows},
            {self.warehouse.id, self.warehouse_b.id},
        )

    def test_waste_balance_is_not_alerted(self):
        self._lot(
            "WASTE",
            1,
            qty=5,
            balance_status=LotBalance.STATUS_WASTE,
        )
        self.assertEqual(
            scan_expiry_risks(company=self.company, today=self.today),
            [],
        )

    def test_sync_creates_one_active_alert(self):
        lot, _ = self._lot("SYNC", 10, qty=7)
        result = sync_lot_expiry_alerts(
            company=self.company,
            today=self.today,
        )
        self.assertEqual(result["created"], 1)
        alert = LotExpiryAlert.objects.get(lot=lot)
        self.assertEqual(alert.affected_quantity, 7)
        self.assertEqual(alert.severity, LotExpiryAlert.SEVERITY_WARNING)

    def test_sync_escalation_marks_alert_unread(self):
        lot, _ = self._lot("ESC", 10, qty=3)
        sync_lot_expiry_alerts(company=self.company, today=self.today)
        alert = LotExpiryAlert.objects.get(lot=lot)
        alert.is_read = True
        alert.save(update_fields=["is_read", "updated_at"])

        sync_lot_expiry_alerts(
            company=self.company,
            today=self.today + timedelta(days=8),
        )
        alert.refresh_from_db()
        self.assertEqual(alert.severity, LotExpiryAlert.SEVERITY_CRITICAL)
        self.assertFalse(alert.is_read)

    def test_sync_resolves_when_physical_quantity_disappears(self):
        lot, balance = self._lot("RESOLVE", 10, qty=3)
        sync_lot_expiry_alerts(company=self.company, today=self.today)
        balance.physical_quantity = 0
        balance.save(update_fields=["physical_quantity", "updated_at"])
        result = sync_lot_expiry_alerts(
            company=self.company,
            today=self.today,
        )
        self.assertEqual(result["resolved"], 1)
        alert = LotExpiryAlert.objects.get(lot=lot)
        self.assertEqual(alert.status, LotExpiryAlert.STATUS_RESOLVED)

    def test_summary_respects_warehouse_scope(self):
        self._lot("A", 5, qty=2)
        self._lot("B", 10, qty=3, location=self.location_b)
        summary = get_expiry_summary(
            company=self.company,
            warehouse_ids=[self.warehouse.id],
            today=self.today,
        )
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["affected_quantity"], 2)

    def test_dry_run_command_does_not_create_rows(self):
        self._lot("DRY", 5, qty=2)
        out = StringIO()
        call_command(
            "sync_lot_expiry_alerts",
            "--company-id",
            str(self.company.id),
            "--dry-run",
            stdout=out,
        )
        self.assertEqual(LotExpiryAlert.objects.count(), 0)
        self.assertIn("DRY-RUN OK", out.getvalue())

    def test_expiry_alert_view_never_exposes_other_company(self):
        self._lot("VISIBLE", 5, qty=2)
        sync_lot_expiry_alerts(company=self.company, today=self.today)

        other_wh = Warehouse.objects.create(
            company=self.other_company,
            name="Otra bodega",
            code="OTHER",
        )
        other_product = Product.objects.create(
            company=self.other_company,
            name="Otro",
            base_sku="OTHER",
        )
        other_variant = ProductVariant.objects.create(
            company=self.other_company,
            product=other_product,
            sku="OTHER-EXP",
            inventory_control=ProductVariant.INVENTORY_CONTROL_LOT_EXPIRY,
        )
        other_lot = ProductLot.objects.create(
            company=self.other_company,
            variant=other_variant,
            lot_number="SECRET",
            expiration_date=self.today + timedelta(days=2),
        )
        LotExpiryAlert.objects.create(
            company=self.other_company,
            warehouse=other_wh,
            lot=other_lot,
            severity=LotExpiryAlert.SEVERITY_CRITICAL,
            expiration_date=other_lot.expiration_date,
            days_to_expiry=2,
            affected_quantity=99,
        )

        # Esta prueba valida aislamiento tenant de la vista, no el
        # middleware mensual de suscripción. La empresa fixture no crea
        # Subscription, por lo que un owner normal sería redirigido antes
        # de entrar a inventory. Usamos el bypass de superusuario que el
        # middleware de producción ya define explícitamente, conservando
        # el UserProfile de self.company para que la vista siga filtrando
        # estrictamente por empresa.
        self.user.is_superuser = True
        self.user.is_staff = True
        self.user.save(
            update_fields=["is_superuser", "is_staff"]
        )
        self.client.force_login(self.user)
        response = self.client.get(reverse("inventory:lot_expiry_alerts"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("VISIBLE", body)
        self.assertNotIn("SECRET", body)

    def test_expired_in_transit_cannot_be_received_as_conforming(self):
        lot, source = self._lot("TRANSIT-EXP", -1, qty=0)
        transfer = WarehouseTransfer.objects.create(
            company=self.company,
            folio="LT1F-TRANSIT",
            origin_warehouse=self.warehouse,
            destination_warehouse=self.warehouse_b,
            status=WarehouseTransfer.STATUS_RECEIVING,
            priority=WarehouseTransfer.PRIORITY_NORMAL,
            reason="Prueba caducidad tránsito",
            requested_by=self.user,
        )
        item = WarehouseTransferItem.objects.create(
            company=self.company,
            transfer=transfer,
            variant=self.variant,
            requested_quantity=2,
            reserved_quantity=2,
            dispatched_quantity=2,
        )
        allocation = TransferLotAllocation.objects.create(
            company=self.company,
            transfer_item=item,
            lot=lot,
            source_balance=source,
            quantity=2,
            status=TransferLotAllocation.STATUS_IN_TRANSIT,
        )

        with self.assertRaisesMessage(ValidationError, "caducó durante el tránsito"):
            receive_transfer_lot_allocation(
                company=self.company,
                transfer=transfer,
                allocation=allocation,
                received=2,
                damaged=0,
                rejected=0,
                missing=0,
                destination_location=self.location_b,
                notes="",
                user=self.user,
            )

        self.assertFalse(
            LotBalance.objects.filter(
                lot=lot,
                location=self.location_b,
                physical_quantity__gt=0,
            ).exists()
        )
