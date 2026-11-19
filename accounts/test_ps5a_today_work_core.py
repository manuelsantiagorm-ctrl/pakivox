import inspect

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase

from companies.models import Company
from inventory.models import Warehouse
from purchases.models import PurchaseOrder, Supplier

from accounts.employee_home_views import employee_home
from accounts.models import (
    UserProfile,
    UserWarehouseAccess,
)
from accounts.today_work import (
    build_today_work_items,
)
from accounts.warehouse_access import (
    ALL_WAREHOUSES_VALUE,
    SESSION_WAREHOUSE_KEY,
)


User = get_user_model()


class _Session(dict):
    modified = False


class PS5ATodayWorkCoreTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

        self.company = Company.objects.create(
            name="PS5A Empresa",
            slug="ps5a-empresa",
        )
        self.other_company = Company.objects.create(
            name="PS5A Ajena",
            slug="ps5a-ajena",
        )

        self.user = User.objects.create_user(
            "ps5a-employee@test.local",
            password="PS5A-StrongPass-123!",
        )
        UserProfile.objects.create(
            user=self.user,
            company=self.company,
            role="sales",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )

        self.owner = User.objects.create_user(
            "ps5a-owner@test.local",
            password="PS5A-OwnerPass-123!",
        )
        UserProfile.objects.create(
            user=self.owner,
            company=self.company,
            role="owner",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )

        self.warehouse_a = Warehouse.objects.create(
            company=self.company,
            name="A",
            code="PS5A-A",
        )
        self.warehouse_b = Warehouse.objects.create(
            company=self.company,
            name="B",
            code="PS5A-B",
        )
        self.foreign_warehouse = Warehouse.objects.create(
            company=self.other_company,
            name="Ajena",
            code="PS5A-X",
        )

        UserWarehouseAccess.objects.create(
            company=self.company,
            user=self.user,
            warehouse=self.warehouse_a,
            access_level=UserWarehouseAccess.LEVEL_VIEW,
            is_active=True,
        )

        self.supplier = Supplier.objects.create(
            company=self.company,
            name="Proveedor PS5A",
        )
        self.foreign_supplier = Supplier.objects.create(
            company=self.other_company,
            name="Proveedor Ajeno PS5A",
        )

        PurchaseOrder.objects.create(
            company=self.company,
            supplier=self.supplier,
            warehouse=self.warehouse_a,
            folio="PS5A-PO-A",
            status=PurchaseOrder.STATUS_DRAFT,
        )
        PurchaseOrder.objects.create(
            company=self.company,
            supplier=self.supplier,
            warehouse=self.warehouse_b,
            folio="PS5A-PO-B",
            status=PurchaseOrder.STATUS_DRAFT,
        )
        PurchaseOrder.objects.create(
            company=self.other_company,
            supplier=self.foreign_supplier,
            warehouse=self.foreign_warehouse,
            folio="PS5A-PO-X",
            status=PurchaseOrder.STATUS_DRAFT,
        )
        PurchaseOrder.objects.create(
            company=self.company,
            supplier=self.supplier,
            warehouse=self.warehouse_a,
            folio="PS5A-PO-DONE",
            status=PurchaseOrder.STATUS_RECEIVED,
        )

    def _request(self, *, selected=ALL_WAREHOUSES_VALUE, user=None):
        request = self.factory.get("/accounts/inicio/")
        request.user = user or self.user
        request.session = _Session(
            {
                SESSION_WAREHOUSE_KEY: selected,
            }
        )
        return request

    def _purchasing_items(self, request):
        return build_today_work_items(
            request,
            capability_codes={"purchases.view"},
            activity_codes={"purchasing"},
        )

    def test_all_scope_only_counts_authorized_warehouse(self):
        items = self._purchasing_items(self._request())

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["code"], "purchasing_open")
        self.assertEqual(items[0]["count"], 1)
        self.assertEqual(items[0]["count_label"], "1")

    def test_specific_warehouse_scope_is_honored(self):
        UserWarehouseAccess.objects.create(
            company=self.company,
            user=self.user,
            warehouse=self.warehouse_b,
            access_level=UserWarehouseAccess.LEVEL_VIEW,
            is_active=True,
        )

        request = self._request(
            selected=str(self.warehouse_b.pk),
        )
        items = self._purchasing_items(request)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["count"], 1)

    def test_foreign_company_and_terminal_rows_never_count(self):
        items = self._purchasing_items(self._request())

        self.assertEqual(items[0]["count"], 1)

    def test_activity_not_assigned_means_no_task(self):
        items = build_today_work_items(
            self._request(),
            capability_codes={"purchases.view"},
            activity_codes=set(),
        )

        self.assertEqual(items, tuple())

    def test_missing_capability_means_no_task(self):
        items = build_today_work_items(
            self._request(),
            capability_codes=set(),
            activity_codes={"purchasing"},
        )

        self.assertEqual(items, tuple())

    def test_owner_never_uses_employee_today_queue(self):
        items = build_today_work_items(
            self._request(
                user=self.owner,
            ),
            capability_codes={"purchases.view"},
            activity_codes={"purchasing"},
        )

        self.assertEqual(items, tuple())

    def test_employee_home_removed_company_wide_payment_query(self):
        source = inspect.getsource(employee_home)

        self.assertNotIn("Payment.objects", source)
        self.assertNotIn("payment_summary", source)
        self.assertIn("build_today_work_context", source)

    def test_template_has_today_work_and_no_old_payment_summary(self):
        from django.conf import settings

        template = (
            settings.BASE_DIR
            / "templates"
            / "accounts"
            / "employee_home.html"
        ).read_text(encoding="utf-8")

        self.assertIn("data-today-work", template)
        self.assertIn("data-today-work-code", template)
        self.assertIn("today_work_items", template)
        self.assertNotIn("payment_summary.pending_amount", template)
        self.assertNotIn("recent_payments", template)
