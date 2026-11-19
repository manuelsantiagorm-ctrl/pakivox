from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase

from warehouse.services.order_navigation import (
    get_order_operational_step,
)


class OrderNavigationSecurityTests(SimpleTestCase):
    def test_rejects_cross_company_order(self):
        order = SimpleNamespace(pk=10, company_id=2)
        company = SimpleNamespace(id=1)

        with self.assertRaises(PermissionDenied):
            get_order_operational_step(
                order=order,
                company=company,
            )

    @patch(
        "warehouse.services.order_navigation."
        "ShippingOrder.objects"
    )
    @patch(
        "warehouse.services.order_navigation."
        "PackingOrder.objects"
    )
    @patch(
        "warehouse.services.order_navigation."
        "PickingOrder.objects"
    )
    @patch(
        "warehouse.services.order_navigation.reverse",
        return_value="/sales/orders/10/",
    )
    def test_falls_back_to_order_detail(
        self,
        mocked_reverse,
        picking_objects,
        packing_objects,
        shipping_objects,
    ):
        for manager in (
            shipping_objects,
            packing_objects,
            picking_objects,
        ):
            (
                manager.filter.return_value
                .exclude.return_value
                .order_by.return_value
                .first.return_value
            ) = None

        order = SimpleNamespace(pk=10, company_id=1)
        company = SimpleNamespace(id=1)

        step = get_order_operational_step(
            order=order,
            company=company,
        )

        self.assertEqual(step.stage, "order")
        self.assertEqual(step.label, "Continuar al pedido")
