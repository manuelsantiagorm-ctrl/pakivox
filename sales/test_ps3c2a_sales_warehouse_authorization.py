import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, SimpleTestCase

from accounts.simple_activity_route_contract import route_action_contract
from sales import views


class PS3C2ASalesWarehouseAuthorizationTests(SimpleTestCase):
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

    def _request(self, path="/sales/test/"):
        request = self.factory.post(path, {})
        request.user = self.user
        return request

    def _assert_contract(
        self,
        route_name,
        *,
        activity,
        level,
        capability,
    ):
        contract = route_action_contract(
            "sales",
            route_name,
        )

        self.assertEqual(
            contract.activity_code,
            activity,
        )
        self.assertEqual(
            contract.minimum_level,
            level,
        )
        self.assertEqual(
            contract.capability_code,
            capability,
        )
        self.assertEqual(
            contract.scope,
            "multiwarehouse",
        )

    def test_contract_update_status_is_operate(self):
        self._assert_contract(
            "actualizar_estado_pedido",
            activity="sales_customers",
            level="operate",
            capability="sales.create",
        )

    def test_contract_update_payment_is_approve(self):
        self._assert_contract(
            "actualizar_pago_pedido",
            activity="collections",
            level="approve",
            capability="payments.approve",
        )

    def test_contract_mark_paid_is_approve(self):
        self._assert_contract(
            "marcar_pedido_pagado",
            activity="collections",
            level="approve",
            capability="payments.approve",
        )

    def test_contract_cancel_order_is_approve(self):
        self._assert_contract(
            "cancelar_pedido",
            activity="sales_customers",
            level="approve",
            capability="sales.cancel_order",
        )

    def test_contract_recover_order_is_approve(self):
        self._assert_contract(
            "recuperar_pedido_error_humano",
            activity="sales_customers",
            level="approve",
            capability="sales.recover_order",
        )

    def test_contract_commercial_cancel_is_manage(self):
        self._assert_contract(
            "cancelar_pedido_comercialmente",
            activity="sales_customers",
            level="manage",
            capability="sales.commercial_cancel",
        )

    def test_helper_passes_all_resolved_warehouses_to_ps3a(self):
        request = self._request()
        warehouse_1 = SimpleNamespace(pk=1, id=1)
        warehouse_2 = SimpleNamespace(pk=2, id=2)

        with (
            patch(
                "warehouse.services.order_authorization."
                "resolve_order_authorization_warehouses",
                return_value=(warehouse_1, warehouse_2),
            ) as resolver,
            patch(
                "accounts.activity_route_authorization."
                "authorize_activity_route"
            ) as authorize,
        ):
            views._authorize_order_activity_route(
                request,
                "actualizar_pago_pedido",
                self.order,
                plan_if_missing=True,
            )

        resolver.assert_called_once_with(
            self.order,
            plan_if_missing=True,
            fallback_to_company=True,
        )
        authorize.assert_called_once_with(
            request,
            "sales",
            "actualizar_pago_pedido",
            warehouses=(warehouse_1, warehouse_2),
        )

    def test_cancel_helper_does_not_plan_new_fulfillment(self):
        request = self._request()
        warehouse = SimpleNamespace(pk=1, id=1)

        with (
            patch(
                "warehouse.services.order_authorization."
                "resolve_order_authorization_warehouses",
                return_value=(warehouse,),
            ) as resolver,
            patch(
                "accounts.activity_route_authorization."
                "authorize_activity_route"
            ),
        ):
            views._authorize_order_activity_route(
                request,
                "cancelar_pedido",
                self.order,
                plan_if_missing=False,
            )

        resolver.assert_called_once_with(
            self.order,
            plan_if_missing=False,
            fallback_to_company=True,
        )

    def _assert_denied_before_business_logic(
        self,
        view_function,
        route_name,
        *,
        scope_function,
        business_patch=None,
    ):
        request = self._request()

        with (
            patch.object(
                views,
                "get_user_company",
                return_value=self.company,
            ),
            patch.object(
                views,
                scope_function,
                return_value=MagicMock(),
            ),
            patch.object(
                views,
                "get_object_or_404",
                return_value=self.order,
            ),
            patch.object(
                views,
                "_authorize_order_activity_route",
                side_effect=PermissionDenied("denied"),
            ) as authorize,
        ):
            if business_patch is None:
                with self.assertRaises(PermissionDenied):
                    inspect.unwrap(view_function)(
                        request,
                        order_id=self.order.id,
                    )
            else:
                with patch(
                    business_patch
                ) as business:
                    with self.assertRaises(PermissionDenied):
                        inspect.unwrap(view_function)(
                            request,
                            order_id=self.order.id,
                        )

                    business.assert_not_called()

        authorize.assert_called_once()
        self.assertEqual(
            authorize.call_args.args[1],
            route_name,
        )

    def test_update_status_denied_before_order_mutation(self):
        self._assert_denied_before_business_logic(
            views.actualizar_estado_pedido,
            "actualizar_estado_pedido",
            scope_function="scope_orders_for_sales_user",
        )

        self.order.save.assert_not_called()

    def test_update_payment_denied_before_order_mutation(self):
        self._assert_denied_before_business_logic(
            views.actualizar_pago_pedido,
            "actualizar_pago_pedido",
            scope_function="scope_orders_for_company",
        )

        self.order.save.assert_not_called()

    def test_mark_paid_denied_before_order_mutation(self):
        self._assert_denied_before_business_logic(
            views.marcar_pedido_pagado,
            "marcar_pedido_pagado",
            scope_function="scope_orders_for_company",
        )

        self.order.save.assert_not_called()

    def test_safe_cancel_denied_before_cancellation_service(self):
        self._assert_denied_before_business_logic(
            views.cancelar_pedido,
            "cancelar_pedido",
            scope_function="scope_orders_for_sales_user",
            business_patch=(
                "sales.services.order_resolution."
                "cancel_order_safely"
            ),
        )

    def test_human_recovery_denied_before_recovery_service(self):
        self._assert_denied_before_business_logic(
            views.recuperar_pedido_error_humano,
            "recuperar_pedido_error_humano",
            scope_function="scope_orders_for_sales_user",
            business_patch=(
                "sales.services.order_resolution."
                "recover_order_from_human_error"
            ),
        )

    def test_commercial_cancel_denied_before_financial_service(self):
        self._assert_denied_before_business_logic(
            views.cancelar_pedido_comercialmente,
            "cancelar_pedido_comercialmente",
            scope_function="scope_orders_for_sales_user",
            business_patch=(
                "sales.services.order_resolution."
                "cancel_paid_order_commercially"
            ),
        )
