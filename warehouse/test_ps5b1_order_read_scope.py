import inspect

from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase

from accounts.models import UserProfile, UserWarehouseAccess
from accounts.warehouse_access import (
    ALL_WAREHOUSES_VALUE,
    SESSION_WAREHOUSE_KEY,
)
from companies.models import Company
from crm.models import Customer
from inventory.models import Warehouse
from payments.models import Payment
from sales.models import Order
from warehouse.models import OrderFulfillment, PickingOrder
from warehouse.services.order_read_scope import (
    get_request_read_warehouse_ids,
    scope_orders_for_read,
    scope_payments_for_read,
)

from payments import views as payment_views
from sales import views as sales_views
from warehouse.services import fulfillment_planning

User = get_user_model()


class _Session(dict):
    modified = False


class PS5B1OrderReadScopeDatabaseTests(TestCase):
    def setUp(self):
        self.company = Company.objects.create(
            name="PS5B Empresa",
            slug="ps5b-empresa",
        )
        self.foreign_company = Company.objects.create(
            name="PS5B Ajena",
            slug="ps5b-ajena",
        )
        self.user = User.objects.create_user(
            "ps5b@test.local",
            password="PS5B-StrongPass-123!",
        )
        UserProfile.objects.create(
            user=self.user,
            company=self.company,
            role="sales",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )
        self.warehouse_a = Warehouse.objects.create(
            company=self.company,
            name="A",
            code="PS5B-A",
        )
        self.warehouse_b = Warehouse.objects.create(
            company=self.company,
            name="B",
            code="PS5B-B",
        )
        self.foreign_warehouse = Warehouse.objects.create(
            company=self.foreign_company,
            name="X",
            code="PS5B-X",
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
            full_name="Cliente PS5B",
        )
        self.foreign_customer = Customer.objects.create(
            company=self.foreign_company,
            full_name="Cliente Ajeno PS5B",
        )

        self.unassigned = self._order(self.company, self.customer, "PS5B-U")
        self.order_a = self._order(self.company, self.customer, "PS5B-A")
        self.order_b = self._order(self.company, self.customer, "PS5B-B")
        self.order_ab = self._order(self.company, self.customer, "PS5B-AB")
        self.foreign_order = self._order(
            self.foreign_company,
            self.foreign_customer,
            "PS5B-X",
        )

        OrderFulfillment.objects.create(
            company=self.company,
            order=self.order_a,
            warehouse=self.warehouse_a,
            sequence=1,
        )
        OrderFulfillment.objects.create(
            company=self.company,
            order=self.order_b,
            warehouse=self.warehouse_b,
            sequence=1,
        )
        OrderFulfillment.objects.create(
            company=self.company,
            order=self.order_ab,
            warehouse=self.warehouse_a,
            sequence=1,
        )
        OrderFulfillment.objects.create(
            company=self.company,
            order=self.order_ab,
            warehouse=self.warehouse_b,
            sequence=2,
        )

    def _order(self, company, customer, folio):
        return Order.objects.create(
            company=company,
            customer=customer,
            created_by=self.user if company == self.company else None,
            folio=folio,
            status="draft",
        )

    def _ids(self, warehouse_ids):
        return set(
            scope_orders_for_read(
                Order.objects.all(),
                company=self.company,
                warehouse_ids=warehouse_ids,
            ).values_list("id", flat=True)
        )

    def test_unassigned_commercial_order_remains_visible(self):
        self.assertIn(self.unassigned.id, self._ids([self.warehouse_a.id]))

    def test_authorized_persisted_warehouse_is_visible(self):
        self.assertIn(self.order_a.id, self._ids([self.warehouse_a.id]))

    def test_inaccessible_persisted_warehouse_is_hidden(self):
        self.assertNotIn(self.order_b.id, self._ids([self.warehouse_a.id]))

    def test_multiwarehouse_order_requires_every_persisted_warehouse(self):
        self.assertNotIn(self.order_ab.id, self._ids([self.warehouse_a.id]))
        self.assertIn(
            self.order_ab.id,
            self._ids([self.warehouse_a.id, self.warehouse_b.id]),
        )

    def test_foreign_company_order_never_leaks(self):
        self.assertNotIn(
            self.foreign_order.id,
            self._ids(
                [
                    self.warehouse_a.id,
                    self.warehouse_b.id,
                    self.foreign_warehouse.id,
                ]
            ),
        )

    def test_cancelled_historical_fulfillment_still_scopes_order(self):
        historical = self._order(self.company, self.customer, "PS5B-HIST")
        OrderFulfillment.objects.create(
            company=self.company,
            order=historical,
            warehouse=self.warehouse_b,
            sequence=1,
            status=OrderFulfillment.STATUS_CANCELLED,
        )
        self.assertNotIn(historical.id, self._ids([self.warehouse_a.id]))

    def test_legacy_picking_warehouse_also_scopes_order(self):
        legacy = self._order(self.company, self.customer, "PS5B-LEG")
        PickingOrder.objects.create(
            company=self.company,
            order=legacy,
            warehouse=self.warehouse_b,
        )
        self.assertNotIn(legacy.id, self._ids([self.warehouse_a.id]))

    def test_cross_tenant_persisted_association_fails_closed(self):
        corrupt = self._order(self.company, self.customer, "PS5B-CORRUPT")
        OrderFulfillment.objects.create(
            company=self.foreign_company,
            order=corrupt,
            warehouse=self.foreign_warehouse,
            sequence=1,
        )
        self.assertNotIn(
            corrupt.id,
            self._ids([self.warehouse_a.id, self.warehouse_b.id]),
        )

    def test_payment_inherits_order_read_scope(self):
        visible = Payment.objects.create(
            company=self.company,
            order=self.order_a,
            registered_by=self.user,
            folio="PS5B-PAY-A",
            payment_method="manual",
            payment_type="deposit",
            status="pending",
            amount="10.00",
        )
        hidden = Payment.objects.create(
            company=self.company,
            order=self.order_b,
            registered_by=self.user,
            folio="PS5B-PAY-B",
            payment_method="manual",
            payment_type="deposit",
            status="pending",
            amount="10.00",
        )

        ids = set(
            scope_payments_for_read(
                Payment.objects.all(),
                company=self.company,
                warehouse_ids=[self.warehouse_a.id],
            ).values_list("id", flat=True)
        )
        self.assertIn(visible.id, ids)
        self.assertNotIn(hidden.id, ids)

    def test_empty_warehouse_scope_returns_none(self):
        self.assertFalse(
            scope_orders_for_read(
                Order.objects.all(),
                company=self.company,
                warehouse_ids=[],
            ).exists()
        )
        self.assertFalse(
            scope_payments_for_read(
                Payment.objects.all(),
                company=self.company,
                warehouse_ids=[],
            ).exists()
        )


class PS5B1RequestWarehouseScopeTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.company = Company.objects.create(
            name="PS5B Request",
            slug="ps5b-request",
        )
        self.user = User.objects.create_user(
            "ps5b-request@test.local",
            password="PS5B-RequestPass-123!",
        )
        UserProfile.objects.create(
            user=self.user,
            company=self.company,
            role="sales",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )
        self.a = Warehouse.objects.create(
            company=self.company,
            name="A",
            code="PS5BR-A",
        )
        self.b = Warehouse.objects.create(
            company=self.company,
            name="B",
            code="PS5BR-B",
        )
        for warehouse in (self.a, self.b):
            UserWarehouseAccess.objects.create(
                company=self.company,
                user=self.user,
                warehouse=warehouse,
                access_level=UserWarehouseAccess.LEVEL_VIEW,
                is_active=True,
            )

    def _request(self, selected):
        request = self.factory.get("/sales/orders/")
        request.user = self.user
        request.session = _Session(
            {SESSION_WAREHOUSE_KEY: selected}
        )
        return request

    def test_all_scope_returns_all_authorized_warehouses(self):
        result = get_request_read_warehouse_ids(
            self._request(ALL_WAREHOUSES_VALUE),
            company=self.company,
        )
        self.assertEqual(set(result), {self.a.id, self.b.id})

    def test_active_warehouse_narrows_read_scope(self):
        result = get_request_read_warehouse_ids(
            self._request(str(self.a.id)),
            company=self.company,
        )
        self.assertEqual(result, (self.a.id,))


class PS5B1ViewAndStockContractTests(SimpleTestCase):
    def test_sales_read_views_use_order_read_scope(self):
        for function in (
            sales_views.lista_pedidos,
            sales_views.detalle_pedido,
            sales_views.pedidos_en_curso,
            sales_views.incidencias_pedidos,
            sales_views.abrir_chat_flotante_pedido,
        ):
            source = inspect.getsource(inspect.unwrap(function))
            with self.subTest(function=function.__name__):
                self.assertIn("scope_orders_for_read", source)
                self.assertIn("get_request_read_warehouse_ids", source)

    def test_payment_read_views_inherit_order_scope(self):
        for function in (
            payment_views.lista_pagos,
            payment_views.detalle_pago,
            payment_views.pagos_pedido,
            payment_views.abrir_chat_flotante_pago,
        ):
            source = inspect.getsource(inspect.unwrap(function))
            with self.subTest(function=function.__name__):
                self.assertTrue(
                    "scope_orders_for_read" in source
                    or "scope_payments_for_read" in source
                )
                self.assertIn("get_request_read_warehouse_ids", source)

    def test_register_payment_has_read_scope_and_activity_authorization(self):
        source = inspect.getsource(inspect.unwrap(payment_views.registrar_pago))
        self.assertIn("scope_orders_for_read", source)
        self.assertIn("_authorize_payment_order_activity_route", source)
        self.assertIn('"registrar_pago"', source)
        self.assertIn("plan_if_missing=True", source)

    def test_detail_stock_is_limited_to_read_warehouses(self):
        source = inspect.getsource(inspect.unwrap(sales_views.detalle_pedido))
        self.assertIn("warehouse_ids=read_warehouse_ids", source)

    def test_pickable_stock_keeps_backward_compatible_optional_scope(self):
        signature = inspect.signature(
            fulfillment_planning.get_pickable_stock_for_variant
        )
        source = inspect.getsource(
            fulfillment_planning.get_pickable_stock_for_variant
        )
        self.assertIn("warehouse_ids", signature.parameters)
        self.assertIsNone(signature.parameters["warehouse_ids"].default)
        self.assertIn("warehouse_id__in=warehouse_ids", source)

    def test_payment_list_uses_database_aggregate_not_python_sum(self):
        source = inspect.getsource(inspect.unwrap(payment_views.lista_pagos))
        self.assertNotIn("sum(", source)
        self.assertIn('aggregate(total=Sum("amount"))', source)
