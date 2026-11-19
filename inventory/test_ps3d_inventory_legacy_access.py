import inspect
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, SimpleTestCase

from inventory import views


class PS3DInventoryLegacyAccessTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _request(self, path):
        request = self.factory.get(path)
        request.user = SimpleNamespace(
            is_authenticated=True,
            is_superuser=False,
        )
        return request

    def test_manage_ui_is_capability_driven_even_for_legacy_sales_role(self):
        user = SimpleNamespace(
            is_authenticated=True,
            profile=SimpleNamespace(role="sales"),
        )

        with patch.object(
            views,
            "user_has_capability",
            return_value=True,
        ) as resolver:
            allowed = views._can_manage_inventory(user)

        self.assertTrue(allowed)
        resolver.assert_called_once_with(
            user,
            "inventory.manage",
        )

    def test_manage_ui_respects_capability_denial_even_for_legacy_manager_role(self):
        user = SimpleNamespace(
            is_authenticated=True,
            profile=SimpleNamespace(role="manager"),
        )

        with patch.object(
            views,
            "user_has_capability",
            return_value=False,
        ) as resolver:
            allowed = views._can_manage_inventory(user)

        self.assertFalse(allowed)
        resolver.assert_called_once_with(
            user,
            "inventory.manage",
        )

    def test_detail_product_has_no_management_role_gate(self):
        source = inspect.getsource(
            inspect.unwrap(views.detalle_producto)
        )

        self.assertNotIn("allowed_management_roles", source)
        self.assertNotIn("profile.role", source)
        self.assertIn("_can_manage_inventory(", source)

    def test_add_stock_has_no_legacy_role_gate(self):
        source = inspect.getsource(
            inspect.unwrap(views.agregar_stock)
        )

        self.assertNotIn("allowed_roles", source)
        self.assertNotIn("profile.role", source)
        self.assertNotIn("Tu rol no permite", source)

    def test_create_product_has_no_legacy_role_gate(self):
        source = inspect.getsource(
            inspect.unwrap(views.crear_producto)
        )

        self.assertNotIn("allowed_roles", source)
        self.assertNotIn("profile.role", source)
        self.assertNotIn("Tu rol no permite", source)

    def test_detail_product_keeps_inventory_view_contract(self):
        self.assertEqual(
            views._INVENTORY_CAPABILITIES["detalle_producto"],
            "inventory.view",
        )

    def test_add_stock_keeps_inventory_manage_contract(self):
        self.assertEqual(
            views._INVENTORY_CAPABILITIES["agregar_stock"],
            "inventory.manage",
        )

    def test_create_product_keeps_inventory_manage_contract(self):
        self.assertEqual(
            views._INVENTORY_CAPABILITIES["crear_producto"],
            "inventory.manage",
        )

    def test_add_stock_denied_by_capability_before_view_body(self):
        request = self._request("/inventory/products/1/add-stock/")

        with patch(
            "accounts.capability_access.authorize_request",
            side_effect=PermissionDenied("denied"),
        ) as authorize:
            with self.assertRaises(PermissionDenied):
                views.agregar_stock(
                    request,
                    product_id=1,
                )

        self.assertEqual(
            authorize.call_args.args[1],
            "inventory.manage",
        )

    def test_create_product_denied_by_capability_before_view_body(self):
        request = self._request("/inventory/products/create/")

        with patch(
            "accounts.capability_access.authorize_request",
            side_effect=PermissionDenied("denied"),
        ) as authorize:
            with self.assertRaises(PermissionDenied):
                views.crear_producto(request)

        self.assertEqual(
            authorize.call_args.args[1],
            "inventory.manage",
        )

    def test_detail_product_denied_by_view_capability_before_view_body(self):
        request = self._request("/inventory/products/1/")

        with patch(
            "accounts.capability_access.authorize_request",
            side_effect=PermissionDenied("denied"),
        ) as authorize:
            with self.assertRaises(PermissionDenied):
                views.detalle_producto(
                    request,
                    product_id=1,
                )

        self.assertEqual(
            authorize.call_args.args[1],
            "inventory.view",
        )
