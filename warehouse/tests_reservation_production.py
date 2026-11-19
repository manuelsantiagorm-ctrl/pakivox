from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from warehouse.services.fulfillment_planning import (
    _split_requirements_multibodega,
)


class ProductionReservationPlannerTests(SimpleTestCase):
    def _requirement(self, internal=10, commercial=Decimal("10")):
        return {
            "order_item_id": 40,
            "variant_id": 6,
            "variant": SimpleNamespace(id=6, sku="1111"),
            "order_item": SimpleNamespace(id=40),
            "internal_quantity": internal,
            "commercial_quantity": commercial,
        }

    def test_splits_one_line_across_two_warehouses(self):
        warehouses = {
            3: SimpleNamespace(id=3, name="Principal"),
            4: SimpleNamespace(id=4, name="Sur"),
        }
        groups = _split_requirements_multibodega(
            [self._requirement()], warehouses, {(3, 6): 5, (4, 6): 7}
        )
        self.assertEqual([g["warehouse_id"] for g in groups], [3, 4])
        self.assertEqual(
            sum(line["internal_quantity"] for g in groups for line in g["lines"]),
            10,
        )

    def test_preserves_total_commercial_quantity_when_split(self):
        warehouses = {
            3: SimpleNamespace(id=3, name="Principal"),
            4: SimpleNamespace(id=4, name="Sur"),
        }
        groups = _split_requirements_multibodega(
            [self._requirement(12, Decimal("1"))],
            warehouses,
            {(3, 6): 5, (4, 6): 7},
        )
        self.assertEqual(
            sum(line["commercial_quantity"] for g in groups for line in g["lines"]),
            Decimal("1"),
        )

    def test_reports_exact_pickable_shortage(self):
        warehouses = {3: SimpleNamespace(id=3, name="Principal")}
        with self.assertRaisesMessage(ValidationError, "faltante: 5"):
            _split_requirements_multibodega(
                [self._requirement()], warehouses, {(3, 6): 5}
            )

    def test_never_uses_availability_from_another_variant(self):
        warehouses = {3: SimpleNamespace(id=3, name="Principal")}
        with self.assertRaises(ValidationError):
            _split_requirements_multibodega(
                [self._requirement()], warehouses, {(3, 999): 1000}
            )
