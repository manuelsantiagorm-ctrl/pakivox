from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import (
    Capability,
    EmployeeJobAssignment,
    JobTemplate,
    UserProfile,
    UserWarehouseAccess,
)
from accounts.simple_employee_access import (
    _required_capability_codes,
    ensure_company_activity_templates,
)
from companies.models import Company
from crm.models import Customer
from inventory.models import (
    Product,
    ProductVariant,
    Stock,
    Warehouse,
)
from sales.models import (
    Order,
    OrderItem,
)
from subscriptions.models import (
    Plan,
    Subscription,
)
from warehouse.models import (
    OrderFulfillment,
    VariantLocation,
    WarehouseLocation,
)


User = get_user_model()


class PS6P5C4PostgreSQLSalesMutationIsolationTests(
    TestCase
):
    """
    PS-6P.5C4B.

    Prueba funcional real contra PostgreSQL.

    No parchea:
    - authorize_activity_route;
    - resolve_order_mutation_authorization_warehouses;
    - fulfillment planner;
    - consultas de stock;
    - company scope;
    - warehouse scope.

    Solo neutraliza la notificacion WhatsApp posterior a un ADD exitoso,
    porque una integracion externa no forma parte del contrato de C4.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        if connection.vendor != "postgresql":
            raise AssertionError(
                "PS-6P.5C4B debe ejecutarse con PostgreSQL."
            )

    @classmethod
    def setUpTestData(cls):
        today = timezone.localdate()

        # ====================================================
        # EMPRESA PRINCIPAL
        # ====================================================

        cls.company = Company.objects.create(
            name="PS6P5C4 Empresa",
            slug="ps6p5c4-empresa",
        )

        cls.plan = Plan.objects.create(
            name="PS6P5C4 Plan",
            slug="ps6p5c4-plan",
            max_users=50,
            max_warehouses=10,
        )

        Subscription.objects.create(
            company=cls.company,
            plan=cls.plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=today,
            end_date=today + timedelta(days=30),
        )

        # ====================================================
        # EMPRESA AJENA
        # ====================================================

        cls.foreign_company = Company.objects.create(
            name="PS6P5C4 Empresa Ajena",
            slug="ps6p5c4-empresa-ajena",
        )

        cls.foreign_plan = Plan.objects.create(
            name="PS6P5C4 Plan Ajeno",
            slug="ps6p5c4-plan-ajeno",
            max_users=50,
            max_warehouses=10,
        )

        Subscription.objects.create(
            company=cls.foreign_company,
            plan=cls.foreign_plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=today,
            end_date=today + timedelta(days=30),
        )

        # ====================================================
        # USUARIO SALES PRINCIPAL
        # ====================================================

        cls.user = User.objects.create_user(
            username="ps6p5c4-sales",
            email="ps6p5c4-sales@test.local",
            password="PS6P5C4-test-password",
        )

        UserProfile.objects.update_or_create(
            user=cls.user,
            defaults={
                "company": cls.company,
                "role": "sales",
                "is_active": True,
                "employment_status": "active",
            },
        )

        # ====================================================
        # USUARIO DE EMPRESA AJENA
        # ====================================================

        cls.foreign_user = User.objects.create_user(
            username="ps6p5c4-foreign",
            email="ps6p5c4-foreign@test.local",
            password="PS6P5C4-test-password",
        )

        UserProfile.objects.update_or_create(
            user=cls.foreign_user,
            defaults={
                "company": cls.foreign_company,
                "role": "sales",
                "is_active": True,
                "employment_status": "active",
            },
        )

        # ====================================================
        # ACTIVIDAD SIMPLE SALES
        # ====================================================

        # Este test debe ser independiente del orden de ejecucion.
        #
        # TransactionTestCase hace flush entre pruebas y los datos
        # creados por data migrations no son recreados despues del
        # flush. Por eso materializamos aqui las capabilities SIMPLE
        # necesarias para este fixture.
        for capability_code in sorted(
            _required_capability_codes()
        ):
            area_code = capability_code.split(
                ".",
                1,
            )[0]

            Capability.objects.update_or_create(
                code=capability_code,
                defaults={
                    "name": capability_code,
                    "description": (
                        "PS-6P.5C4 PostgreSQL fixture"
                    ),
                    "area_code": area_code,
                    "is_active": True,
                },
            )

        ensure_company_activity_templates(
            cls.company
        )

        sales_job = JobTemplate.objects.get(
            company=cls.company,
            code="activity_sales_customers",
            is_system=True,
            is_active=True,
        )

        EmployeeJobAssignment.objects.create(
            company=cls.company,
            user=cls.user,
            job_template=sales_job,
            is_active=True,
            is_primary=True,
        )

        # ====================================================
        # BODEGAS
        # ====================================================

        cls.warehouse_a = Warehouse.objects.create(
            company=cls.company,
            name="PS6P5C4 Bodega A",
            code="PS6P5C4-A",
            is_active=True,
        )

        cls.warehouse_b = Warehouse.objects.create(
            company=cls.company,
            name="PS6P5C4 Bodega B",
            code="PS6P5C4-B",
            is_active=True,
        )

        cls.foreign_warehouse = Warehouse.objects.create(
            company=cls.foreign_company,
            name="PS6P5C4 Bodega Ajena",
            code="PS6P5C4-X",
            is_active=True,
        )

        # ====================================================
        # UBICACIONES
        # ====================================================

        cls.location_a = WarehouseLocation.objects.create(
            company=cls.company,
            warehouse=cls.warehouse_a,
            aisle="A",
            rack="R1",
            level="1",
            bin_code="PS6P5C4-A-01",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )

        cls.location_b = WarehouseLocation.objects.create(
            company=cls.company,
            warehouse=cls.warehouse_b,
            aisle="B",
            rack="R1",
            level="1",
            bin_code="PS6P5C4-B-01",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )

        cls.foreign_location = WarehouseLocation.objects.create(
            company=cls.foreign_company,
            warehouse=cls.foreign_warehouse,
            aisle="X",
            rack="R1",
            level="1",
            bin_code="PS6P5C4-X-01",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )

        # ====================================================
        # CLIENTES
        # ====================================================

        cls.customer = Customer.objects.create(
            company=cls.company,
            assigned_to=cls.user,
            full_name="Cliente PS6P5C4",
            customer_type="customer",
            is_active=True,
        )

        cls.foreign_customer = Customer.objects.create(
            company=cls.foreign_company,
            assigned_to=cls.foreign_user,
            full_name="Cliente Ajeno PS6P5C4",
            customer_type="customer",
            is_active=True,
        )

        # ====================================================
        # PRODUCTO A
        # ====================================================

        cls.product_a = Product.objects.create(
            company=cls.company,
            name="Producto A PS6P5C4",
            base_sku="PS6P5C4-PROD-A",
            stock_mode="unit",
            base_unit="pza",
            commercial_unit="pza",
            conversion_factor=Decimal("1"),
            cost_price=Decimal("5.00"),
            sale_price=Decimal("10.00"),
            wholesale_price=Decimal("9.00"),
            is_active=True,
        )

        cls.variant_a = ProductVariant.objects.create(
            company=cls.company,
            product=cls.product_a,
            sku="PS6P5C4-SKU-A",
            cost_price=Decimal("5.00"),
            sale_price=Decimal("10.00"),
            wholesale_price=Decimal("9.00"),
            inventory_control="normal",
            is_active=True,
        )

        # ====================================================
        # PRODUCTO B
        # ====================================================

        cls.product_b = Product.objects.create(
            company=cls.company,
            name="Producto B PS6P5C4",
            base_sku="PS6P5C4-PROD-B",
            stock_mode="unit",
            base_unit="pza",
            commercial_unit="pza",
            conversion_factor=Decimal("1"),
            cost_price=Decimal("7.00"),
            sale_price=Decimal("15.00"),
            wholesale_price=Decimal("14.00"),
            is_active=True,
        )

        cls.variant_b = ProductVariant.objects.create(
            company=cls.company,
            product=cls.product_b,
            sku="PS6P5C4-SKU-B",
            cost_price=Decimal("7.00"),
            sale_price=Decimal("15.00"),
            wholesale_price=Decimal("14.00"),
            inventory_control="normal",
            is_active=True,
        )

        # ====================================================
        # PRODUCTO AJENO
        # ====================================================

        cls.foreign_product = Product.objects.create(
            company=cls.foreign_company,
            name="Producto Ajeno PS6P5C4",
            base_sku="PS6P5C4-PROD-X",
            stock_mode="unit",
            base_unit="pza",
            commercial_unit="pza",
            conversion_factor=Decimal("1"),
            cost_price=Decimal("1.00"),
            sale_price=Decimal("2.00"),
            wholesale_price=Decimal("2.00"),
            is_active=True,
        )

        cls.foreign_variant = ProductVariant.objects.create(
            company=cls.foreign_company,
            product=cls.foreign_product,
            sku="PS6P5C4-SKU-X",
            cost_price=Decimal("1.00"),
            sale_price=Decimal("2.00"),
            wholesale_price=Decimal("2.00"),
            inventory_control="normal",
            is_active=True,
        )

        # ====================================================
        # STOCK INICIAL
        #
        # SKU A -> Bodega A
        # SKU B -> Bodega B
        # ====================================================

        cls._set_stock_class(
            company=cls.company,
            variant=cls.variant_a,
            warehouse=cls.warehouse_a,
            location=cls.location_a,
            quantity=20,
        )

        cls._set_stock_class(
            company=cls.company,
            variant=cls.variant_a,
            warehouse=cls.warehouse_b,
            location=cls.location_b,
            quantity=0,
        )

        cls._set_stock_class(
            company=cls.company,
            variant=cls.variant_b,
            warehouse=cls.warehouse_a,
            location=cls.location_a,
            quantity=0,
        )

        cls._set_stock_class(
            company=cls.company,
            variant=cls.variant_b,
            warehouse=cls.warehouse_b,
            location=cls.location_b,
            quantity=20,
        )

        cls._set_stock_class(
            company=cls.foreign_company,
            variant=cls.foreign_variant,
            warehouse=cls.foreign_warehouse,
            location=cls.foreign_location,
            quantity=20,
        )

    @classmethod
    def _set_stock_class(
        cls,
        *,
        company,
        variant,
        warehouse,
        location,
        quantity,
    ):
        Stock.objects.update_or_create(
            company=company,
            warehouse=warehouse,
            variant=variant,
            defaults={
                "physical_stock": quantity,
                "reserved_stock": 0,
                "committed_stock": 0,
            },
        )

        VariantLocation.objects.update_or_create(
            company=company,
            variant=variant,
            location=location,
            defaults={
                "quantity": quantity,
                "is_primary": True,
            },
        )

    def setUp(self):
        self.client.force_login(
            self.user
        )

        self.whatsapp_patcher = patch(
            "sales.views."
            "notificar_producto_agregado_whatsapp",
            return_value=None,
        )

        self.whatsapp_patcher.start()

        self.addCleanup(
            self.whatsapp_patcher.stop
        )

    # ========================================================
    # HELPERS
    # ========================================================

    def _grant(
        self,
        warehouse,
        level=UserWarehouseAccess.LEVEL_OPERATE,
    ):
        access, _ = UserWarehouseAccess.objects.update_or_create(
            company=self.company,
            user=self.user,
            warehouse=warehouse,
            defaults={
                "access_level": level,
                "is_active": True,
            },
        )

        return access

    def _set_stock(
        self,
        variant,
        warehouse,
        quantity,
    ):
        location = {
            self.warehouse_a.pk: self.location_a,
            self.warehouse_b.pk: self.location_b,
        }[
            warehouse.pk
        ]

        Stock.objects.update_or_create(
            company=self.company,
            warehouse=warehouse,
            variant=variant,
            defaults={
                "physical_stock": quantity,
                "reserved_stock": 0,
                "committed_stock": 0,
            },
        )

        VariantLocation.objects.update_or_create(
            company=self.company,
            variant=variant,
            location=location,
            defaults={
                "quantity": quantity,
                "is_primary": True,
            },
        )

    def _order(
        self,
        suffix,
        *,
        variant=None,
        quantity=1,
        warehouses=None,
    ):
        order = Order.objects.create(
            company=self.company,
            customer=self.customer,
            created_by=self.user,
            folio=f"PS6P5C4-{suffix}",
            status="draft",
        )

        item = None

        if variant is not None:
            item = OrderItem.objects.create(
                order=order,
                variant=variant,
                quantity=quantity,
                unit_price=variant.sale_price,
            )

        for sequence, warehouse in enumerate(
            warehouses or (),
            start=1,
        ):
            OrderFulfillment.objects.create(
                company=self.company,
                order=order,
                warehouse=warehouse,
                sequence=sequence,
                status="pending",
            )

        return order, item

    def _foreign_order(
        self,
        suffix,
    ):
        order = Order.objects.create(
            company=self.foreign_company,
            customer=self.foreign_customer,
            created_by=self.foreign_user,
            folio=f"PS6P5C4-X-{suffix}",
            status="draft",
        )

        item = OrderItem.objects.create(
            order=order,
            variant=self.foreign_variant,
            quantity=1,
            unit_price=self.foreign_variant.sale_price,
        )

        OrderFulfillment.objects.create(
            company=self.foreign_company,
            order=order,
            warehouse=self.foreign_warehouse,
            sequence=1,
            status="pending",
        )

        return order, item

    def _add_url(
        self,
        order,
    ):
        return reverse(
            "sales:agregar_producto_pedido",
            kwargs={
                "order_id": order.pk,
            },
        )

    def _update_url(
        self,
        order,
        item,
    ):
        return reverse(
            "sales:actualizar_producto_pedido",
            kwargs={
                "order_id": order.pk,
                "item_id": item.pk,
            },
        )

    def _delete_url(
        self,
        order,
        item,
    ):
        return reverse(
            "sales:eliminar_producto_pedido",
            kwargs={
                "order_id": order.pk,
                "item_id": item.pk,
            },
        )

    # ========================================================
    # 1. A -> A
    # ========================================================

    def test_a_to_a_add_is_allowed_with_operate_a(self):
        self._grant(
            self.warehouse_a
        )

        order, item = self._order(
            "A-TO-A",
            variant=self.variant_a,
            quantity=1,
            warehouses=(
                self.warehouse_a,
            ),
        )

        fulfillment_count = (
            OrderFulfillment.objects.filter(
                order=order
            ).count()
        )

        response = self.client.post(
            self._add_url(order),
            {
                "variant": str(
                    self.variant_a.pk
                ),
                "quantity": "1",
                "unit_price": "10.00",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        item.refresh_from_db()

        self.assertEqual(
            item.quantity,
            2,
        )

        self.assertEqual(
            OrderFulfillment.objects.filter(
                order=order
            ).count(),
            fulfillment_count,
        )

    # ========================================================
    # 2. A -> A+B DENIED
    # ========================================================

    def test_add_requiring_b_is_denied_without_operate_b_and_does_not_mutate(self):
        self._grant(
            self.warehouse_a
        )

        order, original = self._order(
            "ADD-B-DENY",
            variant=self.variant_a,
            quantity=1,
            warehouses=(
                self.warehouse_a,
            ),
        )

        before_items = (
            OrderItem.objects.filter(
                order=order
            ).count()
        )

        before_fulfillments = (
            OrderFulfillment.objects.filter(
                order=order
            ).count()
        )

        response = self.client.post(
            self._add_url(order),
            {
                "variant": str(
                    self.variant_b.pk
                ),
                "quantity": "1",
                "unit_price": "15.00",
            },
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        self.assertEqual(
            OrderItem.objects.filter(
                order=order
            ).count(),
            before_items,
        )

        self.assertFalse(
            OrderItem.objects.filter(
                order=order,
                variant=self.variant_b,
            ).exists()
        )

        self.assertEqual(
            OrderFulfillment.objects.filter(
                order=order
            ).count(),
            before_fulfillments,
        )

        original.refresh_from_db()

        self.assertEqual(
            original.quantity,
            1,
        )

    # ========================================================
    # 3. A -> A+B ALLOWED
    # ========================================================

    def test_add_requiring_b_is_allowed_with_operate_a_and_b(self):
        self._grant(
            self.warehouse_a
        )

        self._grant(
            self.warehouse_b
        )

        order, _ = self._order(
            "ADD-B-ALLOW",
            variant=self.variant_a,
            quantity=1,
            warehouses=(
                self.warehouse_a,
            ),
        )

        fulfillment_count = (
            OrderFulfillment.objects.filter(
                order=order
            ).count()
        )

        response = self.client.post(
            self._add_url(order),
            {
                "variant": str(
                    self.variant_b.pk
                ),
                "quantity": "1",
                "unit_price": "15.00",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        created = OrderItem.objects.get(
            order=order,
            variant=self.variant_b,
        )

        self.assertEqual(
            created.quantity,
            1,
        )

        # El preview de autorizacion NO debe materializar
        # fulfillments nuevos.
        self.assertEqual(
            OrderFulfillment.objects.filter(
                order=order
            ).count(),
            fulfillment_count,
        )

    # ========================================================
    # 4. A -> B PROSPECTIVO DENIED
    #
    # Estado historico A.
    # Nueva cantidad solo cabe completa en B.
    # La union de autoridad debe ser A+B.
    # ========================================================

    def test_update_shift_a_to_b_is_denied_without_operate_b_and_quantity_is_unchanged(self):
        self._grant(
            self.warehouse_a
        )

        self._set_stock(
            self.variant_a,
            self.warehouse_a,
            3,
        )

        self._set_stock(
            self.variant_a,
            self.warehouse_b,
            20,
        )

        order, item = self._order(
            "UPDATE-B-DENY",
            variant=self.variant_a,
            quantity=2,
            warehouses=(
                self.warehouse_a,
            ),
        )

        response = self.client.post(
            self._update_url(
                order,
                item,
            ),
            {
                "quantity": "5",
                "unit_price": "10.00",
            },
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        item.refresh_from_db()

        self.assertEqual(
            item.quantity,
            2,
        )

    # ========================================================
    # 5. A -> B PROSPECTIVO ALLOWED
    # ========================================================

    def test_update_shift_a_to_b_is_allowed_with_operate_a_and_b(self):
        self._grant(
            self.warehouse_a
        )

        self._grant(
            self.warehouse_b
        )

        self._set_stock(
            self.variant_a,
            self.warehouse_a,
            3,
        )

        self._set_stock(
            self.variant_a,
            self.warehouse_b,
            20,
        )

        order, item = self._order(
            "UPDATE-B-ALLOW",
            variant=self.variant_a,
            quantity=2,
            warehouses=(
                self.warehouse_a,
            ),
        )

        response = self.client.post(
            self._update_url(
                order,
                item,
            ),
            {
                "quantity": "5",
                "unit_price": "10.00",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        item.refresh_from_db()

        self.assertEqual(
            item.quantity,
            5,
        )

    # ========================================================
    # 6. VIEW != OPERATE
    # ========================================================

    def test_view_level_does_not_allow_sales_mutation(self):
        self._grant(
            self.warehouse_a,
            UserWarehouseAccess.LEVEL_VIEW,
        )

        order, item = self._order(
            "VIEW-DENY",
            variant=self.variant_a,
            quantity=1,
            warehouses=(
                self.warehouse_a,
            ),
        )

        response = self.client.post(
            self._add_url(order),
            {
                "variant": str(
                    self.variant_a.pk
                ),
                "quantity": "1",
                "unit_price": "10.00",
            },
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        item.refresh_from_db()

        self.assertEqual(
            item.quantity,
            1,
        )

    # ========================================================
    # 7. FOREIGN COMPANY ORDER -> 404
    # ========================================================

    def test_foreign_company_order_is_404(self):
        self._grant(
            self.warehouse_a
        )

        foreign_order, _ = self._foreign_order(
            "ORDER-404"
        )

        response = self.client.post(
            self._add_url(
                foreign_order
            ),
            {
                "variant": str(
                    self.variant_a.pk
                ),
                "quantity": "1",
                "unit_price": "10.00",
            },
        )

        self.assertEqual(
            response.status_code,
            404,
        )

    # ========================================================
    # 8. FOREIGN VARIANT -> 404
    # ========================================================

    def test_foreign_company_variant_is_404_and_order_is_unchanged(self):
        self._grant(
            self.warehouse_a
        )

        order, original = self._order(
            "VARIANT-404",
            variant=self.variant_a,
            quantity=1,
            warehouses=(
                self.warehouse_a,
            ),
        )

        before_count = (
            OrderItem.objects.filter(
                order=order
            ).count()
        )

        response = self.client.post(
            self._add_url(order),
            {
                "variant": str(
                    self.foreign_variant.pk
                ),
                "quantity": "1",
                "unit_price": "2.00",
            },
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        self.assertEqual(
            OrderItem.objects.filter(
                order=order
            ).count(),
            before_count,
        )

        original.refresh_from_db()

        self.assertEqual(
            original.quantity,
            1,
        )

    # ========================================================
    # 9. ITEM_ID DE OTRO PEDIDO -> 404
    # ========================================================

    def test_item_from_another_order_is_404_and_neither_item_changes(self):
        self._grant(
            self.warehouse_a
        )

        order_1, item_1 = self._order(
            "ITEM-404-A",
            variant=self.variant_a,
            quantity=1,
            warehouses=(
                self.warehouse_a,
            ),
        )

        order_2, item_2 = self._order(
            "ITEM-404-B",
            variant=self.variant_a,
            quantity=2,
            warehouses=(
                self.warehouse_a,
            ),
        )

        response = self.client.post(
            reverse(
                "sales:actualizar_producto_pedido",
                kwargs={
                    "order_id": order_1.pk,
                    "item_id": item_2.pk,
                },
            ),
            {
                "quantity": "9",
                "unit_price": "10.00",
            },
        )

        self.assertEqual(
            response.status_code,
            404,
        )

        item_1.refresh_from_db()
        item_2.refresh_from_db()

        self.assertEqual(
            item_1.quantity,
            1,
        )

        self.assertEqual(
            item_2.quantity,
            2,
        )

    # ========================================================
    # 10. DELETE DENIED -> NO DELETE
    # ========================================================

    def test_delete_requires_operate_on_every_current_warehouse_and_does_not_delete(self):
        self._grant(
            self.warehouse_a
        )

        order, item = self._order(
            "DELETE-DENY",
            variant=self.variant_a,
            quantity=1,
            warehouses=(
                self.warehouse_a,
                self.warehouse_b,
            ),
        )

        response = self.client.post(
            self._delete_url(
                order,
                item,
            ),
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        self.assertTrue(
            OrderItem.objects.filter(
                pk=item.pk,
                order=order,
            ).exists()
        )

        self.assertEqual(
            OrderFulfillment.objects.filter(
                order=order
            ).count(),
            2,
        )

    # ========================================================
    # 11. PRIMERA LINEA DE PEDIDO VACIO
    #
    # No existe alcance historico.
    # Debe autorizarse exclusivamente con el alcance prospectivo.
    # ========================================================

    def test_first_line_of_empty_order_uses_prospective_warehouse_scope(self):
        self._grant(
            self.warehouse_b
        )

        order, item = self._order(
            "EMPTY-FIRST-LINE",
            variant=None,
            warehouses=(),
        )

        self.assertIsNone(
            item
        )

        response = self.client.post(
            self._add_url(order),
            {
                "variant": str(
                    self.variant_b.pk
                ),
                "quantity": "1",
                "unit_price": "15.00",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        created = OrderItem.objects.get(
            order=order,
            variant=self.variant_b,
        )

        self.assertEqual(
            created.quantity,
            1,
        )

        # La autorizacion prospectiva es READ ONLY.
        self.assertEqual(
            OrderFulfillment.objects.filter(
                order=order
            ).count(),
            0,
        )