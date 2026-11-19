from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from accounts.models import (
    Capability,
    EmployeeCapabilityAssignment,
    UserProfile,
    UserWarehouseAccess,
)
from companies.models import Company
from inventory.models import (
    Product,
    ProductVariant,
    StockCriticalAlert,
    Warehouse,
)
from notifications.models import Notification
from notifications.services import sync_stock_notification


User = get_user_model()


class PS6P4BReplenishmentEntryTests(TestCase):

    def setUp(self):
        self.company = Company.objects.create(
            name="PS6P4B Replenishment",
            slug="ps6p4b-replenishment",
        )

        self.foreign_company = Company.objects.create(
            name="PS6P4B Replenishment X",
            slug="ps6p4b-replenishment-x",
        )

        self.user = User.objects.create_user(
            username="ps6p4b-replenishment@test.local",
            password="x",
        )

        UserProfile.objects.create(
            user=self.user,
            company=self.company,
            role="warehouse",
            is_active=True,
            employment_status=(
                UserProfile.STATUS_ACTIVE
            ),
        )

        self.warehouse = Warehouse.objects.create(
            company=self.company,
            name="Replenishment A",
            code="PS6P4BR-A",
        )

        self.other_warehouse = Warehouse.objects.create(
            company=self.company,
            name="Replenishment B",
            code="PS6P4BR-B",
        )

        self.foreign_warehouse = Warehouse.objects.create(
            company=self.foreign_company,
            name="Replenishment X",
            code="PS6P4BR-X",
        )

        UserWarehouseAccess.objects.create(
            company=self.company,
            user=self.user,
            warehouse=self.warehouse,
            access_level=(
                UserWarehouseAccess.LEVEL_OPERATE
            ),
            is_active=True,
        )

        capability = Capability.objects.get(
            code="purchases.create"
        )

        self.assignment = (
            EmployeeCapabilityAssignment.objects.create(
                company=self.company,
                user=self.user,
                capability=capability,
                source=(
                    EmployeeCapabilityAssignment
                    .SOURCE_DIRECT
                ),
                is_active=True,
            )
        )

        self.product = Product.objects.create(
            company=self.company,
            name="Producto Seguro PS6P4B",
            base_sku="PS6P4B-P",
            cost_price="12.50",
            sale_price="30.00",
        )

        self.variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="PS6P4B-SKU",
            cost_price="12.50",
            sale_price="30.00",
            min_stock=5,
        )

        self.foreign_product = Product.objects.create(
            company=self.foreign_company,
            name="Producto Ajeno",
            base_sku="PS6P4B-X",
            cost_price="99.99",
            sale_price="150.00",
        )

        self.foreign_variant = ProductVariant.objects.create(
            company=self.foreign_company,
            product=self.foreign_product,
            sku="PS6P4B-X-SKU",
            cost_price="99.99",
            sale_price="150.00",
            min_stock=5,
        )

        self.alert = StockCriticalAlert.objects.create(
            company=self.company,
            warehouse=self.warehouse,
            variant=self.variant,
            alert_type=(
                StockCriticalAlert.TYPE_LOW_STOCK
            ),
            status=(
                StockCriticalAlert.STATUS_ACTIVE
            ),
            current_available=1,
            min_stock=5,
            source="ps6p4b",
            reference="SAFE-A",
            message="Reabastecimiento requerido.",
        )

        self.other_alert = (
            StockCriticalAlert.objects.create(
                company=self.company,
                warehouse=self.other_warehouse,
                variant=self.variant,
                alert_type=(
                    StockCriticalAlert.TYPE_LOW_STOCK
                ),
                status=(
                    StockCriticalAlert.STATUS_ACTIVE
                ),
                current_available=1,
                min_stock=5,
                source="ps6p4b",
                reference="SAFE-B",
                message="Otra bodega.",
            )
        )

        self.foreign_alert = (
            StockCriticalAlert.objects.create(
                company=self.foreign_company,
                warehouse=self.foreign_warehouse,
                variant=self.foreign_variant,
                alert_type=(
                    StockCriticalAlert.TYPE_LOW_STOCK
                ),
                status=(
                    StockCriticalAlert.STATUS_ACTIVE
                ),
                current_available=0,
                min_stock=5,
                source="ps6p4b",
                reference="SAFE-X",
                message="Ajena.",
            )
        )

        self.client.force_login(
            self.user
        )

    def test_operational_purchaser_can_open_authorized_alert(self):
        response = self.client.get(
            reverse(
                "purchases:replenishment_alert_detail",
                kwargs={
                    "alert_id": self.alert.id,
                },
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertContains(
            response,
            "PS6P4B-SKU",
        )

        self.assertContains(
            response,
            "Replenishment A",
        )

    def test_replenishment_page_does_not_expose_costs(self):
        response = self.client.get(
            reverse(
                "purchases:replenishment_alert_detail",
                kwargs={
                    "alert_id": self.alert.id,
                },
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertNotContains(
            response,
            "12.50",
        )

        self.assertNotContains(
            response,
            "Costo unitario",
        )

        self.assertNotContains(
            response,
            "cost_price",
        )

        self.assertFalse(
            EmployeeCapabilityAssignment.objects.filter(
                company=self.company,
                user=self.user,
                capability__code="purchases.costs.view",
                is_active=True,
            ).exists()
        )

    def test_same_company_unauthorized_warehouse_is_403(self):
        response = self.client.get(
            reverse(
                "purchases:replenishment_alert_detail",
                kwargs={
                    "alert_id": self.other_alert.id,
                },
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_foreign_alert_is_404(self):
        response = self.client.get(
            reverse(
                "purchases:replenishment_alert_detail",
                kwargs={
                    "alert_id": self.foreign_alert.id,
                },
            )
        )

        self.assertEqual(
            response.status_code,
            404,
        )

    def test_missing_purchase_capability_is_403(self):
        self.assignment.is_active = False
        self.assignment.save(
            update_fields=[
                "is_active",
            ]
        )

        response = self.client.get(
            reverse(
                "purchases:replenishment_alert_detail",
                kwargs={
                    "alert_id": self.alert.id,
                },
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_stock_notification_uses_non_financial_safe_action(self):
        sync_stock_notification(
            self.alert
        )

        notification = Notification.objects.get(
            company=self.company,
            event_type=(
                StockCriticalAlert.TYPE_LOW_STOCK
            ),
            source_object_id=self.alert.id,
        )

        self.assertEqual(
            notification.action_name,
            "purchases:replenishment_alert_detail",
        )

        self.assertEqual(
            notification.action_kwargs,
            {
                "alert_id": self.alert.id,
            },
        )

        response = self.client.get(
            reverse(
                "notifications:open",
                kwargs={
                    "notification_id": notification.id,
                },
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertEqual(
            response.url,
            reverse(
                "purchases:replenishment_alert_detail",
                kwargs={
                    "alert_id": self.alert.id,
                },
            ),
        )
