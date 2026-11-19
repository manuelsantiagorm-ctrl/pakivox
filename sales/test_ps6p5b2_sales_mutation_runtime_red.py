import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, SimpleTestCase

from sales import views


class PS6P5B2SalesMutationRuntimeRedTests(SimpleTestCase):
    """
    PS-6P.5B2

    Contrato:
    una denegacion del motor de autoridad debe ocurrir antes
    de cualquier logica que pueda modificar el pedido.
    """

    def setUp(self):
        self.factory = RequestFactory()

        self.user = SimpleNamespace(
            id=77,
            pk=77,
            is_authenticated=True,
        )

        self.company = SimpleNamespace(
            id=41,
            pk=41,
        )

        self.order = MagicMock()
        self.order.id = 901
        self.order.pk = 901
        self.order.company_id = 41

    def _request(self):
        request = self.factory.post(
            "/sales/ps6p5b2/",
            {},
        )

        request.user = self.user

        return request

    def _assert_authority_blocks_before_edit_logic(
        self,
        *,
        view_function,
        route_name,
        item_id=None,
    ):
        request = self._request()

        scoped_queryset = MagicMock(
            name="scoped_order_queryset",
        )

        business_reached = AssertionError(
            "La vista alcanzo pedido_permite_edicion "
            "antes de aplicar autoridad de bodega."
        )

        with (
            patch.object(
                views,
                "get_user_company",
                return_value=self.company,
            ),
            patch.object(
                views,
                "scope_orders_for_sales_user",
                return_value=scoped_queryset,
            ),
            patch.object(
                views,
                "get_object_or_404",
                return_value=self.order,
            ),
            patch.object(
                views,
                "_authorize_order_activity_route",
                side_effect=PermissionDenied(
                    "warehouse authority denied"
                ),
            ) as authorize,
            patch.object(
                views,
                "pedido_permite_edicion",
                side_effect=business_reached,
            ) as edit_logic,
        ):

            kwargs = {
                "order_id": self.order.id,
            }

            if item_id is not None:
                kwargs["item_id"] = item_id

            with self.assertRaises(
                PermissionDenied
            ):
                inspect.unwrap(
                    view_function
                )(
                    request,
                    **kwargs,
                )

        authorize.assert_called_once()

        self.assertEqual(
            authorize.call_args.args[0],
            request,
        )

        self.assertEqual(
            authorize.call_args.args[1],
            route_name,
        )

        self.assertIs(
            authorize.call_args.args[2],
            self.order,
        )

        edit_logic.assert_not_called()

    def test_add_item_denied_before_edit_logic(self):
        self._assert_authority_blocks_before_edit_logic(
            view_function=views.agregar_producto_pedido,
            route_name="agregar_producto_pedido",
        )

    def test_delete_item_denied_before_edit_logic(self):
        self._assert_authority_blocks_before_edit_logic(
            view_function=views.eliminar_producto_pedido,
            route_name="eliminar_producto_pedido",
            item_id=7001,
        )

    def test_update_item_denied_before_edit_logic(self):
        self._assert_authority_blocks_before_edit_logic(
            view_function=views.actualizar_producto_pedido,
            route_name="actualizar_producto_pedido",
            item_id=7001,
        )