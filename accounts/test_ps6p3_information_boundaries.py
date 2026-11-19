from django.test import SimpleTestCase

from accounts.simple_activity_contract import ACTIVITY_ACCESS_CONTRACTS


class PS6P3InformationBoundaryContractTests(SimpleTestCase):
    """
    RED contract.

    An operational activity must not inherit a whole business domain
    merely because it needs a few pieces of information from that domain.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        cls.contracts = {
            contract.code: contract
            for contract in ACTIVITY_ACCESS_CONTRACTS
        }

    def capabilities_for(self, activity_code):
        contract = self.contracts[activity_code]

        result = set()

        for _level, capabilities in contract.capability_steps:
            result.update(capabilities)

        return result

    def test_picking_has_its_own_read_capability(self):
        capabilities = self.capabilities_for("picking")

        self.assertIn(
            "picking.view",
            capabilities,
        )

        self.assertIn(
            "picking.execute",
            capabilities,
        )

        self.assertNotIn(
            "inventory.view",
            capabilities,
        )

    def test_packing_has_its_own_read_capability(self):
        capabilities = self.capabilities_for("packing")

        self.assertIn(
            "packing.view",
            capabilities,
        )

        self.assertIn(
            "packing.execute",
            capabilities,
        )

        self.assertNotIn(
            "inventory.view",
            capabilities,
        )

    def test_receiving_does_not_require_general_inventory_read(self):
        capabilities = self.capabilities_for("receiving")

        self.assertIn(
            "receiving.view",
            capabilities,
        )

        self.assertIn(
            "receiving.execute",
            capabilities,
        )

        self.assertNotIn(
            "inventory.view",
            capabilities,
        )


    def test_receiving_has_dedicated_entry_route(self):
        contract = self.contracts["receiving"]

        self.assertEqual(
            contract.entry_view_name,
            "purchases:lista_recepciones",
        )

        self.assertNotEqual(
            contract.entry_view_name,
            "inventory:lista_stock",
        )

    def test_inventory_keeps_inventory_read_authority(self):
        capabilities = self.capabilities_for("inventory")

        self.assertIn(
            "inventory.view",
            capabilities,
        )

    def test_sales_activity_does_not_inherit_finance_or_warehouse(self):
        capabilities = self.capabilities_for("sales_customers")

        forbidden_prefixes = (
            "payments.",
            "inventory.",
            "picking.",
            "packing.",
            "shipping.",
            "receiving.",
            "supplier_payments.",
        )

        for capability in capabilities:
            self.assertFalse(
                capability.startswith(forbidden_prefixes),
                capability,
            )

        self.assertNotIn(
            "finance.costs.view",
            capabilities,
        )

    def test_collections_activity_does_not_inherit_warehouse_domains(self):
        capabilities = self.capabilities_for("collections")

        forbidden_prefixes = (
            "inventory.",
            "picking.",
            "packing.",
            "shipping.",
            "receiving.",
            "purchases.",
            "supplier_payments.",
        )

        for capability in capabilities:
            self.assertFalse(
                capability.startswith(forbidden_prefixes),
                capability,
            )

    def test_shipping_is_not_inventory_authority(self):
        capabilities = self.capabilities_for("shipping")

        self.assertIn(
            "shipping.view",
            capabilities,
        )

        self.assertNotIn(
            "inventory.view",
            capabilities,
        )