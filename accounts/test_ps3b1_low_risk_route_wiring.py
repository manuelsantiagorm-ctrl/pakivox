import ast
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from companies.models import Company
from inventory.models import Warehouse
from subscriptions.models import Plan, Subscription

from accounts.models import (
    Capability,
    EmployeeCapabilityAssignment,
    UserProfile,
    UserWarehouseAccess,
)
from accounts.simple_employee_access import ensure_company_activity_templates


User = get_user_model()


TARGET_READ_ROUTES = {
    "pendientes_por_enviar",
    "lista_picking",
    "detalle_picking",
    "lista_packing",
    "detalle_packing",
    "listos_despacho",
    "pedidos_en_ruta",
    "lista_shipping",
    "detalle_shipping",
    "timeline_pedido",
}


def _module_source(relative_path):
    return (
        Path(settings.BASE_DIR)
        / relative_path
    ).read_text(
        encoding="utf-8-sig",
    )


def _literal_set_assignment(tree, name):
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue

        if not any(
            isinstance(target, ast.Name)
            and target.id == name
            for target in node.targets
        ):
            continue

        if not isinstance(node.value, (ast.Set, ast.List, ast.Tuple)):
            raise AssertionError(
                f"{name} debe ser un literal simple."
            )

        values = set()

        for element in node.value.elts:
            if not isinstance(element, ast.Constant):
                raise AssertionError(
                    f"{name} contiene un valor no literal."
                )
            values.add(element.value)

        return values

    raise AssertionError(
        f"No se encontró {name}."
    )


def _literal_dict_assignment(tree, name):
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue

        if not any(
            isinstance(target, ast.Name)
            and target.id == name
            for target in node.targets
        ):
            continue

        if not isinstance(node.value, ast.Dict):
            raise AssertionError(
                f"{name} debe ser un dict literal."
            )

        result = {}

        for key, value in zip(
            node.value.keys,
            node.value.values,
        ):
            if not (
                isinstance(key, ast.Constant)
                and isinstance(value, ast.Constant)
            ):
                raise AssertionError(
                    f"{name} contiene valores no literales."
                )

            result[key.value] = value.value

        return result

    raise AssertionError(
        f"No se encontró {name}."
    )


def _required_level_for_named_path(tree, route_name):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        if not (
            isinstance(node.func, ast.Name)
            and node.func.id == "path"
        ):
            continue

        named = None

        for keyword in node.keywords:
            if (
                keyword.arg == "name"
                and isinstance(keyword.value, ast.Constant)
            ):
                named = keyword.value.value

        if named != route_name:
            continue

        if len(node.args) < 2:
            raise AssertionError(
                f"Ruta {route_name} sin callback."
            )

        callback = node.args[1]

        if not isinstance(callback, ast.Call):
            raise AssertionError(
                f"Ruta {route_name} no usa helper seguro."
            )

        for keyword in callback.keywords:
            if keyword.arg != "required_level":
                continue

            if isinstance(keyword.value, ast.Name):
                return keyword.value.id

            raise AssertionError(
                f"{route_name} tiene required_level no nominal."
            )

        raise AssertionError(
            f"{route_name} no declara required_level."
        )

    raise AssertionError(
        f"No se encontró la ruta {route_name}."
    )


class PS3B1StaticWiringTests(TestCase):
    def test_target_read_route_set_is_exact(self):
        source = _module_source(
            "warehouse/urls.py",
        )
        tree = ast.parse(source)

        self.assertEqual(
            _literal_set_assignment(
                tree,
                "PS3B_ACTIVITY_READ_ROUTES",
            ),
            TARGET_READ_ROUTES,
        )

    def test_warehouse_urls_import_common_activity_engine(self):
        source = _module_source(
            "warehouse/urls.py",
        )

        self.assertIn(
            "from accounts.activity_route_authorization "
            "import activity_route_required",
            source,
        )

    def test_target_routes_are_wired_through_common_engine(self):
        source = _module_source(
            "warehouse/urls.py",
        )

        self.assertIn(
            'activity_route_required(\n'
            '            "warehouse",\n'
            '            pattern.name,\n'
            '        )(pattern.callback)',
            source,
        )

    def test_picking_read_routes_are_not_in_execute_set(self):
        tree = ast.parse(
            _module_source(
                "warehouse/urls.py",
            )
        )

        execute_routes = _literal_set_assignment(
            tree,
            "PICKING_EXECUTE_ROUTES",
        )

        self.assertTrue(
            {
                "pendientes_por_enviar",
                "lista_picking",
                "detalle_picking",
            }.isdisjoint(
                execute_routes,
            )
        )

    def test_packing_read_routes_are_not_in_execute_set(self):
        tree = ast.parse(
            _module_source(
                "warehouse/urls.py",
            )
        )

        execute_routes = _literal_set_assignment(
            tree,
            "PACKING_EXECUTE_ROUTES",
        )

        self.assertTrue(
            {
                "lista_packing",
                "detalle_packing",
            }.isdisjoint(
                execute_routes,
            )
        )

    def test_internal_picking_read_gate_is_picking_view(self):
        tree = ast.parse(
            _module_source(
                "warehouse/views.py"
            )
        )

        mapping = _literal_dict_assignment(
            tree,
            "_WAREHOUSE_CAPABILITIES",
        )

        self.assertEqual(
            mapping["lista_picking"],
            "picking.view",
        )

        self.assertEqual(
            mapping["detalle_picking"],
            "picking.view",
        )

        self.assertEqual(
            mapping["generar_picking"],
            "picking.execute",
        )

    def test_internal_packing_read_gate_is_packing_view(self):
        tree = ast.parse(
            _module_source(
                "warehouse/views.py"
            )
        )

        mapping = _literal_dict_assignment(
            tree,
            "_WAREHOUSE_CAPABILITIES",
        )

        self.assertEqual(
            mapping["lista_packing"],
            "packing.view",
        )

        self.assertEqual(
            mapping["detalle_packing"],
            "packing.view",
        )

        self.assertEqual(
            mapping["generar_packing"],
            "packing.execute",
        )

    def test_internal_shipping_read_gate_is_shipping_view(self):
        tree = ast.parse(
            _module_source(
                "warehouse/views.py",
            )
        )

        mapping = _literal_dict_assignment(
            tree,
            "_WAREHOUSE_CAPABILITIES",
        )

        self.assertEqual(
            mapping["lista_shipping"],
            "shipping.view",
        )
        self.assertEqual(
            mapping["detalle_shipping"],
            "shipping.view",
        )
        self.assertEqual(
            mapping["generar_shipping"],
            "shipping.execute",
        )

    def test_cancel_receipt_requires_manage(self):
        tree = ast.parse(
            _module_source(
                "purchases/urls.py",
            )
        )

        self.assertEqual(
            _required_level_for_named_path(
                tree,
                "cancelar_recepcion",
            ),
            "ACCESS_MANAGE",
        )

    def test_cancel_supplier_payment_requires_manage(self):
        tree = ast.parse(
            _module_source(
                "purchases/urls.py",
            )
        )

        self.assertEqual(
            _required_level_for_named_path(
                tree,
                "cancelar_pago_proveedor",
            ),
            "ACCESS_MANAGE",
        )


class PS3B1RuntimeReadRouteTests(TestCase):
    def setUp(self):
        self.client = Client()

        self.company = Company.objects.create(
            name="PS3B1 Empresa",
            slug="ps3b1-empresa",
        )

        today = timezone.localdate()

        plan = Plan.objects.create(
            name="PS3B1 Plan",
            slug="ps3b1-plan",
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

        self.user = User.objects.create_user(
            "ps3b1-worker@test.local",
            password="PS3B1-StrongPass-123!",
        )

        UserProfile.objects.create(
            user=self.user,
            company=self.company,
            role="warehouse",
            is_active=True,
            employment_status=UserProfile.STATUS_ACTIVE,
        )

        self.warehouse = Warehouse.objects.create(
            company=self.company,
            name="Principal PS3B1",
            code="PS3B1-A",
        )

        ensure_company_activity_templates(
            self.company,
        )

        UserWarehouseAccess.objects.create(
            company=self.company,
            user=self.user,
            warehouse=self.warehouse,
            access_level=UserWarehouseAccess.LEVEL_VIEW,
            is_active=True,
        )

        self.client.force_login(
            self.user,
        )

    def _direct(self, capability_code):
        capability = Capability.objects.get(
            code=capability_code,
            is_active=True,
        )

        return EmployeeCapabilityAssignment.objects.create(
            company=self.company,
            user=self.user,
            capability=capability,
            source=EmployeeCapabilityAssignment.SOURCE_DIRECT,
            is_active=True,
        )

    def test_inventory_view_direct_cannot_open_picking_list(self):
        self._direct(
            "inventory.view",
        )

        response = self.client.get(
            reverse(
                "warehouse:lista_picking",
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_inventory_view_direct_cannot_probe_missing_picking_object(self):
        self._direct(
            "inventory.view",
        )

        response = self.client.get(
            reverse(
                "warehouse:detalle_picking",
                kwargs={
                    "picking_id": 999999,
                },
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_picking_view_direct_can_open_picking_list(self):
        self._direct(
            "picking.view",
        )

        response = self.client.get(
            reverse(
                "warehouse:lista_picking",
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

    def test_picking_view_direct_reaches_missing_object_as_404(self):
        self._direct(
            "picking.view",
        )

        response = self.client.get(
            reverse(
                "warehouse:detalle_picking",
                kwargs={
                    "picking_id": 999999,
                },
            )
        )

        self.assertEqual(
            response.status_code,
            404,
        )

    def test_picking_execute_without_picking_view_cannot_read_picking(self):
        self._direct(
            "picking.execute",
        )

        response = self.client.get(
            reverse(
                "warehouse:lista_picking",
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_inventory_view_direct_cannot_open_packing_list(self):
        self._direct(
            "inventory.view",
        )

        response = self.client.get(
            reverse(
                "warehouse:lista_packing",
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_inventory_view_direct_cannot_probe_missing_packing_object(self):
        self._direct(
            "inventory.view",
        )

        response = self.client.get(
            reverse(
                "warehouse:detalle_packing",
                kwargs={
                    "packing_id": 999999,
                },
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_packing_view_direct_can_open_packing_list(self):
        self._direct(
            "packing.view",
        )

        response = self.client.get(
            reverse(
                "warehouse:lista_packing",
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

    def test_packing_view_direct_reaches_missing_object_as_404(self):
        self._direct(
            "packing.view",
        )

        response = self.client.get(
            reverse(
                "warehouse:detalle_packing",
                kwargs={
                    "packing_id": 999999,
                },
            )
        )

        self.assertEqual(
            response.status_code,
            404,
        )

    def test_packing_execute_without_packing_view_cannot_read_packing(self):
        self._direct(
            "packing.execute",
        )

        response = self.client.get(
            reverse(
                "warehouse:lista_packing",
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_receiving_view_direct_can_open_receiving_list(self):
        self._direct(
            "receiving.view",
        )

        response = self.client.get(
            reverse(
                "purchases:lista_recepciones",
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

    def test_inventory_view_direct_cannot_open_receiving_list(self):
        self._direct(
            "inventory.view",
        )

        response = self.client.get(
            reverse(
                "purchases:lista_recepciones",
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_receiving_view_direct_cannot_open_inventory_stock(self):
        self._direct(
            "receiving.view",
        )

        response = self.client.get(
            reverse(
                "inventory:lista_stock",
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_shipping_view_direct_can_open_shipping_list(self):
        self._direct(
            "shipping.view",
        )

        response = self.client.get(
            reverse(
                "warehouse:lista_shipping",
            )
        )

        self.assertEqual(
            response.status_code,
            200,
        )

    def test_shipping_view_direct_reaches_missing_shipping_object_as_404(self):
        self._direct(
            "shipping.view",
        )

        response = self.client.get(
            reverse(
                "warehouse:detalle_shipping",
                kwargs={"shipping_id": 999999},
            )
        )

        self.assertEqual(
            response.status_code,
            404,
        )

    def test_shipping_execute_without_shipping_view_cannot_read_shipping(self):
        self._direct(
            "shipping.execute",
        )

        response = self.client.get(
            reverse(
                "warehouse:lista_shipping",
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )

    def test_sales_view_direct_reaches_missing_timeline_order_as_404(self):
        self._direct(
            "sales.view",
        )

        response = self.client.get(
            reverse(
                "warehouse:timeline_pedido",
                kwargs={"order_id": 999999},
            )
        )

        self.assertEqual(
            response.status_code,
            404,
        )

    def test_inventory_view_without_sales_view_cannot_open_order_timeline(self):
        self._direct(
            "inventory.view",
        )

        response = self.client.get(
            reverse(
                "warehouse:timeline_pedido",
                kwargs={"order_id": 999999},
            )
        )

        self.assertEqual(
            response.status_code,
            403,
        )
