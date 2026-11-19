from types import SimpleNamespace

from django.test import SimpleTestCase

from accounts.navigation import build_navigation


class GlobalNavigationProductionTests(SimpleTestCase):
    databases = set()

    def _request(self, view_name, kwargs=None):
        return SimpleNamespace(
            user=SimpleNamespace(is_authenticated=True),
            resolver_match=SimpleNamespace(
                view_name=view_name,
                kwargs=kwargs or {},
            ),
        )

    def test_root_view_has_no_back_button(self):
        nav = build_navigation(self._request("sales:lista_pedidos"))
        self.assertFalse(nav["show"])

    def test_create_order_returns_to_orders(self):
        nav = build_navigation(self._request("sales:crear_pedido"))
        self.assertTrue(nav["show"])
        self.assertEqual(nav["back_url"], "/sales/orders/")
        self.assertEqual(nav["back_label"], "Volver a Pedidos")

    def test_product_edit_preserves_product_id_without_database_lookup(self):
        nav = build_navigation(
            self._request("inventory:editar_producto", {"product_id": 123})
        )
        self.assertTrue(nav["show"])
        self.assertEqual(nav["back_url"], "/inventory/products/123/")
        self.assertNotIn("http://", nav["back_url"])
        self.assertNotIn("https://", nav["back_url"])

    def test_missing_dynamic_kwarg_falls_back_to_module_root(self):
        nav = build_navigation(self._request("inventory:editar_producto"))
        self.assertTrue(nav["show"])
        self.assertEqual(nav["back_url"], "/inventory/products/")

    def test_payment_order_returns_to_same_order(self):
        nav = build_navigation(
            self._request("payments:pagos_pedido", {"order_id": 77})
        )
        self.assertTrue(nav["show"])
        self.assertEqual(nav["back_url"], "/sales/orders/77/")

    def test_unknown_view_does_not_invent_destination(self):
        nav = build_navigation(self._request("unknown_view"))
        self.assertFalse(nav["show"])

    def test_webhook_never_exposes_navigation(self):
        nav = build_navigation(self._request("messaging:meta_whatsapp_webhook"))
        self.assertFalse(nav["show"])

    def test_anonymous_user_has_no_navigation(self):
        request = self._request("sales:crear_pedido")
        request.user = SimpleNamespace(is_authenticated=False)
        nav = build_navigation(request)
        self.assertFalse(nav["show"])
