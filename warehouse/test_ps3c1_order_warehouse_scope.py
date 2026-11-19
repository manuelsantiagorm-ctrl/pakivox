from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from warehouse.services import order_authorization


class _FakeQuerySet(list):
    def select_related(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self


class PS3C1OrderWarehouseScopeTests(SimpleTestCase):
    def _order(self, company_id=7, order_id=91):
        return SimpleNamespace(
            pk=order_id,
            id=order_id,
            company_id=company_id,
        )

    def _warehouse(self, pk, company_id=7):
        return SimpleNamespace(
            pk=pk,
            id=pk,
            company_id=company_id,
        )

    def test_missing_order_is_rejected(self):
        with self.assertRaises(ValidationError):
            order_authorization.resolve_order_authorization_warehouses(None)

    def test_unsaved_order_is_rejected(self):
        order = SimpleNamespace(
            pk=None,
            company_id=7,
        )

        with self.assertRaises(ValidationError):
            order_authorization.resolve_order_authorization_warehouses(order)

    def test_missing_company_is_rejected(self):
        order = SimpleNamespace(
            pk=91,
            company_id=None,
        )

        with self.assertRaises(ValidationError):
            order_authorization.resolve_order_authorization_warehouses(order)

    def test_existing_scope_combines_fulfillments_and_legacy_pickings(self):
        order = self._order()
        warehouse_1 = self._warehouse(1)
        warehouse_2 = self._warehouse(2)

        fulfillment_rows = _FakeQuerySet([
            SimpleNamespace(
                warehouse=warehouse_2,
            ),
            SimpleNamespace(
                warehouse=warehouse_1,
            ),
        ])

        picking_rows = _FakeQuerySet([
            SimpleNamespace(
                warehouse=warehouse_1,
            ),
        ])

        with (
            patch.object(
                order_authorization.OrderFulfillment.objects,
                "filter",
                return_value=fulfillment_rows,
            ),
            patch.object(
                order_authorization.PickingOrder.objects,
                "filter",
                return_value=picking_rows,
            ),
        ):
            result = (
                order_authorization.existing_order_authorization_warehouses(
                    order
                )
            )

        self.assertEqual(
            [warehouse.pk for warehouse in result],
            [1, 2],
        )

    def test_existing_scope_rejects_foreign_company_warehouse(self):
        order = self._order(company_id=7)
        foreign = self._warehouse(9, company_id=999)

        with (
            patch.object(
                order_authorization.OrderFulfillment.objects,
                "filter",
                return_value=_FakeQuerySet([
                    SimpleNamespace(
                        warehouse=foreign,
                    ),
                ]),
            ),
            patch.object(
                order_authorization.PickingOrder.objects,
                "filter",
                return_value=_FakeQuerySet([]),
            ),
        ):
            with self.assertRaises(ValidationError):
                order_authorization.existing_order_authorization_warehouses(
                    order
                )

    def test_existing_scope_never_calls_preview(self):
        order = self._order()
        warehouse = self._warehouse(3)

        with (
            patch.object(
                order_authorization,
                "existing_order_authorization_warehouses",
                return_value=(warehouse,),
            ),
            patch(
                "warehouse.services.fulfillment_planning."
                "preview_fulfillment_plan"
            ) as preview,
        ):
            result = (
                order_authorization.resolve_order_authorization_warehouses(
                    order,
                    plan_if_missing=True,
                )
            )

        self.assertEqual(result, (warehouse,))
        preview.assert_not_called()

    def test_missing_scope_uses_read_only_preview(self):
        order = self._order()
        warehouse_2 = self._warehouse(2)
        warehouse_1 = self._warehouse(1)

        plan = {
            "groups": [
                {"warehouse": warehouse_2},
                {"warehouse": warehouse_1},
            ]
        }

        with (
            patch.object(
                order_authorization,
                "existing_order_authorization_warehouses",
                return_value=(),
            ),
            patch(
                "warehouse.services.fulfillment_planning."
                "preview_fulfillment_plan",
                return_value=plan,
            ) as preview,
        ):
            result = (
                order_authorization.resolve_order_authorization_warehouses(
                    order,
                    plan_if_missing=True,
                )
            )

        preview.assert_called_once_with(order)
        self.assertEqual(
            [warehouse.pk for warehouse in result],
            [1, 2],
        )

    def test_preview_rejects_foreign_company_warehouse(self):
        order = self._order()
        foreign = self._warehouse(4, company_id=999)

        with (
            patch.object(
                order_authorization,
                "existing_order_authorization_warehouses",
                return_value=(),
            ),
            patch(
                "warehouse.services.fulfillment_planning."
                "preview_fulfillment_plan",
                return_value={
                    "groups": [
                        {"warehouse": foreign},
                    ]
                },
            ),
        ):
            with self.assertRaises(ValidationError):
                order_authorization.resolve_order_authorization_warehouses(
                    order
                )

    def test_empty_preview_without_fallback_is_rejected(self):
        order = self._order()

        with (
            patch.object(
                order_authorization,
                "existing_order_authorization_warehouses",
                return_value=(),
            ),
            patch(
                "warehouse.services.fulfillment_planning."
                "preview_fulfillment_plan",
                return_value={"groups": []},
            ),
        ):
            with self.assertRaises(ValidationError):
                order_authorization.resolve_order_authorization_warehouses(
                    order,
                    fallback_to_company=False,
                )

    def test_planning_error_can_fallback_to_all_active_company_warehouses(self):
        order = self._order(company_id=7)
        warehouse_1 = self._warehouse(1)
        warehouse_2 = self._warehouse(2)

        company_qs = _FakeQuerySet([
            warehouse_1,
            warehouse_2,
        ])

        with (
            patch.object(
                order_authorization,
                "existing_order_authorization_warehouses",
                return_value=(),
            ),
            patch(
                "warehouse.services.fulfillment_planning."
                "preview_fulfillment_plan",
                side_effect=ValidationError("Sin stock."),
            ),
            patch.object(
                order_authorization.Warehouse.objects,
                "filter",
                return_value=company_qs,
            ) as warehouse_filter,
        ):
            result = (
                order_authorization.resolve_order_authorization_warehouses(
                    order,
                    plan_if_missing=True,
                    fallback_to_company=True,
                )
            )

        self.assertEqual(result, (warehouse_1, warehouse_2))
        warehouse_filter.assert_called_once_with(
            company_id=7,
            is_active=True,
        )

    def test_fallback_without_active_company_warehouses_is_rejected(self):
        order = self._order()

        with (
            patch.object(
                order_authorization,
                "existing_order_authorization_warehouses",
                return_value=(),
            ),
            patch.object(
                order_authorization.Warehouse.objects,
                "filter",
                return_value=_FakeQuerySet([]),
            ),
        ):
            with self.assertRaises(ValidationError):
                order_authorization.resolve_order_authorization_warehouses(
                    order,
                    plan_if_missing=False,
                    fallback_to_company=True,
                )

    def test_no_plan_and_no_fallback_is_rejected(self):
        order = self._order()

        with patch.object(
            order_authorization,
            "existing_order_authorization_warehouses",
            return_value=(),
        ):
            with self.assertRaises(ValidationError):
                order_authorization.resolve_order_authorization_warehouses(
                    order,
                    plan_if_missing=False,
                    fallback_to_company=False,
                )
