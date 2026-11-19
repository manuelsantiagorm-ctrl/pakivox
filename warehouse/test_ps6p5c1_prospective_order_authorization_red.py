from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from warehouse.services import order_authorization
from warehouse.services import fulfillment_planning


RESOLVER_NAME = (
    "resolve_order_mutation_authorization_warehouses"
)


def _warehouse(pk, company_id):
    return SimpleNamespace(
        pk=pk,
        id=pk,
        company_id=company_id,
        is_active=True,
    )


def _variant(pk, company_id, sku):
    return SimpleNamespace(
        pk=pk,
        id=pk,
        company_id=company_id,
        sku=sku,
        product=None,
        is_bundle=False,
    )


def _requirement(
    *,
    item_id,
    variant,
    quantity,
):
    quantity = Decimal(
        str(quantity)
    )

    return {
        "order_item": SimpleNamespace(
            pk=item_id,
            id=item_id,
        ),
        "order_item_id": item_id,
        "variant": variant,
        "variant_id": variant.id,
        "commercial_quantity": quantity,
        "internal_quantity": int(
            quantity
        ),
    }


class PS6P5C1ProspectiveAuthorizationRedTests(
    SimpleTestCase
):

    def setUp(self):
        self.company_id = 100

        self.order = SimpleNamespace(
            pk=500,
            id=500,
            company_id=self.company_id,
        )

        self.wh_a = _warehouse(
            1,
            self.company_id,
        )

        self.wh_b = _warehouse(
            2,
            self.company_id,
        )

        self.variant_a = _variant(
            101,
            self.company_id,
            "SKU-A",
        )

        self.variant_b = _variant(
            102,
            self.company_id,
            "SKU-B",
        )

    def _resolver(self):
        resolver = getattr(
            order_authorization,
            RESOLVER_NAME,
            None,
        )

        self.assertTrue(
            callable(resolver),
            msg=(
                "Falta implementar "
                "resolve_order_mutation_authorization_warehouses."
            ),
        )

        return resolver

    def test_add_unions_current_and_future_warehouses(self):
        resolver = self._resolver()

        base = [
            _requirement(
                item_id=10,
                variant=self.variant_a,
                quantity=1,
            )
        ]

        captured = {}

        def fake_build(
            order,
            requirements,
            *,
            lock_stock=False,
        ):
            captured[
                "requirements"
            ] = requirements

            captured[
                "lock_stock"
            ] = lock_stock

            return {
                "order": order,
                "mode": "multi_warehouse",
                "groups": [
                    {
                        "warehouse": self.wh_a,
                    },
                    {
                        "warehouse": self.wh_b,
                    },
                ],
                "existing": False,
                "created": False,
            }

        with (
            patch.object(
                order_authorization,
                "resolve_order_authorization_warehouses",
                return_value=(
                    self.wh_a,
                ),
            ),
            patch.object(
                fulfillment_planning,
                "_load_requirements",
                return_value=base,
            ),
            patch.object(
                fulfillment_planning,
                "_build_new_plan",
                side_effect=fake_build,
            ),
        ):
            warehouses = resolver(
                self.order,
                mutation="add",
                variant=self.variant_b,
                commercial_quantity=3,
            )

        self.assertEqual(
            {
                warehouse.pk
                for warehouse
                in warehouses
            },
            {
                self.wh_a.pk,
                self.wh_b.pk,
            },
        )

        prospective = (
            captured[
                "requirements"
            ]
        )

        added = [
            requirement
            for requirement
            in prospective
            if requirement[
                "variant_id"
            ] == self.variant_b.id
        ]

        self.assertEqual(
            len(
                added
            ),
            1,
        )

        self.assertEqual(
            added[0][
                "commercial_quantity"
            ],
            Decimal("3"),
        )

        self.assertEqual(
            added[0][
                "internal_quantity"
            ],
            3,
        )

        self.assertFalse(
            captured[
                "lock_stock"
            ]
        )

    def test_update_quantity_can_expand_scope(self):
        resolver = self._resolver()

        base = [
            _requirement(
                item_id=20,
                variant=self.variant_a,
                quantity=2,
            )
        ]

        captured = {}

        def fake_build(
            order,
            requirements,
            *,
            lock_stock=False,
        ):
            captured[
                "requirements"
            ] = requirements

            captured[
                "lock_stock"
            ] = lock_stock

            return {
                "order": order,
                "mode": "multi_warehouse",
                "groups": [
                    {
                        "warehouse": self.wh_a,
                    },
                    {
                        "warehouse": self.wh_b,
                    },
                ],
                "existing": False,
                "created": False,
            }

        with (
            patch.object(
                order_authorization,
                "resolve_order_authorization_warehouses",
                return_value=(
                    self.wh_a,
                ),
            ),
            patch.object(
                fulfillment_planning,
                "_load_requirements",
                return_value=base,
            ),
            patch.object(
                fulfillment_planning,
                "_build_new_plan",
                side_effect=fake_build,
            ),
        ):
            warehouses = resolver(
                self.order,
                mutation="update",
                order_item_id=20,
                commercial_quantity=8,
            )

        self.assertEqual(
            {
                warehouse.pk
                for warehouse
                in warehouses
            },
            {
                self.wh_a.pk,
                self.wh_b.pk,
            },
        )

        requirement = (
            captured[
                "requirements"
            ][0]
        )

        self.assertEqual(
            requirement[
                "order_item_id"
            ],
            20,
        )

        self.assertEqual(
            requirement[
                "commercial_quantity"
            ],
            Decimal("8"),
        )

        self.assertEqual(
            requirement[
                "internal_quantity"
            ],
            8,
        )

        self.assertFalse(
            captured[
                "lock_stock"
            ]
        )

    def test_delete_keeps_current_scope_if_order_becomes_empty(self):
        resolver = self._resolver()

        base = [
            _requirement(
                item_id=30,
                variant=self.variant_a,
                quantity=1,
            )
        ]

        with (
            patch.object(
                order_authorization,
                "resolve_order_authorization_warehouses",
                return_value=(
                    self.wh_a,
                ),
            ),
            patch.object(
                fulfillment_planning,
                "_load_requirements",
                return_value=base,
            ),
            patch.object(
                fulfillment_planning,
                "_build_new_plan",
            ) as build_plan,
        ):
            warehouses = resolver(
                self.order,
                mutation="delete",
                order_item_id=30,
            )

        self.assertEqual(
            tuple(
                warehouse.pk
                for warehouse
                in warehouses
            ),
            (
                self.wh_a.pk,
            ),
        )

        build_plan.assert_not_called()

    def test_cross_company_variant_is_rejected(self):
        resolver = self._resolver()

        foreign_variant = _variant(
            999,
            200,
            "FOREIGN",
        )

        with patch.object(
            order_authorization,
            "resolve_order_authorization_warehouses",
            return_value=(
                self.wh_a,
            ),
        ):
            with self.assertRaises(
                ValidationError
            ):
                resolver(
                    self.order,
                    mutation="add",
                    variant=foreign_variant,
                    commercial_quantity=1,
                )

    def test_unknown_item_is_rejected_for_update(self):
        resolver = self._resolver()

        base = [
            _requirement(
                item_id=40,
                variant=self.variant_a,
                quantity=1,
            )
        ]

        with (
            patch.object(
                order_authorization,
                "resolve_order_authorization_warehouses",
                return_value=(
                    self.wh_a,
                ),
            ),
            patch.object(
                fulfillment_planning,
                "_load_requirements",
                return_value=base,
            ),
        ):
            with self.assertRaises(
                ValidationError
            ):
                resolver(
                    self.order,
                    mutation="update",
                    order_item_id=999,
                    commercial_quantity=4,
                )

    def test_unknown_item_is_rejected_for_delete(self):
        resolver = self._resolver()

        base = [
            _requirement(
                item_id=50,
                variant=self.variant_a,
                quantity=1,
            )
        ]

        with (
            patch.object(
                order_authorization,
                "resolve_order_authorization_warehouses",
                return_value=(
                    self.wh_a,
                ),
            ),
            patch.object(
                fulfillment_planning,
                "_load_requirements",
                return_value=base,
            ),
        ):
            with self.assertRaises(
                ValidationError
            ):
                resolver(
                    self.order,
                    mutation="delete",
                    order_item_id=999,
                )