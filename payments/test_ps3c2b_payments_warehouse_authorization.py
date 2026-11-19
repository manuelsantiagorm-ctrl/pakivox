import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, SimpleTestCase

from accounts.simple_activity_route_contract import route_action_contract
from payments import views


class PS3C2BPaymentsWarehouseAuthorizationTests(SimpleTestCase):
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

        self.payment = MagicMock()
        self.payment.id = 501
        self.payment.pk = 501
        self.payment.company_id = 41
        self.payment.order = self.order
        self.payment.order_id = 901
        self.payment.folio = "PAY-TEST"

        self.refund = MagicMock()
        self.refund.id = 601
        self.refund.pk = 601
        self.refund.company_id = 41
        self.refund.order = self.order
        self.refund.order_id = 901
        self.refund.payment = self.payment
        self.refund.payment_id = 501

    def _payment_queryset_stub(self):
        queryset = MagicMock()
        queryset.filter.return_value = queryset
        queryset.select_related.return_value = queryset
        return queryset

    def _refund_queryset_stub(self):
        queryset = MagicMock()
        queryset.filter.return_value = queryset
        queryset.select_related.return_value = queryset
        return queryset

    def _order_queryset_stub(self):
        queryset = MagicMock()
        queryset.filter.return_value = queryset
        queryset.select_related.return_value = queryset
        return queryset

    def _request(self, path="/payments/test/"):
        request = self.factory.post(path, {})
        request.user = self.user
        return request

    def _assert_contract(
        self,
        route_name,
        *,
        level,
        capability,
    ):
        contract = route_action_contract(
            "payments",
            route_name,
        )

        self.assertEqual(
            contract.activity_code,
            "collections",
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

    def test_contract_approve_payment_is_approve(self):
        self._assert_contract(
            "aprobar_pago",
            level="approve",
            capability="payments.approve",
        )

    def test_contract_reject_payment_is_approve(self):
        self._assert_contract(
            "rechazar_pago",
            level="approve",
            capability="payments.approve",
        )

    def test_contract_cancel_payment_is_approve(self):
        self._assert_contract(
            "cancelar_pago",
            level="approve",
            capability="payments.cancel",
        )

    def test_contract_request_refund_is_manage(self):
        self._assert_contract(
            "reembolsar_pago",
            level="manage",
            capability="payments.refund",
        )

    def test_contract_confirm_refund_is_manage(self):
        self._assert_contract(
            "confirmar_reembolso_pago",
            level="manage",
            capability="payments.refund.confirm",
        )

    def test_contract_convert_refund_credit_is_manage(self):
        self._assert_contract(
            "convertir_reembolso_saldo_favor",
            level="manage",
            capability="payments.customer_credit.manage",
        )

    def test_contract_apply_credit_is_manage(self):
        self._assert_contract(
            "aplicar_saldo_favor_pedido",
            level="manage",
            capability="payments.customer_credit.apply",
        )

    def test_helper_passes_all_warehouses_to_ps3a(self):
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
            views._authorize_payment_order_activity_route(
                request,
                "aprobar_pago",
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
            "payments",
            "aprobar_pago",
            warehouses=(warehouse_1, warehouse_2),
        )

    def test_approve_allows_read_only_planning(self):
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
            views._authorize_payment_order_activity_route(
                request,
                "aprobar_pago",
                self.order,
                plan_if_missing=True,
            )

        resolver.assert_called_once_with(
            self.order,
            plan_if_missing=True,
            fallback_to_company=True,
        )

    def test_apply_credit_allows_read_only_planning(self):
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
            views._authorize_payment_order_activity_route(
                request,
                "aplicar_saldo_favor_pedido",
                self.order,
                plan_if_missing=True,
            )

        resolver.assert_called_once_with(
            self.order,
            plan_if_missing=True,
            fallback_to_company=True,
        )

    def test_cancel_does_not_plan_new_fulfillment(self):
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
            views._authorize_payment_order_activity_route(
                request,
                "cancelar_pago",
                self.order,
                plan_if_missing=False,
            )

        resolver.assert_called_once_with(
            self.order,
            plan_if_missing=False,
            fallback_to_company=True,
        )

    def test_refund_does_not_plan_new_fulfillment(self):
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
            views._authorize_payment_order_activity_route(
                request,
                "reembolsar_pago",
                self.order,
                plan_if_missing=False,
            )

        resolver.assert_called_once_with(
            self.order,
            plan_if_missing=False,
            fallback_to_company=True,
        )

    def _call_payment_view_denied(
        self,
        view_function,
        route_name,
        *,
        service_patch=None,
    ):
        request = self._request()
        payment_queryset = self._payment_queryset_stub()

        with (
            patch.object(
                views,
                "get_user_company",
                return_value=self.company,
            ),
            patch.object(
                views.Payment.objects,
                "select_related",
                return_value=payment_queryset,
            ),
            patch.object(
                views.Payment.objects,
                "filter",
                return_value=payment_queryset,
            ),
            patch.object(
                views,
                "get_object_or_404",
                return_value=self.payment,
            ),
            patch.object(
                views,
                "_authorize_payment_order_activity_route",
                side_effect=PermissionDenied("denied"),
            ) as authorize,
        ):
            if service_patch:
                with patch(service_patch) as business:
                    with self.assertRaises(PermissionDenied):
                        inspect.unwrap(view_function)(
                            request,
                            payment_id=self.payment.id,
                        )
                    business.assert_not_called()
            else:
                with self.assertRaises(PermissionDenied):
                    inspect.unwrap(view_function)(
                        request,
                        payment_id=self.payment.id,
                    )

        authorize.assert_called_once()
        self.assertEqual(
            authorize.call_args.args[1],
            route_name,
        )

    def _call_refund_view_denied(
        self,
        view_function,
        route_name,
        service_patch,
    ):
        request = self._request()
        refund_queryset = self._refund_queryset_stub()

        with (
            patch.object(
                views,
                "get_user_company",
                return_value=self.company,
            ),
            patch.object(
                views.PaymentRefundRequest.objects,
                "filter",
                return_value=refund_queryset,
            ),
            patch.object(
                views,
                "get_object_or_404",
                return_value=self.refund,
            ),
            patch.object(
                views,
                "_authorize_payment_order_activity_route",
                side_effect=PermissionDenied("denied"),
            ) as authorize,
            patch(service_patch) as business,
        ):
            with self.assertRaises(PermissionDenied):
                inspect.unwrap(view_function)(
                    request,
                    refund_id=self.refund.id,
                )

            business.assert_not_called()

        authorize.assert_called_once()
        self.assertEqual(
            authorize.call_args.args[1],
            route_name,
        )

    def test_approve_denied_before_payment_approve(self):
        self._call_payment_view_denied(
            views.aprobar_pago,
            "aprobar_pago",
        )
        self.payment.approve.assert_not_called()

    def test_reject_denied_before_payment_reject(self):
        self._call_payment_view_denied(
            views.rechazar_pago,
            "rechazar_pago",
        )
        self.payment.reject.assert_not_called()

    def test_cancel_denied_before_payment_cancel(self):
        self._call_payment_view_denied(
            views.cancelar_pago,
            "cancelar_pago",
        )
        self.payment.cancel.assert_not_called()

    def test_request_refund_denied_before_refund_service(self):
        self._call_payment_view_denied(
            views.reembolsar_pago,
            "reembolsar_pago",
            service_patch="payments.views.request_full_refund",
        )

    def test_confirm_refund_denied_before_confirm_service(self):
        self._call_refund_view_denied(
            views.confirmar_reembolso_pago,
            "confirmar_reembolso_pago",
            "payments.views.confirm_manual_full_refund",
        )

    def test_convert_credit_denied_before_credit_service(self):
        self._call_refund_view_denied(
            views.convertir_reembolso_saldo_favor,
            "convertir_reembolso_saldo_favor",
            "payments.views.convert_refund_to_customer_credit",
        )

    def test_apply_credit_denied_before_credit_application_service(self):
        request = self._request()
        order_queryset = self._order_queryset_stub()

        with (
            patch.object(
                views,
                "get_user_company",
                return_value=self.company,
            ),
            patch.object(
                views.Order.objects,
                "filter",
                return_value=order_queryset,
            ),
            patch.object(
                views,
                "get_object_or_404",
                return_value=self.order,
            ),
            patch.object(
                views,
                "_authorize_payment_order_activity_route",
                side_effect=PermissionDenied("denied"),
            ) as authorize,
            patch(
                "payments.views.apply_customer_credit_to_order"
            ) as business,
        ):
            with self.assertRaises(PermissionDenied):
                inspect.unwrap(
                    views.aplicar_saldo_favor_pedido
                )(
                    request,
                    order_id=self.order.id,
                )

            business.assert_not_called()

        authorize.assert_called_once()
        self.assertEqual(
            authorize.call_args.args[1],
            "aplicar_saldo_favor_pedido",
        )

    def test_refund_lookup_uses_order_for_scope(self):
        request = self._request()
        refund_queryset = self._refund_queryset_stub()

        with (
            patch.object(
                views,
                "get_user_company",
                return_value=self.company,
            ),
            patch.object(
                views.PaymentRefundRequest.objects,
                "filter",
                return_value=refund_queryset,
            ),
            patch.object(
                views,
                "get_object_or_404",
                return_value=self.refund,
            ),
            patch.object(
                views,
                "_authorize_payment_order_activity_route",
                side_effect=PermissionDenied("denied"),
            ) as authorize,
        ):
            with self.assertRaises(PermissionDenied):
                inspect.unwrap(
                    views.confirmar_reembolso_pago
                )(
                    request,
                    refund_id=self.refund.id,
                )

        self.assertIs(
            authorize.call_args.args[2],
            self.refund.order,
        )

    def test_payment_lookup_uses_order_for_scope(self):
        request = self._request()
        payment_queryset = self._payment_queryset_stub()

        with (
            patch.object(
                views,
                "get_user_company",
                return_value=self.company,
            ),
            patch.object(
                views.Payment.objects,
                "select_related",
                return_value=payment_queryset,
            ),
            patch.object(
                views,
                "get_object_or_404",
                return_value=self.payment,
            ),
            patch.object(
                views,
                "_authorize_payment_order_activity_route",
                side_effect=PermissionDenied("denied"),
            ) as authorize,
        ):
            with self.assertRaises(PermissionDenied):
                inspect.unwrap(
                    views.aprobar_pago
                )(
                    request,
                    payment_id=self.payment.id,
                )

        self.assertIs(
            authorize.call_args.args[2],
            self.payment.order,
        )
