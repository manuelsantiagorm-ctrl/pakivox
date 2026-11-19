from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier, Event, Lock
from unittest.mock import patch

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.color import no_style
from django.db import close_old_connections, connection
from django.test import Client, TransactionTestCase
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
from sales import views as sales_views
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


class PS6P5C5PostgreSQLSalesMutationConcurrencyTests(
    TransactionTestCase
):
    """
    Concurrencia real PS-6P.5C5.

    TransactionTestCase es obligatorio:
    cada thread necesita su propia conexion PostgreSQL y los commits
    deben ser visibles entre conexiones.

    Contratos:
    1. El row lock del Order serializa dos ADD simultaneos.
    2. Doble clic simultaneo no puede exceder el stock del pedido.
    3. Una mutacion concurrente hacia B sigue requiriendo OPERATE B.
    """

    # No usamos reset_sequences=True.
    # C5 se ejecuta con --keepdb y puede conservar datos
    # sembrados por migraciones en la primera prueba.

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        if connection.vendor != "postgresql":
            raise AssertionError(
                "PS-6P.5C5 debe ejecutarse con PostgreSQL."
            )

        # Guardamos el baseline completo de la DB de pruebas.
        #
        # TransactionTestCase necesita transacciones reales y hace
        # flush entre casos. C5 debe restaurar exactamente lo que
        # existia antes de comenzar la clase.
        cls._ps6p5c5_database_snapshot = (
            connection.creation.serialize_db_to_string()
        )

    @classmethod
    def tearDownClass(cls):
        try:
            snapshot = getattr(
                cls,
                "_ps6p5c5_database_snapshot",
                None,
            )

            if snapshot is not None:
                close_old_connections()

                # El flush normal de TransactionTestCase puede
                # emitir post_migrate y recrear ContentType /
                # Permission. Antes de restaurar el snapshot,
                # vaciamos de nuevo la DB SIN post_migrate.
                #
                # Esto ocurre exclusivamente sobre la DB de
                # pruebas administrada por Django.
                call_command(
                    "flush",
                    verbosity=0,
                    interactive=False,
                    database=connection.alias,
                    reset_sequences=False,
                    allow_cascade=True,
                    inhibit_post_migrate=True,
                )

                connection.creation.deserialize_db_from_string(
                    snapshot
                )

                # Los objetos restaurados conservan sus PK.
                # Ajustamos las secuencias PostgreSQL al MAX(pk)
                # restaurado para evitar futuras colisiones.
                statements = (
                    connection.ops.sequence_reset_sql(
                        no_style(),
                        apps.get_models(),
                    )
                )

                if statements:
                    with connection.cursor() as cursor:
                        for statement in statements:
                            cursor.execute(
                                statement
                            )

        finally:
            super().tearDownClass()

    def setUp(self):
        if connection.vendor != "postgresql":
            raise AssertionError(
                "PS-6P.5C5 debe ejecutarse con PostgreSQL."
            )

        today = timezone.localdate()

        # ====================================================
        # EMPRESA
        # ====================================================

        self.company = Company.objects.create(
            name="PS6P5C5 Empresa",
            slug="ps6p5c5-empresa",
        )

        # La base --keepdb puede conservar un Plan sembrado
        # por migraciones. Reutilizarlo evita depender del estado
        # de la secuencia PK de subscriptions_plan.
        plan = Plan.objects.order_by(
            "pk"
        ).first()

        if plan is None:
            plan = Plan.objects.create(
                name="PS6P5C5 Plan",
                slug="ps6p5c5-plan",
                max_users=50,
                max_warehouses=10,
            )

        Subscription.objects.create(
            company=self.company,
            plan=plan,
            status=Subscription.STATUS_ACTIVE,
            start_date=today,
            end_date=today + timedelta(days=30),
        )

        # ====================================================
        # USUARIO SALES
        # ====================================================

        self.user = User.objects.create_user(
            username="ps6p5c5-sales",
            email="ps6p5c5-sales@test.local",
            password="PS6P5C5-test-password",
        )

        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={
                "company": self.company,
                "role": "sales",
                "is_active": True,
                "employment_status": "active",
            },
        )

        # TransactionTestCase hace flush entre pruebas.
        # Las data migrations no se reejecutan despues del flush,
        # asi que este fixture materializa explicitamente el
        # catalogo minimo requerido por SIMPLE.
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
                        "PS-6P.5C5 concurrency fixture"
                    ),
                    "area_code": area_code,
                    "is_active": True,
                },
            )

        ensure_company_activity_templates(
            self.company
        )

        sales_job = JobTemplate.objects.get(
            company=self.company,
            code="activity_sales_customers",
            is_system=True,
            is_active=True,
        )

        EmployeeJobAssignment.objects.create(
            company=self.company,
            user=self.user,
            job_template=sales_job,
            is_active=True,
            is_primary=True,
        )

        # ====================================================
        # BODEGAS
        # ====================================================

        self.warehouse_a = Warehouse.objects.create(
            company=self.company,
            name="PS6P5C5 A",
            code="PS6P5C5-A",
            is_active=True,
        )

        self.warehouse_b = Warehouse.objects.create(
            company=self.company,
            name="PS6P5C5 B",
            code="PS6P5C5-B",
            is_active=True,
        )

        # ====================================================
        # UBICACIONES
        # ====================================================

        self.location_a = WarehouseLocation.objects.create(
            company=self.company,
            warehouse=self.warehouse_a,
            aisle="A",
            rack="R1",
            level="1",
            bin_code="PS6P5C5-A-01",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )

        self.location_b = WarehouseLocation.objects.create(
            company=self.company,
            warehouse=self.warehouse_b,
            aisle="B",
            rack="R1",
            level="1",
            bin_code="PS6P5C5-B-01",
            status=WarehouseLocation.STATUS_ACTIVE,
            is_virtual=False,
            is_active=True,
        )

        # ====================================================
        # CLIENTE
        # ====================================================

        self.customer = Customer.objects.create(
            company=self.company,
            assigned_to=self.user,
            full_name="Cliente PS6P5C5",
            customer_type="customer",
            is_active=True,
        )

        # ====================================================
        # SKU A
        # ====================================================

        product_a = Product.objects.create(
            company=self.company,
            name="Producto A PS6P5C5",
            base_sku="PS6P5C5-PROD-A",
            stock_mode="unit",
            base_unit="pza",
            commercial_unit="pza",
            conversion_factor=Decimal("1"),
            cost_price=Decimal("5.00"),
            sale_price=Decimal("10.00"),
            wholesale_price=Decimal("9.00"),
            is_active=True,
        )

        self.variant_a = ProductVariant.objects.create(
            company=self.company,
            product=product_a,
            sku="PS6P5C5-SKU-A",
            cost_price=Decimal("5.00"),
            sale_price=Decimal("10.00"),
            wholesale_price=Decimal("9.00"),
            inventory_control="normal",
            is_active=True,
        )

        # ====================================================
        # SKU B
        # ====================================================

        product_b = Product.objects.create(
            company=self.company,
            name="Producto B PS6P5C5",
            base_sku="PS6P5C5-PROD-B",
            stock_mode="unit",
            base_unit="pza",
            commercial_unit="pza",
            conversion_factor=Decimal("1"),
            cost_price=Decimal("7.00"),
            sale_price=Decimal("15.00"),
            wholesale_price=Decimal("14.00"),
            is_active=True,
        )

        self.variant_b = ProductVariant.objects.create(
            company=self.company,
            product=product_b,
            sku="PS6P5C5-SKU-B",
            cost_price=Decimal("7.00"),
            sale_price=Decimal("15.00"),
            wholesale_price=Decimal("14.00"),
            inventory_control="normal",
            is_active=True,
        )

        # ====================================================
        # STOCK DEFAULT
        # ====================================================

        self._set_stock(
            self.variant_a,
            self.warehouse_a,
            20,
        )

        self._set_stock(
            self.variant_a,
            self.warehouse_b,
            0,
        )

        self._set_stock(
            self.variant_b,
            self.warehouse_a,
            0,
        )

        self._set_stock(
            self.variant_b,
            self.warehouse_b,
            20,
        )

        # ====================================================
        # EXTERNAL SIDE EFFECT OFF
        # ====================================================

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
        return UserWarehouseAccess.objects.update_or_create(
            company=self.company,
            user=self.user,
            warehouse=warehouse,
            defaults={
                "access_level": level,
                "is_active": True,
            },
        )[0]

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
        warehouses=(),
    ):
        order = Order.objects.create(
            company=self.company,
            customer=self.customer,
            created_by=self.user,
            folio=f"PS6P5C5-{suffix}",
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
            warehouses,
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

    def _logged_client(self):
        client = Client()

        client.force_login(
            self.user
        )

        return client

    @staticmethod
    def _thread_post(
        client,
        url,
        data,
        *,
        barrier=None,
    ):
        close_old_connections()

        try:
            if barrier is not None:
                barrier.wait(
                    timeout=10
                )

            response = client.post(
                url,
                data,
            )

            return response.status_code

        finally:
            close_old_connections()

    # ========================================================
    # 1.
    # ORDER ROW LOCK MUST SERIALIZE TWO FIRST-LINE ADDS
    # ========================================================

    def test_order_row_lock_serializes_two_simultaneous_first_line_adds(self):
        self._grant(
            self.warehouse_a
        )

        order, _ = self._order(
            "ROW-LOCK",
        )

        url = self._add_url(
            order
        )

        data = {
            "variant": str(
                self.variant_a.pk
            ),
            "quantity": "1",
            "unit_price": "10.00",
        }

        client_1 = self._logged_client()
        client_2 = self._logged_client()

        original = (
            sales_views
            ._authorize_order_mutation_activity_route
        )

        counter_lock = Lock()

        first_inside_locked_section = Event()
        second_inside_locked_section = Event()
        release_first = Event()

        call_count = {
            "value": 0,
        }

        def instrumented_authorization(
            *args,
            **kwargs,
        ):
            with counter_lock:
                call_count["value"] += 1
                call_number = call_count["value"]

            if call_number == 1:
                first_inside_locked_section.set()

                released = release_first.wait(
                    timeout=10
                )

                if not released:
                    raise AssertionError(
                        "Timeout esperando liberar la primera transaccion."
                    )

            else:
                second_inside_locked_section.set()

            return original(
                *args,
                **kwargs,
            )

        with patch(
            "sales.views."
            "_authorize_order_mutation_activity_route",
            side_effect=instrumented_authorization,
        ):
            with ThreadPoolExecutor(
                max_workers=2
            ) as pool:

                first = pool.submit(
                    self._thread_post,
                    client_1,
                    url,
                    data,
                )

                reached = first_inside_locked_section.wait(
                    timeout=10
                )

                self.assertTrue(
                    reached,
                    msg=(
                        "La primera peticion no alcanzo "
                        "la seccion protegida por Order.select_for_update()."
                    ),
                )

                second = pool.submit(
                    self._thread_post,
                    client_2,
                    url,
                    data,
                )

                try:
                    second_reached_too_early = (
                        second_inside_locked_section.wait(
                            timeout=1.5
                        )
                    )

                    self.assertFalse(
                        second_reached_too_early,
                        msg=(
                            "La segunda peticion entro a la seccion "
                            "de mutacion mientras la primera conservaba "
                            "el row lock del pedido."
                        ),
                    )

                finally:
                    release_first.set()

                status_1 = first.result(
                    timeout=15
                )

                status_2 = second.result(
                    timeout=15
                )

        self.assertEqual(
            sorted(
                (
                    status_1,
                    status_2,
                )
            ),
            [
                302,
                302,
            ],
        )

        items = list(
            OrderItem.objects.filter(
                order=order,
                variant=self.variant_a,
            )
        )

        self.assertEqual(
            len(items),
            1,
            msg=(
                "Dos ADD simultaneos no deben crear "
                "dos renglones del mismo SKU."
            ),
        )

        self.assertEqual(
            items[0].quantity,
            2,
        )

        self.assertEqual(
            OrderFulfillment.objects.filter(
                order=order
            ).count(),
            0,
            msg=(
                "La autorizacion prospectiva no debe "
                "materializar fulfillments."
            ),
        )

    # ========================================================
    # 2.
    # DOUBLE CLICK CANNOT EXCEED SAME-ORDER STOCK
    # ========================================================

    def test_two_simultaneous_adds_do_not_exceed_available_stock(self):
        self._grant(
            self.warehouse_a
        )

        # Cuando el plan prospectivo queda sin stock,
        # el resolver usa fallback conservador a todas
        # las bodegas activas. Concedemos B para aislar
        # este test a concurrencia/stock, no permisos.
        self._grant(
            self.warehouse_b
        )

        self._set_stock(
            self.variant_a,
            self.warehouse_a,
            1,
        )

        self._set_stock(
            self.variant_a,
            self.warehouse_b,
            0,
        )

        order, _ = self._order(
            "STOCK-CEILING",
        )

        url = self._add_url(
            order
        )

        data = {
            "variant": str(
                self.variant_a.pk
            ),
            "quantity": "1",
            "unit_price": "10.00",
        }

        client_1 = self._logged_client()
        client_2 = self._logged_client()

        barrier = Barrier(
            2
        )

        with ThreadPoolExecutor(
            max_workers=2
        ) as pool:

            future_1 = pool.submit(
                self._thread_post,
                client_1,
                url,
                data,
                barrier=barrier,
            )

            future_2 = pool.submit(
                self._thread_post,
                client_2,
                url,
                data,
                barrier=barrier,
            )

            status_1 = future_1.result(
                timeout=20
            )

            status_2 = future_2.result(
                timeout=20
            )

        # Tanto exito como "stock insuficiente"
        # regresan al detalle por redirect.
        self.assertEqual(
            status_1,
            302,
        )

        self.assertEqual(
            status_2,
            302,
        )

        items = list(
            OrderItem.objects.filter(
                order=order,
                variant=self.variant_a,
            )
        )

        self.assertEqual(
            len(items),
            1,
        )

        self.assertEqual(
            items[0].quantity,
            1,
            msg=(
                "Dos clics simultaneos no pueden "
                "convertir stock disponible=1 en cantidad=2."
            ),
        )

    # ========================================================
    # 3.
    # CONCURRENT A MUTATION MUST NOT LET B SLIP THROUGH
    # ========================================================

    def test_concurrent_authorized_a_and_unauthorized_b_preserve_scope(self):
        self._grant(
            self.warehouse_a
        )

        order, item_a = self._order(
            "AUTH-RACE",
            variant=self.variant_a,
            quantity=1,
            warehouses=(
                self.warehouse_a,
            ),
        )

        url = self._add_url(
            order
        )

        client_a = self._logged_client()
        client_b = self._logged_client()

        barrier = Barrier(
            2
        )

        data_a = {
            "variant": str(
                self.variant_a.pk
            ),
            "quantity": "1",
            "unit_price": "10.00",
        }

        data_b = {
            "variant": str(
                self.variant_b.pk
            ),
            "quantity": "1",
            "unit_price": "15.00",
        }

        with ThreadPoolExecutor(
            max_workers=2
        ) as pool:

            future_a = pool.submit(
                self._thread_post,
                client_a,
                url,
                data_a,
                barrier=barrier,
            )

            future_b = pool.submit(
                self._thread_post,
                client_b,
                url,
                data_b,
                barrier=barrier,
            )

            status_a = future_a.result(
                timeout=20
            )

            status_b = future_b.result(
                timeout=20
            )

        self.assertEqual(
            status_a,
            302,
            msg=(
                "La operacion valida sobre A debe continuar."
            ),
        )

        self.assertEqual(
            status_b,
            403,
            msg=(
                "La operacion prospectiva que toca B "
                "debe seguir requiriendo OPERATE B."
            ),
        )

        item_a.refresh_from_db()

        self.assertEqual(
            item_a.quantity,
            2,
        )

        self.assertFalse(
            OrderItem.objects.filter(
                order=order,
                variant=self.variant_b,
            ).exists(),
            msg=(
                "La carrera concurrente no puede introducir "
                "un SKU cuyo alcance requiere B."
            ),
        )

        self.assertEqual(
            OrderFulfillment.objects.filter(
                order=order
            ).count(),
            1,
        )

        self.assertEqual(
            OrderFulfillment.objects.get(
                order=order
            ).warehouse_id,
            self.warehouse_a.pk,
        )