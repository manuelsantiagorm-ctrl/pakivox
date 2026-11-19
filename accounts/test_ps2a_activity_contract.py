from django.template.loader import get_template
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from accounts.models import Capability
from accounts.simple_activity_contract import (
    ACTIVITY_ACCESS_CONTRACTS,
    ACTIVITY_CONTRACT_BY_CODE,
    AUTHORITY_LEVELS,
    OUT_OF_ACTIVITY_SCOPE_CAPABILITIES,
    activity_contract,
    capabilities_for_activity_level,
    entry_view_for_activity,
)
from accounts.simple_employee_access import (
    ACTIVITIES,
    SENSITIVE_BASE_CAPABILITIES,
)
from accounts.warehouse_access import (
    ACCESS_APPROVE,
    ACCESS_MANAGE,
    ACCESS_OPERATE,
    ACCESS_VIEW,
)


EXPECTED_ACTIVITY_CODES = (
    "sales_customers",
    "collections",
    "purchasing",
    "receiving",
    "inventory",
    "picking",
    "packing",
    "shipping",
    "accounting",
)

EXPECTED_ENTRY_PATHS = {
    "sales_customers": "/sales/orders/",
    "collections": "/pagos/",
    "purchasing": "/compras/",
    "receiving": "/compras/recepciones/",
    "inventory": "/inventory/stock/",
    "picking": "/warehouse/picking/",
    "packing": "/warehouse/packing/",
    "shipping": "/warehouse/shipping/",
    "accounting": "/finanzas/",
}


class PS2AActivityContractStaticTests(SimpleTestCase):
    def test_contract_has_exactly_the_nine_human_activities(self):
        self.assertEqual(
            tuple(item.code for item in ACTIVITY_ACCESS_CONTRACTS),
            EXPECTED_ACTIVITY_CODES,
        )
        self.assertEqual(
            set(ACTIVITY_CONTRACT_BY_CODE),
            set(EXPECTED_ACTIVITY_CODES),
        )

    def test_contract_labels_match_ps1_catalog(self):
        ps1_labels = {item.code: item.label for item in ACTIVITIES}
        contract_labels = {
            item.code: item.label
            for item in ACTIVITY_ACCESS_CONTRACTS
        }
        self.assertEqual(contract_labels, ps1_labels)

    def test_authority_levels_are_exact_and_ordered(self):
        self.assertEqual(
            AUTHORITY_LEVELS,
            (
                ACCESS_VIEW,
                ACCESS_OPERATE,
                ACCESS_APPROVE,
                ACCESS_MANAGE,
            ),
        )

    def test_contract_capabilities_are_monotonic_by_authority(self):
        for item in ACTIVITY_ACCESS_CONTRACTS:
            previous = set()
            for level in AUTHORITY_LEVELS:
                current = set(item.capabilities_through(level))
                self.assertTrue(
                    previous <= current,
                    msg=f"{item.code} deja de ser acumulativo en {level}",
                )
                previous = current

    def test_ps1_baseline_matches_declared_baseline_level(self):
        ps1 = {item.code: set(item.capability_codes) for item in ACTIVITIES}
        for item in ACTIVITY_ACCESS_CONTRACTS:
            self.assertEqual(
                set(item.capabilities_through(item.baseline_level)),
                ps1[item.code],
                msg=f"Baseline diferente para {item.code}",
            )

    def test_baseline_levels_document_current_ps1_behavior(self):
        levels = {
            item.code: item.baseline_level
            for item in ACTIVITY_ACCESS_CONTRACTS
        }
        self.assertEqual(levels["inventory"], ACCESS_MANAGE)
        self.assertEqual(levels["collections"], ACCESS_MANAGE)
        self.assertEqual(levels["receiving"], ACCESS_MANAGE)
        self.assertEqual(levels["accounting"], ACCESS_MANAGE)
        self.assertEqual(levels["purchasing"], ACCESS_MANAGE)
        self.assertEqual(levels["sales_customers"], ACCESS_MANAGE)
        for code in set(EXPECTED_ACTIVITY_CODES) - {
            "inventory",
            "collections",
            "receiving",
            "accounting",
            "purchasing",
            "sales_customers",
        }:
            self.assertEqual(levels[code], ACCESS_OPERATE)

    def test_company_admin_capabilities_never_enter_activity_manage_level(self):
        for item in ACTIVITY_ACCESS_CONTRACTS:
            granted = set(item.capabilities_through(ACCESS_MANAGE))
            self.assertFalse(
                granted & OUT_OF_ACTIVITY_SCOPE_CAPABILITIES,
                msg=f"{item.code} eleva permisos de administración de empresa",
            )

    def test_entry_routes_reverse_to_real_current_paths(self):
        for code, expected_path in EXPECTED_ENTRY_PATHS.items():
            view_name = entry_view_for_activity(code)
            self.assertEqual(reverse(view_name), expected_path)

    def test_receiving_has_dedicated_entry_and_no_inventory_authority(self):
        item = activity_contract("receiving")

        self.assertEqual(
            item.entry_view_name,
            "purchases:lista_recepciones",
        )

        baseline = set(
            item.capabilities_through(
                item.baseline_level
            )
        )

        self.assertIn(
            "receiving.view",
            baseline,
        )

        self.assertIn(
            "receiving.execute",
            baseline,
        )

        self.assertNotIn(
            "inventory.view",
            baseline,
        )

    def test_invalid_activity_or_level_is_rejected(self):
        with self.assertRaises(ValueError):
            activity_contract("not-real")
        with self.assertRaises(ValueError):
            capabilities_for_activity_level("sales_customers", "super-admin")

    def test_employee_home_uses_activity_named_routes_not_legacy_hardcoded_urls(self):
        for code in EXPECTED_ACTIVITY_CODES:
            view_name = entry_view_for_activity(code)
            self.assertIn(":", view_name)
            self.assertTrue(reverse(view_name).startswith("/"))

        source = get_template("accounts/employee_home.html").template.source
        self.assertIn("simple_nav_items", source)
        self.assertIn("item.url", source)
        self.assertNotIn("allowed_areas", source)

    def test_employee_home_purchasing_uses_ps2_activity_contract(self):
        purchasing = activity_contract("purchasing")

        self.assertEqual(
            purchasing.entry_view_name,
            "purchases:lista_compras",
        )
        self.assertIn(
            "purchases.view",
            purchasing.capabilities_through(
                purchasing.baseline_level
            ),
        )
        self.assertIn(
            "suppliers.view",
            purchasing.capabilities_through(
                purchasing.baseline_level
            ),
        )
        self.assertIn(
            "suppliers.manage",
            purchasing.capabilities_through(
                purchasing.baseline_level
            ),
        )

    def test_base_menu_exposes_purchases_from_purchases_view(self):
        source = get_template("base.html").template.source
        self.assertIn(
            '"purchases.view" in user_capability_codes',
            source,
        )
        self.assertIn("purchases:lista_compras", source)
        self.assertIn("purchases:lista_proveedores", source)
        self.assertIn("Proveedores", source)

    def test_activity_contract_named_routes_replace_old_broken_home_paths(self):
        from accounts import simple_activity_contract

        source = open(
            simple_activity_contract.__file__,
            encoding="utf-8",
        ).read()

        self.assertNotIn('"/accounting/"', source)
        self.assertNotIn('"/payments/lista_pagos/"', source)
        self.assertNotIn('"/sales/lista_pedidos"', source)

        self.assertIn(
            '"accounting:financial_dashboard"',
            source,
        )
        self.assertIn(
            '"payments:lista_pagos"',
            source,
        )
        self.assertIn(
            '"sales:lista_pedidos"',
            source,
        )

        self.assertEqual(
            entry_view_for_activity("accounting"),
            "accounting:financial_dashboard",
        )
        self.assertEqual(
            entry_view_for_activity("collections"),
            "payments:lista_pagos",
        )
        self.assertEqual(
            entry_view_for_activity("sales_customers"),
            "sales:lista_pedidos",
        )


class PS2AActivityContractDatabaseTests(TestCase):
    def test_every_contract_capability_exists_and_is_active(self):
        required = {
            code
            for item in ACTIVITY_ACCESS_CONTRACTS
            for code in item.capabilities_through(ACCESS_MANAGE)
        }
        active = set(
            Capability.objects.filter(
                code__in=required,
                is_active=True,
            ).values_list("code", flat=True)
        )
        self.assertEqual(active, required)

    def test_sensitive_baseline_catalog_uses_real_capability_codes(self):
        active = set(
            Capability.objects.filter(
                code__in=SENSITIVE_BASE_CAPABILITIES,
                is_active=True,
            ).values_list("code", flat=True)
        )
        self.assertEqual(active, set(SENSITIVE_BASE_CAPABILITIES))
        self.assertNotIn("sales.cancel_order", SENSITIVE_BASE_CAPABILITIES)
        self.assertNotIn("sales.recover_order", SENSITIVE_BASE_CAPABILITIES)
        self.assertNotIn("sales.cancel", SENSITIVE_BASE_CAPABILITIES)
        self.assertNotIn("sales.recover", SENSITIVE_BASE_CAPABILITIES)
