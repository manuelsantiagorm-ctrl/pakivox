from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from accounts.models import UserProfile, UserWarehouseAccess
from accounts.today_work import build_today_work_items
from accounts.warehouse_access import (
    ALL_WAREHOUSES_VALUE,
    SESSION_WAREHOUSE_KEY,
)
from companies.models import Company
from crm.models import Customer
from inventory.models import Warehouse
from payments.models import Payment
from sales.models import Order
from warehouse.models import OrderFulfillment


User = get_user_model()


class _Session(dict):
    modified = False


class PS5B2TodayWorkSalesCollectionsTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

        self.company = Company.objects.create(
            name="PS5B2 Empresa",
            slug="ps5b2-empresa",
        )
        self.other_company = Company.objects.create(
            name="PS5B2 Ajena",
            slug="ps5b2-ajena",
        )

        self.user = User.objects.create_user(
            "ps5b2-user@test.local",
            password="PS5B2-StrongPass-123!",
        )
        UserProfile.objects.create(
            user=self.user,
            company=self.company,
            role="sales",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )

        self.other_user = User.objects.create_user(
            "ps5b2-other@test.local",
            password="PS5B2-OtherPass-123!",
        )
        UserProfile.objects.create(
            user=self.other_user,
            company=self.company,
            role="sales",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )

        self.warehouse_a = Warehouse.objects.create(
            company=self.company,
            name="A",
            code="PS5B2-A",
        )
        self.warehouse_b = Warehouse.objects.create(
            company=self.company,
            name="B",
            code="PS5B2-B",
        )
        self.foreign_warehouse = Warehouse.objects.create(
            company=self.other_company,
            name="X",
            code="PS5B2-X",
        )

        UserWarehouseAccess.objects.create(
            company=self.company,
            user=self.user,
            warehouse=self.warehouse_a,
            access_level=UserWarehouseAccess.LEVEL_VIEW,
            is_active=True,
        )

        self.customer = Customer.objects.create(
            company=self.company,
            full_name="Cliente PS5B2",
        )
        self.foreign_customer = Customer.objects.create(
            company=self.other_company,
            full_name="Cliente Ajeno PS5B2",
        )

    def _request(self, selected=ALL_WAREHOUSES_VALUE):
        request = self.factory.get("/accounts/inicio/")
        request.user = self.user
        request.session = _Session(
            {
                SESSION_WAREHOUSE_KEY: selected,
            }
        )
        return request

    def _order(
        self,
        folio,
        *,
        creator=None,
        status="draft",
        company=None,
        customer=None,
    ):
        company = company or self.company
        customer = customer or self.customer
        return Order.objects.create(
            company=company,
            customer=customer,
            created_by=creator or self.user,
            folio=folio,
            status=status,
        )

    def _payment(self, order, folio, *, status="pending"):
        return Payment.objects.create(
            company=order.company,
            order=order,
            registered_by=self.user,
            folio=folio,
            payment_method="manual",
            payment_type="deposit",
            status=status,
            amount="100.00",
        )

    def _items(self, *, caps, activities, selected=ALL_WAREHOUSES_VALUE):
        return build_today_work_items(
            self._request(selected),
            capability_codes=set(caps),
            activity_codes=set(activities),
        )

    def _by_code(self, items, code):
        return next(
            (item for item in items if item["code"] == code),
            None,
        )

    def test_sales_card_counts_own_open_commercial_order(self):
        self._order("PS5B2-SALES-1")

        items = self._items(
            caps={"sales.view"},
            activities={"sales_customers"},
        )

        task = self._by_code(items, "sales_follow_up")
        self.assertIsNotNone(task)
        self.assertEqual(task["count"], 1)
        self.assertEqual(
            task["url"],
            reverse("sales:pedidos_sin_pagar"),
        )
        self.assertNotIn("amount", task)
        self.assertNotIn("customer", task)

    def test_sales_card_hides_order_assigned_to_inaccessible_warehouse(self):
        order = self._order("PS5B2-SALES-B")
        OrderFulfillment.objects.create(
            company=self.company,
            order=order,
            warehouse=self.warehouse_b,
            sequence=1,
        )

        items = self._items(
            caps={"sales.view"},
            activities={"sales_customers"},
        )

        self.assertIsNone(
            self._by_code(items, "sales_follow_up")
        )

    def test_sales_without_supervise_only_counts_own_orders(self):
        self._order("PS5B2-MINE")
        self._order(
            "PS5B2-OTHER",
            creator=self.other_user,
        )

        items = self._items(
            caps={"sales.view"},
            activities={"sales_customers"},
        )

        self.assertEqual(
            self._by_code(items, "sales_follow_up")["count"],
            1,
        )

    def test_sales_supervise_counts_team_orders_in_scope(self):
        self._order("PS5B2-MINE")
        self._order(
            "PS5B2-OTHER",
            creator=self.other_user,
        )

        items = self._items(
            caps={"sales.view", "sales.supervise"},
            activities={"sales_customers"},
        )

        self.assertEqual(
            self._by_code(items, "sales_follow_up")["count"],
            2,
        )

    def test_sales_card_excludes_order_with_payment_under_review(self):
        order = self._order("PS5B2-REVIEW")
        self._payment(
            order,
            "PS5B2-PAY-REVIEW",
            status="under_review",
        )

        items = self._items(
            caps={"sales.view"},
            activities={"sales_customers"},
        )

        self.assertIsNone(
            self._by_code(items, "sales_follow_up")
        )

    def test_collections_card_counts_pending_and_under_review_only(self):
        pending_order = self._order("PS5B2-PENDING")
        review_order = self._order("PS5B2-UNDER")
        approved_order = self._order("PS5B2-APPROVED")

        self._payment(
            pending_order,
            "PS5B2-PAY-P",
            status="pending",
        )
        self._payment(
            review_order,
            "PS5B2-PAY-U",
            status="under_review",
        )
        self._payment(
            approved_order,
            "PS5B2-PAY-A",
            status="approved",
        )

        items = self._items(
            caps={"payments.view"},
            activities={"collections"},
        )

        task = self._by_code(items, "collections_pending")
        self.assertIsNotNone(task)
        self.assertEqual(task["count"], 2)
        self.assertEqual(
            task["url"],
            reverse("payments:lista_pagos"),
        )

    def test_collections_card_hides_payment_from_inaccessible_warehouse(self):
        order = self._order("PS5B2-PAY-B")
        OrderFulfillment.objects.create(
            company=self.company,
            order=order,
            warehouse=self.warehouse_b,
            sequence=1,
        )
        self._payment(
            order,
            "PS5B2-PAY-HIDDEN",
            status="pending",
        )

        items = self._items(
            caps={"payments.view"},
            activities={"collections"},
        )

        self.assertIsNone(
            self._by_code(items, "collections_pending")
        )

    def test_collections_requires_payments_view(self):
        order = self._order("PS5B2-NOVIEW")
        self._payment(
            order,
            "PS5B2-PAY-NOVIEW",
            status="pending",
        )

        items = self._items(
            caps={"payments.create"},
            activities={"collections"},
        )

        self.assertIsNone(
            self._by_code(items, "collections_pending")
        )

    def test_active_warehouse_narrows_sales_and_collections(self):
        UserWarehouseAccess.objects.create(
            company=self.company,
            user=self.user,
            warehouse=self.warehouse_b,
            access_level=UserWarehouseAccess.LEVEL_VIEW,
            is_active=True,
        )

        order = self._order("PS5B2-AB")
        OrderFulfillment.objects.create(
            company=self.company,
            order=order,
            warehouse=self.warehouse_a,
            sequence=1,
        )
        OrderFulfillment.objects.create(
            company=self.company,
            order=order,
            warehouse=self.warehouse_b,
            sequence=2,
        )
        self._payment(
            order,
            "PS5B2-PAY-AB",
            status="pending",
        )

        all_items = self._items(
            caps={"sales.view", "payments.view"},
            activities={"sales_customers", "collections"},
        )
        a_items = self._items(
            caps={"sales.view", "payments.view"},
            activities={"sales_customers", "collections"},
            selected=str(self.warehouse_a.id),
        )

        self.assertIsNotNone(
            self._by_code(all_items, "collections_pending")
        )
        self.assertIsNone(
            self._by_code(a_items, "collections_pending")
        )

    def test_foreign_company_rows_never_enter_today_work(self):
        foreign_order = self._order(
            "PS5B2-FOREIGN",
            company=self.other_company,
            customer=self.foreign_customer,
        )
        self._payment(
            foreign_order,
            "PS5B2-PAY-FOREIGN",
            status="pending",
        )

        items = self._items(
            caps={"sales.view", "payments.view"},
            activities={"sales_customers", "collections"},
        )

        self.assertIsNone(
            self._by_code(items, "sales_follow_up")
        )
        self.assertIsNone(
            self._by_code(items, "collections_pending")
        )
