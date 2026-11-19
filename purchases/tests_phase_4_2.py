from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch

from purchases.models import GoodsReceipt
from accounts.models import (
    Capability,
    EmployeeCapabilityAssignment,
    UserProfile,
    UserWarehouseAccess,
)
from companies.models import Company
from inventory.models import Warehouse
from purchases.models import PurchaseOrder, Supplier


User = get_user_model()


class PurchaseWarehouseSecurityTests(TestCase):
    """
    Fase 4.2.2

    Comprueba que Compras respete:

    usuario
    + empresa
    + capacidad
    + bodega
    + nivel
    + objeto concreto
    """

    def setUp(self):
        # =====================================================
        # EMPRESAS
        # =====================================================

        self.company_a = Company.objects.create(
            name="Empresa Compra A",
            slug="empresa-compra-a",
        )

        self.company_b = Company.objects.create(
            name="Empresa Compra B",
            slug="empresa-compra-b",
        )

        # =====================================================
        # USUARIO EMPRESA A
        # =====================================================

        self.employee = User.objects.create_user(
            "comprador-fase42@test.local",
            password="x",
        )

        UserProfile.objects.create(
            user=self.employee,
            company=self.company_a,
            role="warehouse",
        )

        # =====================================================
        # BODEGAS
        # =====================================================

        self.warehouse_a1 = Warehouse.objects.create(
            company=self.company_a,
            name="Bodega A1",
            code="F42-A1",
        )

        self.warehouse_a2 = Warehouse.objects.create(
            company=self.company_a,
            name="Bodega A2",
            code="F42-A2",
        )

        self.warehouse_b1 = Warehouse.objects.create(
            company=self.company_b,
            name="Bodega B1",
            code="F42-B1",
        )

        # El empleado SOLO puede operar A1.
        self.warehouse_access = UserWarehouseAccess.objects.create(
            company=self.company_a,
            user=self.employee,
            warehouse=self.warehouse_a1,
            access_level=UserWarehouseAccess.LEVEL_OPERATE,
        )

        # =====================================================
        # PROVEEDORES
        # =====================================================

        self.supplier_a = Supplier.objects.create(
            company=self.company_a,
            name="Proveedor Empresa A",
        )

        self.supplier_b = Supplier.objects.create(
            company=self.company_b,
            name="Proveedor Empresa B",
        )

        # =====================================================
        # ÓRDENES
        # =====================================================

        self.order_a1 = PurchaseOrder.objects.create(
            company=self.company_a,
            supplier=self.supplier_a,
            warehouse=self.warehouse_a1,
        )

        # Misma empresa, pero bodega NO autorizada.
        self.order_a2 = PurchaseOrder.objects.create(
            company=self.company_a,
            supplier=self.supplier_a,
            warehouse=self.warehouse_a2,
        )

        # Empresa completamente ajena.
        self.order_b1 = PurchaseOrder.objects.create(
            company=self.company_b,
            supplier=self.supplier_b,
            warehouse=self.warehouse_b1,
        )

        self.client.force_login(self.employee)

    # =========================================================
    # HELPERS
    # =========================================================

    def grant(self, capability_code):
        capability = Capability.objects.get(
            code=capability_code,
        )

        EmployeeCapabilityAssignment.objects.get_or_create(
            company=self.company_a,
            user=self.employee,
            capability=capability,
        )

    # =========================================================
    # 1. CAPACIDAD SIN BODEGA NO DEBE BASTAR
    # =========================================================

    def test_purchase_view_allows_authorized_warehouse(self):
        self.grant("purchases.view")

        response = self.client.get(
            reverse(
                "purchases:detalle_orden_compra",
                args=[self.order_a1.id],
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

    # =========================================================
    # 2. MISMA EMPRESA, BODEGA NO AUTORIZADA
    # =========================================================

    def test_purchase_view_denies_same_company_unauthorized_warehouse(self):
        self.grant("purchases.view")

        response = self.client.get(
            reverse(
                "purchases:detalle_orden_compra",
                args=[self.order_a2.id],
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    # =========================================================
    # 3. EMPRESA AJENA
    # =========================================================

    def test_purchase_view_hides_foreign_company_order(self):
        self.grant("purchases.view")

        response = self.client.get(
            reverse(
                "purchases:detalle_orden_compra",
                args=[self.order_b1.id],
            )
        )

        # No revelamos si el ID de otra empresa existe.
        self.assertEqual(
            response.status_code,
            404,
        )

    # =========================================================
    # 4. LISTADO SOLO DE BODEGAS AUTORIZADAS
    # =========================================================

    def test_purchase_list_only_contains_accessible_warehouses(self):
        self.grant("purchases.view")

        response = self.client.get(
            reverse("purchases:lista_compras")
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        purchase_orders = list(
            response.context["purchase_orders"]
        )

        self.assertIn(
            self.order_a1,
            purchase_orders,
        )

        self.assertNotIn(
            self.order_a2,
            purchase_orders,
        )

        self.assertNotIn(
            self.order_b1,
            purchase_orders,
        )

    # =========================================================
    # 5. NO PUEDE CREAR SIN CAPACIDAD
    # =========================================================

    def test_purchase_create_requires_specific_capability(self):
        response = self.client.get(
            reverse("purchases:crear_orden_compra")
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    # =========================================================
    # 6. POST MANIPULANDO warehouse_id
    # =========================================================

    def test_purchase_create_rejects_unauthorized_warehouse_post(self):
        self.grant("purchases.create")

        response = self.client.post(
            reverse("purchases:crear_orden_compra"),
            {
                "supplier": self.supplier_a.id,

                # Intento deliberado de operar A2.
                "warehouse": self.warehouse_a2.id,

                "tax": "0.00",
                "shipping_cost": "0.00",
                "discount": "0.00",
                "notes": "Intento de bodega ajena.",
            },
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        self.assertFalse(
            PurchaseOrder.objects.filter(
                company=self.company_a,
                warehouse=self.warehouse_a2,
                notes="Intento de bodega ajena.",
            ).exists()
        )

    # =========================================================
    # 7. POST VÁLIDO
    # =========================================================

    def test_purchase_create_accepts_authorized_warehouse(self):
        self.grant("purchases.create")

        response = self.client.post(
            reverse("purchases:crear_orden_compra"),
            {
                "supplier": self.supplier_a.id,
                "warehouse": self.warehouse_a1.id,
                "tax": "0.00",
                "shipping_cost": "0.00",
                "discount": "0.00",
                "notes": "OC autorizada fase 4.2.",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.assertTrue(
            PurchaseOrder.objects.filter(
                company=self.company_a,
                warehouse=self.warehouse_a1,
                supplier=self.supplier_a,
                notes="OC autorizada fase 4.2.",
            ).exists()
        )

    # =========================================================
    # 8. APROBAR CAPACIDAD + NIVEL DE BODEGA
    # =========================================================

    def test_purchase_approve_requires_approve_warehouse_level(self):
        self.grant("purchases.approve")

        # Actualmente tiene OPERATE, no APPROVE.
        response = self.client.post(
            reverse(
                "purchases:enviar_orden_compra",
                args=[self.order_a1.id],
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    # =========================================================
    # 9. NIVEL APPROVE SÍ ATRAVIESA AUTORIZACIÓN
    # =========================================================

    def test_purchase_approve_allows_approve_level(self):
        self.grant("purchases.approve")

        self.warehouse_access.access_level = (
            UserWarehouseAccess.LEVEL_APPROVE
        )

        self.warehouse_access.save(
            update_fields=[
                "access_level",
                "updated_at",
            ]
        )

        response = self.client.post(
            reverse(
                "purchases:enviar_orden_compra",
                args=[self.order_a1.id],
            )
        )

        # La OC no tiene productos, por lo que el servicio
        # la rechazará funcionalmente y redirigirá.
        #
        # Lo importante aquí es que autorización SÍ pasó.
        self.assertEqual(
            response.status_code,
            302,
        )

        self.order_a1.refresh_from_db()

        self.assertEqual(
            self.order_a1.status,
            PurchaseOrder.STATUS_DRAFT,
        )


# =============================================================
# FASE 4.2.3B
# SEGURIDAD DIRECTA DE SERVICIOS DE REABASTECIMIENTO
# =============================================================

from django.core.exceptions import PermissionDenied

from inventory.models import (
    Product,
    ProductVariant,
    StockCriticalAlert,
)

from purchases.models import (
    PurchaseOrderAlertLink,
    PurchaseOrderItem,
)

from purchases.services import (
    generar_orden_compra_desde_alerta_y_proveedor,
    generar_orden_compra_desde_alertas,
)


class PurchaseReorderServiceSecurityTests(TestCase):
    """
    Pruebas de defensa en profundidad.

    Estas pruebas NO usan las vistas.

    Llaman purchases.services directamente para comprobar
    que saltarse la URL/decorador no permita cruzar:

    - empresas,
    - bodegas,
    - alertas,
    - proveedores,
    - capacidades.
    """

    def setUp(self):

        # =====================================================
        # EMPRESAS
        # =====================================================

        self.company_a = Company.objects.create(
            name="Empresa Reorder A",
            slug="empresa-reorder-a",
        )

        self.company_b = Company.objects.create(
            name="Empresa Reorder B",
            slug="empresa-reorder-b",
        )

        # =====================================================
        # TRABAJADOR DE EMPRESA A
        # =====================================================

        self.employee = User.objects.create_user(
            "reorder-fase42@test.local",
            password="x",
        )

        UserProfile.objects.create(
            user=self.employee,
            company=self.company_a,
            role="warehouse",
        )

        # =====================================================
        # BODEGAS
        # =====================================================

        self.warehouse_a1 = Warehouse.objects.create(
            company=self.company_a,
            name="Reorder A1",
            code="REO-A1",
        )

        self.warehouse_a2 = Warehouse.objects.create(
            company=self.company_a,
            name="Reorder A2",
            code="REO-A2",
        )

        self.warehouse_b1 = Warehouse.objects.create(
            company=self.company_b,
            name="Reorder B1",
            code="REO-B1",
        )

        # El trabajador solamente puede operar A1.
        self.warehouse_access = (
            UserWarehouseAccess.objects.create(
                company=self.company_a,
                user=self.employee,
                warehouse=self.warehouse_a1,
                access_level=(
                    UserWarehouseAccess.LEVEL_OPERATE
                ),
            )
        )

        # =====================================================
        # CAPACIDADES
        # =====================================================

        self.grant(
            "purchases.create"
        )

        self.grant(
            "purchases.costs.view"
        )

        # =====================================================
        # PROVEEDORES
        # =====================================================

        self.supplier_a = Supplier.objects.create(
            company=self.company_a,
            name="Proveedor Reorder A",
        )

        self.supplier_b = Supplier.objects.create(
            company=self.company_b,
            name="Proveedor Reorder B",
        )

        # =====================================================
        # PRODUCTOS EMPRESA A
        # =====================================================

        self.product_a = Product.objects.create(
            company=self.company_a,
            name="Producto Reorder A",
            base_sku="REORDER-A",
            cost_price="12.50",
            sale_price="20.00",
        )

        self.variant_a = ProductVariant.objects.create(
            company=self.company_a,
            product=self.product_a,
            sku="REORDER-A-SKU",
            cost_price="12.50",
            sale_price="20.00",
            min_stock=5,
        )

        # =====================================================
        # PRODUCTOS EMPRESA B
        # =====================================================

        self.product_b = Product.objects.create(
            company=self.company_b,
            name="Producto Reorder B",
            base_sku="REORDER-B",
            cost_price="99.00",
            sale_price="150.00",
        )

        self.variant_b = ProductVariant.objects.create(
            company=self.company_b,
            product=self.product_b,
            sku="REORDER-B-SKU",
            cost_price="99.00",
            sale_price="150.00",
            min_stock=5,
        )

        # =====================================================
        # COMPRA HISTÓRICA EMPRESA A
        #
        # Esto permite que el algoritmo encuentre un proveedor
        # histórico válido para variant_a.
        # =====================================================

        self.historical_order = PurchaseOrder.objects.create(
            company=self.company_a,
            supplier=self.supplier_a,
            warehouse=self.warehouse_a1,
            created_by=self.employee,
        )

        PurchaseOrderItem.objects.create(
            purchase_order=self.historical_order,
            variant=self.variant_a,
            quantity_ordered=1,
            unit_cost="12.50",
        )

        # =====================================================
        # ALERTA AUTORIZADA A1
        # =====================================================

        self.alert_a1 = StockCriticalAlert.objects.create(
            company=self.company_a,
            warehouse=self.warehouse_a1,
            variant=self.variant_a,
            current_available=0,
            min_stock=5,
            source="phase_4_2_test",
            reference="A1",
            message="Alerta autorizada A1.",
        )

        # =====================================================
        # ALERTA MISMA EMPRESA PERO BODEGA NO AUTORIZADA A2
        # =====================================================

        self.alert_a2 = StockCriticalAlert.objects.create(
            company=self.company_a,
            warehouse=self.warehouse_a2,
            variant=self.variant_a,
            current_available=0,
            min_stock=5,
            source="phase_4_2_test",
            reference="A2",
            message="Alerta no autorizada A2.",
        )

        # =====================================================
        # ALERTA EMPRESA B
        # =====================================================

        self.alert_b1 = StockCriticalAlert.objects.create(
            company=self.company_b,
            warehouse=self.warehouse_b1,
            variant=self.variant_b,
            current_available=0,
            min_stock=5,
            source="phase_4_2_test",
            reference="B1",
            message="Alerta empresa B.",
        )

    # =========================================================
    # HELPERS
    # =========================================================

    def grant(self, capability_code):
        capability = Capability.objects.get(
            code=capability_code,
        )

        EmployeeCapabilityAssignment.objects.get_or_create(
            company=self.company_a,
            user=self.employee,
            capability=capability,
            defaults={
                "is_active": True,
            },
        )

    def revoke(self, capability_code):
        EmployeeCapabilityAssignment.objects.filter(
            company=self.company_a,
            user=self.employee,
            capability__code=capability_code,
        ).delete()

    # =========================================================
    # 1. SERVICIO DIRECTO VÁLIDO
    # =========================================================

    def test_service_creates_reorder_for_authorized_context(self):
        before = PurchaseOrder.objects.filter(
            company=self.company_a,
        ).count()

        result = generar_orden_compra_desde_alertas(
            alert_ids=[
                self.alert_a1.id,
            ],
            user=self.employee,
        )

        created_orders = result[
            "created_orders"
        ]

        self.assertEqual(
            len(created_orders),
            1,
        )

        purchase_order = created_orders[0]

        self.assertEqual(
            purchase_order.company_id,
            self.company_a.id,
        )

        self.assertEqual(
            purchase_order.warehouse_id,
            self.warehouse_a1.id,
        )

        self.assertEqual(
            purchase_order.supplier_id,
            self.supplier_a.id,
        )

        self.assertEqual(
            PurchaseOrder.objects.filter(
                company=self.company_a,
            ).count(),
            before + 1,
        )

        self.assertTrue(
            PurchaseOrderAlertLink.objects.filter(
                purchase_order=purchase_order,
                alert=self.alert_a1,
                variant=self.variant_a,
            ).exists()
        )

        self.alert_a1.refresh_from_db()

        self.assertTrue(
            self.alert_a1.is_read
        )

    # =========================================================
    # 2. ALERTA DE OTRA EMPRESA
    # =========================================================

    def test_service_rejects_foreign_company_alert(self):
        before = PurchaseOrder.objects.count()

        with self.assertRaises(
            PermissionDenied
        ):
            generar_orden_compra_desde_alertas(
                alert_ids=[
                    self.alert_b1.id,
                ],
                user=self.employee,
            )

        self.assertEqual(
            PurchaseOrder.objects.count(),
            before,
        )

    # =========================================================
    # 3. MISMA EMPRESA, BODEGA NO AUTORIZADA
    # =========================================================

    def test_service_rejects_unauthorized_warehouse_alert(self):
        before = PurchaseOrder.objects.count()

        with self.assertRaises(
            PermissionDenied
        ):
            generar_orden_compra_desde_alertas(
                alert_ids=[
                    self.alert_a2.id,
                ],
                user=self.employee,
            )

        self.assertEqual(
            PurchaseOrder.objects.count(),
            before,
        )

    # =========================================================
    # 4. ATAQUE MEZCLANDO A1 + EMPRESA B
    #
    # No debe procesar parcialmente A1.
    # Toda la operación debe abortar.
    # =========================================================

    def test_service_rejects_mixed_company_ids_atomically(self):
        before = PurchaseOrder.objects.count()

        with self.assertRaises(
            PermissionDenied
        ):
            generar_orden_compra_desde_alertas(
                alert_ids=[
                    self.alert_a1.id,
                    self.alert_b1.id,
                ],
                user=self.employee,
            )

        self.assertEqual(
            PurchaseOrder.objects.count(),
            before,
        )

        self.assertFalse(
            PurchaseOrderAlertLink.objects.filter(
                alert=self.alert_a1,
            ).exists()
        )

        self.alert_a1.refresh_from_db()

        self.assertFalse(
            self.alert_a1.is_read
        )

    # =========================================================
    # 5. SIN PERMISO DE COSTOS
    # =========================================================

    def test_service_requires_purchase_cost_permission(self):
        self.revoke(
            "purchases.costs.view"
        )

        before = PurchaseOrder.objects.count()

        with self.assertRaises(
            PermissionDenied
        ):
            generar_orden_compra_desde_alertas(
                alert_ids=[
                    self.alert_a1.id,
                ],
                user=self.employee,
            )

        self.assertEqual(
            PurchaseOrder.objects.count(),
            before,
        )

    # =========================================================
    # 6. SIN purchases.create
    # =========================================================

    def test_service_requires_purchase_create_permission(self):
        self.revoke(
            "purchases.create"
        )

        before = PurchaseOrder.objects.count()

        with self.assertRaises(
            PermissionDenied
        ):
            generar_orden_compra_desde_alertas(
                alert_ids=[
                    self.alert_a1.id,
                ],
                user=self.employee,
            )

        self.assertEqual(
            PurchaseOrder.objects.count(),
            before,
        )

    # =========================================================
    # 7. PROVEEDOR DE EMPRESA B MANIPULADO DIRECTAMENTE
    # =========================================================

    def test_manual_supplier_service_rejects_foreign_supplier(self):
        before = PurchaseOrder.objects.count()

        with self.assertRaises(
            PermissionDenied
        ):
            generar_orden_compra_desde_alerta_y_proveedor(
                alert_id=self.alert_a1.id,
                supplier_id=self.supplier_b.id,
                user=self.employee,
            )

        self.assertEqual(
            PurchaseOrder.objects.count(),
            before,
        )

    # =========================================================
    # 8. PROVEEDOR MANUAL VÁLIDO
    # =========================================================

    def test_manual_supplier_service_accepts_authorized_supplier(self):
        before = PurchaseOrder.objects.filter(
            company=self.company_a,
        ).count()

        result = (
            generar_orden_compra_desde_alerta_y_proveedor(
                alert_id=self.alert_a1.id,
                supplier_id=self.supplier_a.id,
                user=self.employee,
            )
        )

        purchase_order = result[
            "purchase_order"
        ]

        self.assertTrue(
            result["created"]
        )

        self.assertFalse(
            result["already_linked"]
        )

        self.assertEqual(
            purchase_order.company_id,
            self.company_a.id,
        )

        self.assertEqual(
            purchase_order.warehouse_id,
            self.warehouse_a1.id,
        )

        self.assertEqual(
            purchase_order.supplier_id,
            self.supplier_a.id,
        )

        self.assertEqual(
            PurchaseOrder.objects.filter(
                company=self.company_a,
            ).count(),
            before + 1,
        )

        self.assertTrue(
            PurchaseOrderAlertLink.objects.filter(
                purchase_order=purchase_order,
                alert=self.alert_a1,
            ).exists()
        )


# =============================================================
# FASE 4.2.3C
# PRUEBAS DE RENDERIZADO DE COSTOS Y PAGOS
# =============================================================

class PurchaseFinancialVisibilityTests(TestCase):

    def setUp(self):
        self.company = Company.objects.create(
            name="Empresa Costos F42",
            slug="empresa-costos-f42",
        )

        self.employee = User.objects.create_user(
            "costos-f42@test.local",
            password="x",
        )

        UserProfile.objects.create(
            user=self.employee,
            company=self.company,
            role="warehouse",
        )

        self.warehouse = Warehouse.objects.create(
            company=self.company,
            name="Bodega Costos F42",
            code="COST-F42",
        )

        UserWarehouseAccess.objects.create(
            company=self.company,
            user=self.employee,
            warehouse=self.warehouse,
            access_level=UserWarehouseAccess.LEVEL_OPERATE,
        )

        self.supplier = Supplier.objects.create(
            company=self.company,
            name="Proveedor Costos F42",
        )

        self.product = Product.objects.create(
            company=self.company,
            name="Producto Costos F42",
            base_sku="COST-F42",
            cost_price="12.50",
            sale_price="20.00",
        )

        self.variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="COST-F42-SKU",
            cost_price="12.50",
            sale_price="20.00",
        )

        self.order = PurchaseOrder.objects.create(
            company=self.company,
            supplier=self.supplier,
            warehouse=self.warehouse,
            created_by=self.employee,
        )

        PurchaseOrderItem.objects.create(
            purchase_order=self.order,
            variant=self.variant,
            quantity_ordered=2,
            quantity_received=0,
            unit_cost="12.50",
        )

        self.order.calculate_totals(
            save=True,
        )

        self.grant(
            "purchases.view"
        )

        self.client.force_login(
            self.employee
        )

    def grant(self, capability_code):
        capability = Capability.objects.get(
            code=capability_code,
        )

        EmployeeCapabilityAssignment.objects.get_or_create(
            company=self.company,
            user=self.employee,
            capability=capability,
            defaults={
                "is_active": True,
            },
        )

    # =========================================================
    # SIN COSTS.VIEW
    # =========================================================

    def test_detail_hides_purchase_costs_without_permission(self):
        response = self.client.get(
            reverse(
                "purchases:detalle_orden_compra",
                args=[self.order.id],
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )
        self.assertContains(
            response,
            "Restringida",
        )

        self.assertNotContains(
            response,
            "$12.50",
        )

        self.assertNotContains(
            response,
            'name="unit_cost"',
        )

    # =========================================================
    # CON COSTS.VIEW
    # =========================================================

    def test_detail_shows_purchase_costs_with_view_permission(self):
        self.grant(
            "purchases.costs.view"
        )

        response = self.client.get(
            reverse(
                "purchases:detalle_orden_compra",
                args=[self.order.id],
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertContains(
            response,
            "$12.50",
        )

        self.assertContains(
            response,
            "$25.00",
        )

        # Puede VER, pero todavía no editar.
        self.assertNotContains(
            response,
            'name="unit_cost"',
        )

    # =========================================================
    # CON COSTS.EDIT
    # =========================================================

    def test_detail_shows_cost_editor_with_edit_permission(self):
        self.grant(
            "purchases.costs.view"
        )

        self.grant(
            "purchases.costs.edit"
        )

        response = self.client.get(
            reverse(
                "purchases:detalle_orden_compra",
                args=[self.order.id],
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertContains(
            response,
            'name="unit_cost"',
        )

        self.assertContains(
            response,
            "Costo unitario",
        )

    # =========================================================
    # SIN PAGOS.VIEW
    # =========================================================

    def test_detail_hides_supplier_payments_without_permission(self):
        response = self.client.get(
            reverse(
                "purchases:detalle_orden_compra",
                args=[self.order.id],
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertNotContains(
            response,
            "Pagos a proveedor",
        )

        self.assertNotContains(
            response,
            "Registrar pago a proveedor",
        )

    # =========================================================
    # CON PAGOS.VIEW
    # =========================================================
    def test_detail_shows_supplier_payment_section_with_permission(self):
        self.grant(
            "supplier_payments.view"
            )
        response = self.client.get(
            reverse(
                "purchases:detalle_orden_compra",
                args=[self.order.id],
                )
            )
        self.assertEqual(
            response.status_code,
            200,
            )
        self.assertContains(
            response,
            "Pagos a proveedor",
            )

        self.assertContains(
            response,
            "Historial de pagos",
            )
        self.assertContains(
            response,
            "No tienes",
            )
        self.assertContains(
            response,
            "pagos a proveedores",
            )
        registrar_pago_url = reverse(
            "purchases:registrar_pago_proveedor",
            args=[self.order.id],
            )
        self.assertNotContains(
            response,
            registrar_pago_url,
            )

    # =========================================================
    # LISTA DE COMPRAS SIN COSTOS
    # =========================================================

    def test_purchase_list_hides_total_without_cost_permission(self):
        response = self.client.get(
            reverse(
                "purchases:lista_compras"
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertContains(
            response,
            "Restringido",
        )

        self.assertNotContains(
            response,
            "$25.00",
        )

    # =========================================================
    # LISTA DE COMPRAS CON COSTOS
    # =========================================================

    def test_purchase_list_shows_total_with_cost_permission(self):
        self.grant(
            "purchases.costs.view"
        )

        response = self.client.get(
            reverse(
                "purchases:lista_compras"
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertContains(
            response,
            "$25.00",
        )


# =============================================================
# FASE 4.2.4
# RECEPCIONES - STOCK / DOBLE CONFIRMACION / SOBRE-RECEPCION
# =============================================================
class GoodsReceiptCoreSecurityTests(TestCase):

    def setUp(self):
        from decimal import Decimal
        from unittest.mock import patch

        self.Decimal = Decimal

        # =========================================================
        # AISLAR CONTABILIDAD EN ESTAS PRUEBAS
        # =========================================================
        #
        # confirm_goods_receipt() genera también el asiento contable.
        # Estas pruebas están enfocadas al motor de recepciones,
        # stock, cantidades, cancelación e idempotencia.
        #

        self.accounting_patcher = patch(
            "accounting.services.inventory_integration." "post_goods_receipt_entry",
            return_value=None,
        )

        self.mock_post_goods_receipt_entry = self.accounting_patcher.start()

        self.addCleanup(self.accounting_patcher.stop)

        # =========================================================
        # EMPRESA
        # =========================================================

        self.company = Company.objects.create(
            name="Empresa Recepciones F42",
            slug="empresa-recepciones-f42",
        )

        # =========================================================
        # BODEGA
        # =========================================================

        self.warehouse = Warehouse.objects.create(
            company=self.company,
            name="Bodega Recepciones F42",
            code="REC-F42",
        )

        # =========================================================
        # PROVEEDOR
        # =========================================================

        self.supplier = Supplier.objects.create(
            company=self.company,
            name="Proveedor Recepciones F42",
        )

        # =========================================================
        # PRODUCTO
        # =========================================================

        self.product = Product.objects.create(
            company=self.company,
            name="Producto Recepciones F42",
            base_sku="REC-F42",
            cost_price=Decimal("10.00"),
            sale_price=Decimal("20.00"),
        )

        # =========================================================
        # VARIANTE
        # =========================================================

        self.variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="REC-F42-SKU",
            cost_price=Decimal("10.00"),
            sale_price=Decimal("20.00"),
        )

        # =========================================================
        # ORDEN DE COMPRA
        # =========================================================

        self.order = PurchaseOrder.objects.create(
            company=self.company,
            supplier=self.supplier,
            warehouse=self.warehouse,
            status=PurchaseOrder.STATUS_SENT,
        )

        # =========================================================
        # PARTIDA DE LA ORDEN
        # =========================================================

        self.order_item = PurchaseOrderItem.objects.create(
            purchase_order=self.order,
            variant=self.variant,
            quantity_ordered=10,
            quantity_received=0,
            unit_cost=Decimal("10.00"),
        )

    def create_receipt(self):
        from purchases.services import (
            create_goods_receipt_from_purchase_order,
        )

        return create_goods_receipt_from_purchase_order(
            purchase_order=self.order,
            user=None,
        )

    # =========================================================
    # CREACION
    # =========================================================

    def test_create_receipt_uses_real_pending_quantity(self):
        receipt = self.create_receipt()

        self.assertEqual(
            receipt.status,
            GoodsReceipt.STATUS_DRAFT,
        )

        item = receipt.items.get()

        self.assertEqual(
            item.purchase_order_item_id,
            self.order_item.id,
        )

        self.assertEqual(
            item.variant_id,
            self.variant.id,
        )

        self.assertEqual(
            item.quantity_received,
            10,
        )

        self.assertFalse(
            item.added_to_stock,
        )

    # =========================================================
    # EDICION DE BORRADOR
    # =========================================================

    def test_draft_receipt_cannot_exceed_real_pending_quantity(self):
        from django.core.exceptions import ValidationError
        from purchases.services import (
            add_item_to_goods_receipt,
        )

        receipt = self.create_receipt()

        with self.assertRaises(ValidationError):
            add_item_to_goods_receipt(
                receipt=receipt,
                purchase_order_item=self.order_item,
                quantity_received=11,
            )

        item = receipt.items.get()

        self.assertEqual(
            item.quantity_received,
            10,
        )

    # =========================================================
    # CONFIRMACION NORMAL
    # =========================================================

    def test_confirm_receipt_adds_stock_once(self):
        from inventory.models import Stock
        from purchases.services import (
            add_item_to_goods_receipt,
            confirm_goods_receipt,
        )

        receipt = self.create_receipt()

        add_item_to_goods_receipt(
            receipt=receipt,
            purchase_order_item=self.order_item,
            quantity_received=6,
        )

        confirm_goods_receipt(
            receipt=receipt,
            user=None,
        )

        receipt.refresh_from_db()
        self.order_item.refresh_from_db()

        stock = Stock.objects.get(
            company=self.company,
            warehouse=self.warehouse,
            variant=self.variant,
        )

        self.assertEqual(
            receipt.status,
            GoodsReceipt.STATUS_RECEIVED,
        )

        self.assertEqual(
            self.order_item.quantity_received,
            6,
        )

        self.assertEqual(
            self.Decimal(str(stock.physical_stock)),
            self.Decimal("6"),
        )

        receipt_item = receipt.items.get()

        self.assertTrue(
            receipt_item.added_to_stock,
        )

    # =========================================================
    # DOBLE CONFIRMACION
    # =========================================================

    def test_double_confirmation_does_not_duplicate_stock(self):
        from django.core.exceptions import ValidationError
        from inventory.models import Stock
        from purchases.services import (
            add_item_to_goods_receipt,
            confirm_goods_receipt,
        )

        receipt = self.create_receipt()

        add_item_to_goods_receipt(
            receipt=receipt,
            purchase_order_item=self.order_item,
            quantity_received=4,
        )

        confirm_goods_receipt(
            receipt=receipt,
            user=None,
        )

        stock = Stock.objects.get(
            company=self.company,
            warehouse=self.warehouse,
            variant=self.variant,
        )

        first_stock = self.Decimal(str(stock.physical_stock))

        with self.assertRaises(ValidationError):
            confirm_goods_receipt(
                receipt=receipt,
                user=None,
            )

        stock.refresh_from_db()
        self.order_item.refresh_from_db()

        self.assertEqual(
            first_stock,
            self.Decimal("4"),
        )

        self.assertEqual(
            self.Decimal(str(stock.physical_stock)),
            self.Decimal("4"),
        )

        self.assertEqual(
            self.order_item.quantity_received,
            4,
        )

    # =========================================================
    # DOS BORRADORES PARA LA MISMA OC
    # =========================================================

    def test_second_receipt_cannot_over_receive_after_first_confirmation(self):
        from django.core.exceptions import ValidationError
        from inventory.models import Stock
        from purchases.services import (
            add_item_to_goods_receipt,
            confirm_goods_receipt,
        )

        receipt_one = self.create_receipt()
        receipt_two = self.create_receipt()

        add_item_to_goods_receipt(
            receipt=receipt_one,
            purchase_order_item=self.order_item,
            quantity_received=6,
        )

        add_item_to_goods_receipt(
            receipt=receipt_two,
            purchase_order_item=self.order_item,
            quantity_received=5,
        )

        confirm_goods_receipt(
            receipt=receipt_one,
            user=None,
        )

        with self.assertRaises(ValidationError):
            confirm_goods_receipt(
                receipt=receipt_two,
                user=None,
            )

        self.order_item.refresh_from_db()

        stock = Stock.objects.get(
            company=self.company,
            warehouse=self.warehouse,
            variant=self.variant,
        )

        self.assertEqual(
            self.order_item.quantity_received,
            6,
        )

        self.assertEqual(
            self.Decimal(str(stock.physical_stock)),
            self.Decimal("6"),
        )

        receipt_two.refresh_from_db()

        self.assertEqual(
            receipt_two.status,
            GoodsReceipt.STATUS_DRAFT,
        )

    # =========================================================
    # CANCELACION
    # =========================================================

    def test_cancel_draft_receipt_does_not_add_stock(self):
        from inventory.models import Stock
        from purchases.services import (
            cancel_goods_receipt,
        )

        receipt = self.create_receipt()

        cancel_goods_receipt(
            receipt=receipt,
            user=None,
            reason="Prueba cancelación F42",
        )

        receipt.refresh_from_db()
        self.order_item.refresh_from_db()

        self.assertEqual(
            receipt.status,
            GoodsReceipt.STATUS_CANCELLED,
        )

        self.assertEqual(
            self.order_item.quantity_received,
            0,
        )

        self.assertFalse(
            Stock.objects.filter(
                company=self.company,
                warehouse=self.warehouse,
                variant=self.variant,
            ).exists()
        )

    # =========================================================
    # RECEPCION CONFIRMADA NO SE CANCELA
    # =========================================================

    def test_confirmed_receipt_cannot_be_cancelled(self):
        from django.core.exceptions import ValidationError
        from purchases.services import (
            add_item_to_goods_receipt,
            cancel_goods_receipt,
            confirm_goods_receipt,
        )

        receipt = self.create_receipt()

        add_item_to_goods_receipt(
            receipt=receipt,
            purchase_order_item=self.order_item,
            quantity_received=3,
        )

        confirm_goods_receipt(
            receipt=receipt,
            user=None,
        )

        with self.assertRaises(ValidationError):
            cancel_goods_receipt(
                receipt=receipt,
                user=None,
                reason="Intento inválido",
            )

        receipt.refresh_from_db()

        self.assertEqual(
            receipt.status,
            GoodsReceipt.STATUS_RECEIVED,
        )


# =============================================================
# FASE 4.2.4
# RECEPCIONES - SEGURIDAD HTTP / COMPANY / BODEGA / CAPABILITIES
# =============================================================


class GoodsReceiptHttpSecurityTests(TestCase):

    def setUp(self):
        from decimal import Decimal
        from django.contrib.auth import get_user_model

        from accounts.models import (
            Capability,
            EmployeeCapabilityAssignment,
            UserProfile,
            UserWarehouseAccess,
        )
        from companies.models import Company
        from inventory.models import Product, ProductVariant, Warehouse
        from purchases.models import (
            GoodsReceipt,
            GoodsReceiptItem,
            PurchaseOrder,
            PurchaseOrderItem,
            Supplier,
        )

        User = get_user_model()

        self.Capability = Capability
        self.EmployeeCapabilityAssignment = EmployeeCapabilityAssignment
        self.UserWarehouseAccess = UserWarehouseAccess

        # =====================================================
        # EMPRESA PRINCIPAL
        # =====================================================

        self.company = Company.objects.create(
            name="Empresa HTTP Recepciones F42",
            slug="empresa-http-recepciones-f42",
        )

        self.user = User.objects.create_user(
            "receiving-http-f42@test.local",
            password="x",
        )

        UserProfile.objects.create(
            user=self.user,
            company=self.company,
            role="warehouse",
        )

        self.warehouse = Warehouse.objects.create(
            company=self.company,
            name="Bodega HTTP Autorizada",
            code="HTTP-REC-01",
        )

        self.other_warehouse = Warehouse.objects.create(
            company=self.company,
            name="Bodega HTTP No Autorizada",
            code="HTTP-REC-02",
        )

        self.access = UserWarehouseAccess.objects.create(
            company=self.company,
            user=self.user,
            warehouse=self.warehouse,
            access_level=UserWarehouseAccess.LEVEL_OPERATE,
        )

        self.supplier = Supplier.objects.create(
            company=self.company,
            name="Proveedor HTTP F42",
        )

        self.product = Product.objects.create(
            company=self.company,
            name="Producto HTTP F42",
            base_sku="HTTP-REC-F42",
            cost_price=Decimal("10.00"),
            sale_price=Decimal("20.00"),
        )

        self.variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="HTTP-REC-F42-SKU",
            cost_price=Decimal("10.00"),
            sale_price=Decimal("20.00"),
        )

        self.order = PurchaseOrder.objects.create(
            company=self.company,
            supplier=self.supplier,
            warehouse=self.warehouse,
            status=PurchaseOrder.STATUS_SENT,
        )

        self.order_item = PurchaseOrderItem.objects.create(
            purchase_order=self.order,
            variant=self.variant,
            quantity_ordered=10,
            quantity_received=0,
            unit_cost=Decimal("10.00"),
        )

        self.receipt = GoodsReceipt.objects.create(
            company=self.company,
            purchase_order=self.order,
            warehouse=self.warehouse,
        )

        self.receipt_item = GoodsReceiptItem.objects.create(
            receipt=self.receipt,
            purchase_order_item=self.order_item,
            variant=self.variant,
            quantity_received=5,
            unit_cost=Decimal("10.00"),
        )

        # =====================================================
        # MISMA EMPRESA, OTRA BODEGA
        # =====================================================

        self.other_order = PurchaseOrder.objects.create(
            company=self.company,
            supplier=self.supplier,
            warehouse=self.other_warehouse,
            status=PurchaseOrder.STATUS_SENT,
        )

        self.other_receipt = GoodsReceipt.objects.create(
            company=self.company,
            purchase_order=self.other_order,
            warehouse=self.other_warehouse,
        )

        # =====================================================
        # EMPRESA AJENA
        # =====================================================

        self.foreign_company = Company.objects.create(
            name="Empresa Ajena HTTP F42",
            slug="empresa-ajena-http-f42",
        )

        self.foreign_warehouse = Warehouse.objects.create(
            company=self.foreign_company,
            name="Bodega Ajena HTTP",
            code="HTTP-FOREIGN",
        )

        self.foreign_supplier = Supplier.objects.create(
            company=self.foreign_company,
            name="Proveedor Ajeno HTTP",
        )

        self.foreign_order = PurchaseOrder.objects.create(
            company=self.foreign_company,
            supplier=self.foreign_supplier,
            warehouse=self.foreign_warehouse,
            status=PurchaseOrder.STATUS_SENT,
        )

        self.foreign_receipt = GoodsReceipt.objects.create(
            company=self.foreign_company,
            purchase_order=self.foreign_order,
            warehouse=self.foreign_warehouse,
        )

        self.client.force_login(self.user)

    def grant(self, capability_code):
        capability = self.Capability.objects.get(
            code=capability_code,
        )

        self.EmployeeCapabilityAssignment.objects.get_or_create(
            company=self.company,
            user=self.user,
            capability=capability,
            defaults={
                "is_active": True,
            },
        )

    def set_access_level(self, level):
        self.UserWarehouseAccess.objects.filter(
            pk=self.access.pk,
        ).update(
            access_level=level,
        )

        self.access.refresh_from_db()

    # =========================================================
    # VIEW AUTORIZADO
    # =========================================================

    def test_receipt_detail_allowed_with_view_permission(self):
        from django.urls import reverse

        self.grant("receiving.view")

        response = self.client.get(
            reverse(
                "purchases:detalle_recepcion",
                args=[self.receipt.id],
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

    # =========================================================
    # MISMA EMPRESA PERO BODEGA NO AUTORIZADA
    # =========================================================

    def test_receipt_same_company_unauthorized_warehouse_returns_403(self):
        from django.urls import reverse

        self.grant("receiving.view")

        response = self.client.get(
            reverse(
                "purchases:detalle_recepcion",
                args=[self.other_receipt.id],
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    # =========================================================
    # EMPRESA AJENA
    # =========================================================

    def test_foreign_company_receipt_returns_404(self):
        from django.urls import reverse

        self.grant("receiving.view")

        response = self.client.get(
            reverse(
                "purchases:detalle_recepcion",
                args=[self.foreign_receipt.id],
            )
        )

        self.assertEqual(
            response.status_code,
            404,
        )

    # =========================================================
    # CREAR SIN RECEIVING.EXECUTE
    # =========================================================

    def test_create_receipt_without_execute_permission_returns_403(self):
        from django.urls import reverse
        from purchases.models import GoodsReceipt
        before_count = GoodsReceipt.objects.filter(
            purchase_order=self.order,
        ).count()

        response = self.client.post(
            reverse(
                "purchases:crear_recepcion",
                args=[self.order.id],
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        after_count = GoodsReceipt.objects.filter(
            purchase_order=self.order,
        ).count()

        self.assertEqual(
            before_count,
            after_count,
        )

    # =========================================================
    # EDITAR ITEM SIN RECEIVING.EXECUTE
    # =========================================================

    def test_update_receipt_item_without_execute_permission_returns_403(self):
        from django.urls import reverse

        original_quantity = self.receipt_item.quantity_received

        response = self.client.post(
            reverse(
                "purchases:actualizar_item_recepcion",
                args=[
                    self.receipt.id,
                    self.receipt_item.id,
                ],
            ),
            {
                "quantity_received": 3,
                "notes": "Intento sin permiso",
            },
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        self.receipt_item.refresh_from_db()

        self.assertEqual(
            self.receipt_item.quantity_received,
            original_quantity,
        )

    # =========================================================
    # CONFIRM CAPABILITY PERO SOLO OPERATE
    # =========================================================

    def test_confirm_receipt_with_operate_level_returns_403(self):
        from django.urls import reverse

        self.grant("receiving.confirm")

        response = self.client.post(
            reverse(
                "purchases:confirmar_recepcion",
                args=[self.receipt.id],
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        self.receipt.refresh_from_db()

        self.assertEqual(
            self.receipt.status,
            GoodsReceipt.STATUS_DRAFT,
        )

    # =========================================================
    # CANCELAR SIN CAPABILITY
    # =========================================================

    def test_cancel_receipt_without_cancel_permission_returns_403(self):
        from django.urls import reverse

        self.set_access_level(self.UserWarehouseAccess.LEVEL_MANAGE)

        response = self.client.post(
            reverse(
                "purchases:cancelar_recepcion",
                args=[self.receipt.id],
            ),
            {
                "reason": "Intento sin capability",
            },
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        self.receipt.refresh_from_db()

        self.assertEqual(
            self.receipt.status,
            GoodsReceipt.STATUS_DRAFT,
        )


# =============================================================
# FASE 4.2.5
# PAGOS A PROVEEDOR - MOTOR FINANCIERO
# =============================================================


class SupplierPaymentCoreSecurityTests(TestCase):

    def setUp(self):
        from decimal import Decimal

        from companies.models import Company
        from inventory.models import (
            Product,
            ProductVariant,
            Warehouse,
        )
        from purchases.models import (
            PurchaseOrder,
            PurchaseOrderItem,
            Supplier,
        )

        self.Decimal = Decimal

        # =====================================================
        # EMPRESA
        # =====================================================

        self.company = Company.objects.create(
            name="Empresa Pagos Proveedor F42",
            slug="empresa-pagos-proveedor-f42",
        )

        # =====================================================
        # BODEGA
        # =====================================================

        self.warehouse = Warehouse.objects.create(
            company=self.company,
            name="Bodega Pagos Proveedor F42",
            code="PP-F42",
        )

        # =====================================================
        # PROVEEDOR
        # =====================================================

        self.supplier = Supplier.objects.create(
            company=self.company,
            name="Proveedor Pagos F42",
        )

        # =====================================================
        # PRODUCTO
        # =====================================================

        self.product = Product.objects.create(
            company=self.company,
            name="Producto Pagos F42",
            base_sku="PP-F42",
            cost_price=Decimal("10.00"),
            sale_price=Decimal("20.00"),
        )

        self.variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="PP-F42-SKU",
            cost_price=Decimal("10.00"),
            sale_price=Decimal("20.00"),
        )

        # =====================================================
        # ORDEN DE COMPRA = $100
        # =====================================================

        self.order = PurchaseOrder.objects.create(
            company=self.company,
            supplier=self.supplier,
            warehouse=self.warehouse,
            status=PurchaseOrder.STATUS_SENT,
        )

        self.order_item = PurchaseOrderItem.objects.create(
            purchase_order=self.order,
            variant=self.variant,
            quantity_ordered=10,
            quantity_received=0,
            unit_cost=Decimal("10.00"),
        )

    # =========================================================
    # REGISTRO NORMAL
    # =========================================================

    def test_register_supplier_payment_stays_pending(self):
        from purchases.models import (
            PurchaseOrder,
            SupplierPayment,
        )
        from purchases.services import (
            register_supplier_payment,
        )

        payment = register_supplier_payment(
            purchase_order=self.order,
            amount=self.Decimal("40.00"),
            user=None,
        )

        payment.refresh_from_db()
        self.order.refresh_from_db()

        self.assertEqual(
            payment.status,
            SupplierPayment.STATUS_PENDING,
        )

        self.assertEqual(
            payment.amount,
            self.Decimal("40.00"),
        )

        self.assertEqual(
            self.order.payment_status,
            PurchaseOrder.PAYMENT_PENDING,
        )

    # =========================================================
    # PENDIENTES RESERVAN CAPACIDAD DE REGISTRO
    # =========================================================

    def test_pending_payments_cannot_exceed_purchase_order_total(self):
        from django.core.exceptions import ValidationError

        from purchases.models import SupplierPayment
        from purchases.services import (
            get_supplier_payment_totals,
            register_supplier_payment,
        )

        first_payment = register_supplier_payment(
            purchase_order=self.order,
            amount=self.Decimal("80.00"),
            user=None,
        )

        self.assertEqual(
            first_payment.status,
            SupplierPayment.STATUS_PENDING,
        )

        totals = get_supplier_payment_totals(self.order)

        self.assertEqual(
            totals["pending_total"],
            self.Decimal("80.00"),
        )

        self.assertEqual(
            totals["balance_due"],
            self.Decimal("100.00"),
        )

        self.assertEqual(
            totals["available_to_register"],
            self.Decimal("20.00"),
        )

        with self.assertRaises(ValidationError):
            register_supplier_payment(
                purchase_order=self.order,
                amount=self.Decimal("21.00"),
                user=None,
            )

        self.assertEqual(
            SupplierPayment.objects.filter(
                purchase_order=self.order,
            ).count(),
            1,
        )

    # =========================================================
    # APROBACION PARCIAL
    # =========================================================

    def test_approve_supplier_payment_marks_order_partial(self):
        from purchases.models import (
            PurchaseOrder,
            SupplierPayment,
        )
        from purchases.services import (
            approve_supplier_payment,
            get_supplier_payment_totals,
            register_supplier_payment,
        )

        payment = register_supplier_payment(
            purchase_order=self.order,
            amount=self.Decimal("40.00"),
            user=None,
        )

        approve_supplier_payment(
            payment=payment,
            user=None,
        )

        payment.refresh_from_db()
        self.order.refresh_from_db()

        totals = get_supplier_payment_totals(self.order)

        self.assertEqual(
            payment.status,
            SupplierPayment.STATUS_APPROVED,
        )

        self.assertEqual(
            self.order.payment_status,
            PurchaseOrder.PAYMENT_PARTIAL,
        )

        self.assertEqual(
            totals["approved_total"],
            self.Decimal("40.00"),
        )

        self.assertEqual(
            totals["balance_due"],
            self.Decimal("60.00"),
        )

        self.assertEqual(
            totals["available_to_register"],
            self.Decimal("60.00"),
        )

    # =========================================================
    # DOBLE APROBACION
    # =========================================================

    def test_double_approval_is_blocked(self):
        from django.core.exceptions import ValidationError

        from purchases.models import SupplierPayment
        from purchases.services import (
            approve_supplier_payment,
            get_supplier_payment_totals,
            register_supplier_payment,
        )

        payment = register_supplier_payment(
            purchase_order=self.order,
            amount=self.Decimal("50.00"),
            user=None,
        )

        approve_supplier_payment(
            payment=payment,
            user=None,
        )

        with self.assertRaises(ValidationError):
            approve_supplier_payment(
                payment=payment,
                user=None,
            )

        payment.refresh_from_db()

        totals = get_supplier_payment_totals(self.order)

        self.assertEqual(
            payment.status,
            SupplierPayment.STATUS_APPROVED,
        )

        self.assertEqual(
            totals["approved_total"],
            self.Decimal("50.00"),
        )

    # =========================================================
    # APROBACION TAMBIEN DEFIENDE SOBRE-PAGO
    # =========================================================

    def test_approval_blocks_payment_above_current_balance(self):
        from django.core.exceptions import ValidationError

        from purchases.models import SupplierPayment
        from purchases.services import (
            approve_supplier_payment,
        )

        # Creamos directamente dos pagos pendientes para simular
        # datos antiguos o una condición previa al endurecimiento.
        first_payment = SupplierPayment.objects.create(
            company=self.company,
            purchase_order=self.order,
            supplier=self.supplier,
            amount=self.Decimal("80.00"),
            status=SupplierPayment.STATUS_PENDING,
        )

        second_payment = SupplierPayment.objects.create(
            company=self.company,
            purchase_order=self.order,
            supplier=self.supplier,
            amount=self.Decimal("30.00"),
            status=SupplierPayment.STATUS_PENDING,
        )

        approve_supplier_payment(
            payment=first_payment,
            user=None,
        )

        with self.assertRaises(ValidationError):
            approve_supplier_payment(
                payment=second_payment,
                user=None,
            )

        first_payment.refresh_from_db()
        second_payment.refresh_from_db()

        self.assertEqual(
            first_payment.status,
            SupplierPayment.STATUS_APPROVED,
        )

        self.assertEqual(
            second_payment.status,
            SupplierPayment.STATUS_PENDING,
        )

    # =========================================================
    # RECHAZO LIBERA CAPACIDAD
    # =========================================================

    def test_rejected_payment_no_longer_reserves_balance(self):
        from purchases.models import SupplierPayment
        from purchases.services import (
            get_supplier_payment_totals,
            register_supplier_payment,
            reject_supplier_payment,
        )

        payment = register_supplier_payment(
            purchase_order=self.order,
            amount=self.Decimal("70.00"),
            user=None,
        )

        reject_supplier_payment(
            payment=payment,
            user=None,
            reason="Comprobante inválido",
        )

        payment.refresh_from_db()

        totals = get_supplier_payment_totals(self.order)

        self.assertEqual(
            payment.status,
            SupplierPayment.STATUS_REJECTED,
        )

        self.assertEqual(
            totals["approved_total"],
            self.Decimal("0.00"),
        )

        self.assertEqual(
            totals["pending_total"],
            self.Decimal("0.00"),
        )

        self.assertEqual(
            totals["balance_due"],
            self.Decimal("100.00"),
        )

        self.assertEqual(
            totals["available_to_register"],
            self.Decimal("100.00"),
        )

    # =========================================================
    # CANCELAR PAGO APROBADO RECALCULA OC
    # =========================================================

    def test_cancel_approved_payment_reopens_balance(self):
        from purchases.models import (
            PurchaseOrder,
            SupplierPayment,
        )
        from purchases.services import (
            approve_supplier_payment,
            cancel_supplier_payment,
            get_supplier_payment_totals,
            register_supplier_payment,
        )

        payment = register_supplier_payment(
            purchase_order=self.order,
            amount=self.Decimal("100.00"),
            user=None,
        )

        approve_supplier_payment(
            payment=payment,
            user=None,
        )

        self.order.refresh_from_db()

        self.assertEqual(
            self.order.payment_status,
            PurchaseOrder.PAYMENT_PAID,
        )

        cancel_supplier_payment(
            payment=payment,
            user=None,
            reason="Pago revertido",
        )

        payment.refresh_from_db()
        self.order.refresh_from_db()

        totals = get_supplier_payment_totals(self.order)

        self.assertEqual(
            payment.status,
            SupplierPayment.STATUS_CANCELLED,
        )

        self.assertEqual(
            self.order.payment_status,
            PurchaseOrder.PAYMENT_PENDING,
        )

        self.assertEqual(
            totals["approved_total"],
            self.Decimal("0.00"),
        )

        self.assertEqual(
            totals["balance_due"],
            self.Decimal("100.00"),
        )

        self.assertEqual(
            totals["available_to_register"],
            self.Decimal("100.00"),
        )

    # =========================================================
    # ORDEN CANCELADA NO ACEPTA PAGOS
    # =========================================================

    def test_cancelled_purchase_order_cannot_receive_payment(self):
        from django.core.exceptions import ValidationError

        from purchases.models import (
            PurchaseOrder,
            SupplierPayment,
        )
        from purchases.services import (
            register_supplier_payment,
        )

        self.order.status = PurchaseOrder.STATUS_CANCELLED

        self.order.save(
            update_fields=[
                "status",
                "updated_at",
            ]
        )

        with self.assertRaises(ValidationError):
            register_supplier_payment(
                purchase_order=self.order,
                amount=self.Decimal("10.00"),
                user=None,
            )

        self.assertFalse(
            SupplierPayment.objects.filter(
                purchase_order=self.order,
            ).exists()
        )

# =============================================================
# FASE 4.2.5
# PAGOS A PROVEEDOR - SEGURIDAD HTTP
# =============================================================

class SupplierPaymentHttpSecurityTests(TestCase):

    def setUp(self):
        from decimal import Decimal
        from django.contrib.auth import get_user_model

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
            Warehouse,
        )
        from purchases.models import (
            PurchaseOrder,
            PurchaseOrderItem,
            Supplier,
            SupplierPayment,
        )

        User = get_user_model()

        self.Decimal = Decimal
        self.Capability = Capability
        self.EmployeeCapabilityAssignment = EmployeeCapabilityAssignment
        self.UserWarehouseAccess = UserWarehouseAccess
        self.SupplierPayment = SupplierPayment

        # =====================================================
        # EMPRESA PRINCIPAL
        # =====================================================

        self.company = Company.objects.create(
            name="Empresa HTTP Pagos F42",
            slug="empresa-http-pagos-f42",
        )

        self.user = User.objects.create_user(
            "supplier-payment-http@test.local",
            password="x",
        )

        UserProfile.objects.create(
            user=self.user,
            company=self.company,
            role="warehouse",
        )

        # =====================================================
        # BODEGA AUTORIZADA
        # =====================================================

        self.warehouse = Warehouse.objects.create(
            company=self.company,
            name="Bodega HTTP Pagos",
            code="HTTP-PP-01",
        )

        self.access = UserWarehouseAccess.objects.create(
            company=self.company,
            user=self.user,
            warehouse=self.warehouse,
            access_level=UserWarehouseAccess.LEVEL_OPERATE,
        )

        # =====================================================
        # BODEGA NO AUTORIZADA
        # =====================================================

        self.other_warehouse = Warehouse.objects.create(
            company=self.company,
            name="Bodega HTTP Pagos No Autorizada",
            code="HTTP-PP-02",
        )

        # =====================================================
        # DATOS PRINCIPALES
        # =====================================================

        self.supplier = Supplier.objects.create(
            company=self.company,
            name="Proveedor HTTP Pagos",
        )

        self.product = Product.objects.create(
            company=self.company,
            name="Producto HTTP Pagos",
            base_sku="HTTP-PP",
            cost_price=Decimal("10.00"),
            sale_price=Decimal("20.00"),
        )

        self.variant = ProductVariant.objects.create(
            company=self.company,
            product=self.product,
            sku="HTTP-PP-SKU",
            cost_price=Decimal("10.00"),
            sale_price=Decimal("20.00"),
        )

        self.order = PurchaseOrder.objects.create(
            company=self.company,
            supplier=self.supplier,
            warehouse=self.warehouse,
            status=PurchaseOrder.STATUS_SENT,
        )

        self.order_item = PurchaseOrderItem.objects.create(
            purchase_order=self.order,
            variant=self.variant,
            quantity_ordered=10,
            quantity_received=0,
            unit_cost=Decimal("10.00"),
        )

        self.payment = SupplierPayment.objects.create(
            company=self.company,
            purchase_order=self.order,
            supplier=self.supplier,
            amount=Decimal("20.00"),
            status=SupplierPayment.STATUS_PENDING,
        )

        # =====================================================
        # MISMA EMPRESA / OTRA BODEGA
        # =====================================================

        self.other_order = PurchaseOrder.objects.create(
            company=self.company,
            supplier=self.supplier,
            warehouse=self.other_warehouse,
            status=PurchaseOrder.STATUS_SENT,
        )

        self.other_payment = SupplierPayment.objects.create(
            company=self.company,
            purchase_order=self.other_order,
            supplier=self.supplier,
            amount=Decimal("10.00"),
            status=SupplierPayment.STATUS_PENDING,
        )

        # =====================================================
        # EMPRESA AJENA
        # =====================================================

        self.foreign_company = Company.objects.create(
            name="Empresa Ajena HTTP Pagos",
            slug="empresa-ajena-http-pagos",
        )

        self.foreign_warehouse = Warehouse.objects.create(
            company=self.foreign_company,
            name="Bodega Ajena HTTP Pagos",
            code="HTTP-PP-FOREIGN",
        )

        self.foreign_supplier = Supplier.objects.create(
            company=self.foreign_company,
            name="Proveedor Ajeno HTTP Pagos",
        )

        self.foreign_order = PurchaseOrder.objects.create(
            company=self.foreign_company,
            supplier=self.foreign_supplier,
            warehouse=self.foreign_warehouse,
            status=PurchaseOrder.STATUS_SENT,
        )

        self.foreign_payment = SupplierPayment.objects.create(
            company=self.foreign_company,
            purchase_order=self.foreign_order,
            supplier=self.foreign_supplier,
            amount=Decimal("10.00"),
            status=SupplierPayment.STATUS_PENDING,
        )

        self.client.force_login(self.user)

    # =========================================================
    # HELPERS
    # =========================================================

    def grant(self, capability_code):
        capability = self.Capability.objects.get(
            code=capability_code,
        )

        self.EmployeeCapabilityAssignment.objects.get_or_create(
            company=self.company,
            user=self.user,
            capability=capability,
            defaults={
                "is_active": True,
            },
        )

    def set_access_level(self, level):
        self.UserWarehouseAccess.objects.filter(
            pk=self.access.pk,
        ).update(
            access_level=level,
        )

        self.access.refresh_from_db()

    # =========================================================
    # REGISTRAR SIN CAPABILITY
    # =========================================================

    def test_register_without_capability_returns_403(self):
        from django.urls import reverse

        before_count = self.SupplierPayment.objects.filter(
            purchase_order=self.order,
        ).count()

        response = self.client.post(
            reverse(
                "purchases:registrar_pago_proveedor",
                args=[self.order.id],
            ),
            {
                "amount": "10.00",
                "method": "transfer",
            },
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        after_count = self.SupplierPayment.objects.filter(
            purchase_order=self.order,
        ).count()

        self.assertEqual(
            before_count,
            after_count,
        )

    # =========================================================
    # CREATE REQUIERE OPERATE
    # =========================================================

    def test_register_with_create_but_view_level_returns_403(self):
        from django.urls import reverse

        self.grant("supplier_payments.create")

        self.set_access_level(self.UserWarehouseAccess.LEVEL_VIEW)

        response = self.client.post(
            reverse(
                "purchases:registrar_pago_proveedor",
                args=[self.order.id],
            ),
            {
                "amount": "10.00",
                "method": "transfer",
            },
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_register_with_create_and_operate_is_allowed(self):
        from django.urls import reverse

        self.grant("supplier_payments.create")

        before_count = self.SupplierPayment.objects.filter(
            purchase_order=self.order,
        ).count()

        response = self.client.post(
            reverse(
                "purchases:registrar_pago_proveedor",
                args=[self.order.id],
            ),
            {
                "amount": "10.00",
                "method": "transfer",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        after_count = self.SupplierPayment.objects.filter(
            purchase_order=self.order,
        ).count()

        self.assertEqual(
            after_count,
            before_count + 1,
        )

    # =========================================================
    # BODEGA NO AUTORIZADA
    # =========================================================

    def test_same_company_unauthorized_warehouse_returns_403(self):
        from django.urls import reverse

        self.grant("supplier_payments.approve")

        self.set_access_level(self.UserWarehouseAccess.LEVEL_APPROVE)

        response = self.client.post(
            reverse(
                "purchases:aprobar_pago_proveedor",
                args=[self.other_payment.id],
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    # =========================================================
    # EMPRESA AJENA
    # =========================================================

    def test_foreign_company_payment_returns_404(self):
        from django.urls import reverse

        self.grant("supplier_payments.approve")

        self.set_access_level(self.UserWarehouseAccess.LEVEL_APPROVE)

        response = self.client.post(
            reverse(
                "purchases:aprobar_pago_proveedor",
                args=[self.foreign_payment.id],
            )
        )

        self.assertEqual(
            response.status_code,
            404,
        )

    # =========================================================
    # APROBAR REQUIERE APPROVE
    # =========================================================

    def test_approve_with_capability_but_operate_level_returns_403(self):
        from django.urls import reverse

        self.grant("supplier_payments.approve")

        response = self.client.post(
            reverse(
                "purchases:aprobar_pago_proveedor",
                args=[self.payment.id],
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        self.payment.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            self.SupplierPayment.STATUS_PENDING,
        )

    def test_approve_with_capability_and_approve_level_is_allowed(self):
        from django.urls import reverse

        self.grant("supplier_payments.approve")

        self.set_access_level(self.UserWarehouseAccess.LEVEL_APPROVE)

        response = self.client.post(
            reverse(
                "purchases:aprobar_pago_proveedor",
                args=[self.payment.id],
            )
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.payment.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            self.SupplierPayment.STATUS_APPROVED,
        )

    # =========================================================
    # RECHAZAR REQUIERE APPROVE
    # =========================================================

    def test_reject_with_approve_permission_is_allowed(self):
        from django.urls import reverse

        self.grant("supplier_payments.reject")

        self.set_access_level(self.UserWarehouseAccess.LEVEL_APPROVE)

        response = self.client.post(
            reverse(
                "purchases:rechazar_pago_proveedor",
                args=[self.payment.id],
            ),
            {
                "reason": "Comprobante inválido",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.payment.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            self.SupplierPayment.STATUS_REJECTED,
        )

    # =========================================================
    # CANCELAR REQUIERE MANAGE
    # =========================================================

    def test_cancel_with_approve_permission_returns_403(self):
        from django.urls import reverse

        self.grant("supplier_payments.cancel")

        self.set_access_level(self.UserWarehouseAccess.LEVEL_APPROVE)

        response = self.client.post(
            reverse(
                "purchases:cancelar_pago_proveedor",
                args=[self.payment.id],
            ),
            {
                "reason": "Intento con APPROVE",
            },
        )

        self.assertEqual(
            response.status_code,
            403,
        )

        self.payment.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            self.SupplierPayment.STATUS_PENDING,
        )

    def test_cancel_with_manage_permission_is_allowed(self):
        from django.urls import reverse

        self.grant("supplier_payments.cancel")

        self.set_access_level(self.UserWarehouseAccess.LEVEL_MANAGE)

        response = self.client.post(
            reverse(
                "purchases:cancelar_pago_proveedor",
                args=[self.payment.id],
            ),
            {
                "reason": "Cancelación autorizada con MANAGE",
            },
        )

        self.assertEqual(
            response.status_code,
            302,
        )

        self.payment.refresh_from_db()

        self.assertEqual(
            self.payment.status,
            self.SupplierPayment.STATUS_CANCELLED,
        )

    # =========================================================
    # COMPROBANTE - SIN PERMISO
    # =========================================================

    def test_supplier_payment_proof_without_permission_returns_403(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse

        self.payment.receipt_file = SimpleUploadedFile(
            "comprobante-f42.txt",
            b"comprobante privado WMS360",
            content_type="text/plain",
        )

        self.payment.save(
            update_fields=[
                "receipt_file",
                "updated_at",
            ]
        )

        response = self.client.get(
            reverse(
                "purchases:ver_comprobante_pago_proveedor",
                args=[self.payment.id],
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    # =========================================================
    # COMPROBANTE - CON PERMISO
    # =========================================================

    def test_supplier_payment_proof_with_permission_is_allowed(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse

        self.payment.receipt_file = SimpleUploadedFile(
            "comprobante-f42.txt",
            b"comprobante privado WMS360",
            content_type="text/plain",
        )

        self.payment.save(
            update_fields=[
                "receipt_file",
                "updated_at",
            ]
        )

        self.grant("supplier_payments.proof.view")

        response = self.client.get(
            reverse(
                "purchases:ver_comprobante_pago_proveedor",
                args=[self.payment.id],
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

    # =========================================================
    # COMPROBANTE - BODEGA NO AUTORIZADA
    # =========================================================

    def test_supplier_payment_proof_other_warehouse_returns_403(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse

        self.other_payment.receipt_file = SimpleUploadedFile(
            "otro-comprobante-f42.txt",
            b"comprobante otra bodega",
            content_type="text/plain",
        )

        self.other_payment.save(
            update_fields=[
                "receipt_file",
                "updated_at",
            ]
        )

        self.grant("supplier_payments.proof.view")

        response = self.client.get(
            reverse(
                "purchases:ver_comprobante_pago_proveedor",
                args=[self.other_payment.id],
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    # =========================================================
    # COMPROBANTE - EMPRESA AJENA
    # =========================================================

    def test_supplier_payment_proof_foreign_company_returns_404(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.urls import reverse

        self.foreign_payment.receipt_file = SimpleUploadedFile(
            "foreign-comprobante-f42.txt",
            b"archivo de otra empresa",
            content_type="text/plain",
        )

        self.foreign_payment.save(
            update_fields=[
                "receipt_file",
                "updated_at",
            ]
        )

        self.grant("supplier_payments.proof.view")

        response = self.client.get(
            reverse(
                "purchases:ver_comprobante_pago_proveedor",
                args=[self.foreign_payment.id],
            )
        )

        self.assertEqual(
            response.status_code,
            404,
        )
