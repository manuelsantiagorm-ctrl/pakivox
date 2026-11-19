import inspect

from django.test import SimpleTestCase

from accounts.today_work import build_today_work_items


class PS6P4CTodayWorkContractTests(SimpleTestCase):
    """
    Tu trabajo de hoy debe respetar las mismas fronteras
    de autoridad que las rutas HTTP.

    No se permite recuperar el acoplamiento legacy:
        Picking -> inventory.view
        Packing -> inventory.view
        Receiving -> inventory:lista_stock
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.source = inspect.getsource(
            build_today_work_items
        )

    def test_receiving_uses_dedicated_receiving_entry(self):
        self.assertIn(
            'reverse("purchases:lista_recepciones")',
            self.source,
        )

        self.assertIn(
            '"receiving.view" in capability_codes',
            self.source,
        )

    def test_receiving_does_not_link_to_inventory_stock(self):
        self.assertNotIn(
            'url=reverse("inventory:lista_stock")',
            self.source,
        )

    def test_picking_requires_picking_view(self):
        self.assertIn(
            '"picking" in activity_codes',
            self.source,
        )

        self.assertIn(
            '"picking.view" in capability_codes',
            self.source,
        )

    def test_picking_does_not_inherit_inventory_view(self):
        legacy = (
            '"picking" in activity_codes\n'
            '        and "inventory.view" in capability_codes'
        )

        self.assertNotIn(
            legacy,
            self.source,
        )

    def test_packing_requires_packing_view(self):
        self.assertIn(
            '"packing" in activity_codes',
            self.source,
        )

        self.assertIn(
            '"packing.view" in capability_codes',
            self.source,
        )

    def test_packing_does_not_inherit_inventory_view(self):
        legacy = (
            '"packing" in activity_codes\n'
            '        and "inventory.view" in capability_codes'
        )

        self.assertNotIn(
            legacy,
            self.source,
        )

    def test_inventory_activity_still_requires_inventory_view(self):
        self.assertIn(
            '"inventory" in activity_codes',
            self.source,
        )

        self.assertIn(
            '"inventory.view" in capability_codes',
            self.source,
        )

    def test_operational_entries_are_independent(self):
        self.assertIn(
            'reverse("warehouse:lista_picking")',
            self.source,
        )

        self.assertIn(
            'reverse("warehouse:lista_packing")',
            self.source,
        )

        self.assertIn(
            'reverse("purchases:lista_recepciones")',
            self.source,
        )
