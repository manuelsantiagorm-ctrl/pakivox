from collections import Counter

from django.test import SimpleTestCase, TestCase
from django.urls import URLPattern

from accounts.models import Capability
from accounts.simple_activity_contract import (
    ACTIVITY_CONTRACT_BY_CODE,
    AUTHORITY_LEVELS,
    OUT_OF_ACTIVITY_SCOPE_CAPABILITIES,
    capabilities_for_activity_level,
)
from accounts.simple_activity_route_contract import (
    CLASS_ACTIVITY,
    CLASS_EXTERNAL,
    CLASS_PRIVILEGED,
    KNOWN_ENFORCEMENT_GAPS,
    KNOWN_GAP_BY_CODE,
    ROUTE_ACTION_CONTRACTS,
    ROUTE_CONTRACT_BY_KEY,
    ROUTE_SOURCES,
    SCOPE_COMPANY,
    SCOPE_EXTERNAL,
    SCOPE_MULTIWAREHOUSE,
    SCOPE_TRANSFER,
    SCOPE_WAREHOUSE,
    route_action_contract,
)


EXPECTED_COUNTS = {
    "sales": 29,  # +3 avisos al cliente sin API de WhatsApp (nov 2026)
    "payments": 13,
    "purchases": 22,
    "inventory": 24,
    "warehouse": 52,
    "shipping_hub": 61,
    "shipping_dispatch": 4,
    "accounting": 9,
    "crm": 24,  # +customer_create (alta de cliente desde el CRM, oct 2026)
}

EXPECTED_GAP_CODES = {
    "sales_capability_without_authority_level",
    "payments_capability_without_authority_level",
    "crm_has_no_read_only_capability",
    "inventory_operate_approve_capability_gap",
    "warehouse_order_timeline_double_gate",
    "shipping_asset_routes_missing_specific_warehouse_gate",
    "legacy_role_checks_coexist_with_inventory_capabilities",
}


def _direct_named_patterns(module):
    return [
        pattern
        for pattern in module.urlpatterns
        if isinstance(pattern, URLPattern) and pattern.name
    ]


def _source_names():
    from accounting import urls as accounting_urls
    from inventory import urls as inventory_urls
    from payments import urls as payments_urls
    from purchases import urls as purchases_urls
    from sales import urls as sales_urls
    from shipping_hub import dispatch_urls as dispatch_urls
    from shipping_hub import urls as shipping_urls
    from warehouse import urls as warehouse_urls
    from config import urls as config_urls

    result = {
        "sales": {item.name for item in _direct_named_patterns(sales_urls)},
        "payments": {item.name for item in _direct_named_patterns(payments_urls)},
        "purchases": {item.name for item in _direct_named_patterns(purchases_urls)},
        "inventory": {item.name for item in _direct_named_patterns(inventory_urls)},
        "warehouse": {item.name for item in _direct_named_patterns(warehouse_urls)},
        "shipping_hub": {item.name for item in _direct_named_patterns(shipping_urls)},
        "shipping_dispatch": {item.name for item in _direct_named_patterns(dispatch_urls)},
        "accounting": {item.name for item in _direct_named_patterns(accounting_urls)},
    }

    result["crm"] = {
        pattern.name
        for pattern in _direct_named_patterns(config_urls)
        if str(pattern.pattern).startswith("crm/")
    }

    return result


class PS2BRouteActionCoverageStaticTests(SimpleTestCase):
    def test_route_contract_has_exact_total_size(self):
        self.assertEqual(len(ROUTE_ACTION_CONTRACTS), 238)  # +3 avisos al cliente (nov 2026)
        self.assertEqual(len(ROUTE_CONTRACT_BY_KEY), 238)

    def test_source_counts_match_audited_route_inventory(self):
        current = _source_names()
        self.assertEqual(set(current), set(EXPECTED_COUNTS))
        for source, expected in EXPECTED_COUNTS.items():
            self.assertEqual(len(current[source]), expected, msg=source)

    def test_every_current_target_route_is_classified(self):
        current = _source_names()
        for source in ROUTE_SOURCES:
            contract_names = {
                item.route_name
                for item in ROUTE_ACTION_CONTRACTS
                if item.source == source
            }
            self.assertEqual(contract_names, current[source], msg=source)

    def test_contract_keys_are_unique(self):
        keys = [item.key for item in ROUTE_ACTION_CONTRACTS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_sales_duplicate_route_name_is_known_and_harmless_for_contract_key(self):
        from sales import urls as sales_urls

        names = [item.name for item in _direct_named_patterns(sales_urls)]
        duplicates = {name: count for name, count in Counter(names).items() if count > 1}
        self.assertEqual(duplicates, {"crear_pedido": 2})
        self.assertIn("sales:crear_pedido", ROUTE_CONTRACT_BY_KEY)

    def test_activity_routes_use_known_activity_and_authority_level(self):
        for item in ROUTE_ACTION_CONTRACTS:
            if item.classification != CLASS_ACTIVITY:
                continue
            self.assertIn(item.activity_code, ACTIVITY_CONTRACT_BY_CODE)
            self.assertIn(item.minimum_level, AUTHORITY_LEVELS)

    def test_activity_route_capability_is_allowed_or_explicitly_route_derived(self):
        for item in ROUTE_ACTION_CONTRACTS:
            if item.classification != CLASS_ACTIVITY:
                continue

            allowed_at_level = set(
                capabilities_for_activity_level(
                    item.activity_code,
                    item.minimum_level,
                )
            )

            if item.capability_code in allowed_at_level:
                self.assertFalse(
                    item.route_level_derivation,
                    msg=(
                        f"{item.key} marca derivación de ruta aunque la "
                        "capability ya existe en el nivel mínimo."
                    ),
                )
                continue

            self.assertTrue(
                item.route_level_derivation,
                msg=item.key,
            )

            allowed_at_manage = set(
                capabilities_for_activity_level(
                    item.activity_code,
                    "manage",
                )
            )

            self.assertIn(
                item.capability_code,
                allowed_at_manage,
                msg=item.key,
            )

            self.assertNotIn(
                item.capability_code,
                OUT_OF_ACTIVITY_SCOPE_CAPABILITIES,
                msg=item.key,
            )

    def test_no_activity_route_auto_uses_out_of_activity_capability(self):
        for item in ROUTE_ACTION_CONTRACTS:
            if item.classification != CLASS_ACTIVITY:
                continue
            self.assertNotIn(
                item.capability_code,
                OUT_OF_ACTIVITY_SCOPE_CAPABILITIES,
                msg=item.key,
            )

    def test_privileged_routes_are_outside_human_activity_matrix(self):
        privileged = [
            item
            for item in ROUTE_ACTION_CONTRACTS
            if item.classification == CLASS_PRIVILEGED
        ]
        self.assertEqual(len(privileged), 10)
        self.assertEqual({item.capability_code for item in privileged}, {"shipping.configure"})
        for item in privileged:
            self.assertIsNone(item.activity_code)
            self.assertIsNone(item.minimum_level)
            self.assertIn(item.capability_code, OUT_OF_ACTIVITY_SCOPE_CAPABILITIES)

    def test_external_routes_are_not_human_permissions(self):
        external = [
            item
            for item in ROUTE_ACTION_CONTRACTS
            if item.classification == CLASS_EXTERNAL
        ]
        self.assertEqual(
            {item.key for item in external},
            {
                "shipping_hub:tracking_webhook",
                "shipping_hub:provider_event_webhook",
                "shipping_hub:skydropx_event_webhook",
                "shipping_hub:envia_event_webhook",
            },
        )
        for item in external:
            self.assertIsNone(item.activity_code)
            self.assertIsNone(item.minimum_level)
            self.assertIsNone(item.capability_code)
            self.assertEqual(item.scope, SCOPE_EXTERNAL)

    def test_scope_values_are_closed_and_explicit(self):
        allowed = {
            SCOPE_COMPANY,
            SCOPE_WAREHOUSE,
            SCOPE_MULTIWAREHOUSE,
            SCOPE_TRANSFER,
            SCOPE_EXTERNAL,
        }
        self.assertTrue({item.scope for item in ROUTE_ACTION_CONTRACTS} <= allowed)

    def test_sales_destructive_routes_have_target_supervise_or_manage_levels(self):
        expected = {
            "cancelar_pedido": "approve",
            "recuperar_pedido_error_humano": "approve",
            "cancelar_pedido_comercialmente": "manage",
        }
        for name, level in expected.items():
            self.assertEqual(route_action_contract("sales", name).minimum_level, level)

    def test_customer_payment_proof_is_read_only_collection_route(self):
        item = route_action_contract(
            "payments",
            "ver_comprobante_pago",
        )
        self.assertEqual(item.activity_code, "collections")
        self.assertEqual(item.minimum_level, "view")
        self.assertEqual(item.capability_code, "payments.proof.view")
        self.assertEqual(item.scope, SCOPE_MULTIWAREHOUSE)

    def test_collections_financial_reversal_routes_are_manage(self):
        for name in (
            "reembolsar_pago",
            "confirmar_reembolso_pago",
            "convertir_reembolso_saldo_favor",
            "aplicar_saldo_favor_pedido",
        ):
            self.assertEqual(route_action_contract("payments", name).minimum_level, "manage")

    def test_receiving_confirm_and_cancel_are_separated(self):
        self.assertEqual(
            route_action_contract("purchases", "confirmar_recepcion").minimum_level,
            "approve",
        )
        self.assertEqual(
            route_action_contract("purchases", "cancelar_recepcion").minimum_level,
            "manage",
        )

    def test_supplier_payment_cancel_is_manage_target(self):
        item = route_action_contract("purchases", "cancelar_pago_proveedor")
        self.assertEqual(item.activity_code, "accounting")
        self.assertEqual(item.minimum_level, "manage")
        self.assertEqual(item.capability_code, "supplier_payments.cancel")

    def test_picking_read_and_work_routes_are_distinct(self):
        for name in (
            "pendientes_por_enviar",
            "lista_picking",
            "detalle_picking",
        ):
            item = route_action_contract(
                "warehouse",
                name,
            )

            self.assertEqual(
                item.activity_code,
                "picking",
            )

            self.assertEqual(
                item.minimum_level,
                "view",
            )

            self.assertEqual(
                item.capability_code,
                "picking.view",
            )

        self.assertEqual(
            route_action_contract(
                "warehouse",
                "confirmar_item_picking",
            ).minimum_level,
            "operate",
        )

        self.assertEqual(
            route_action_contract(
                "warehouse",
                "cancelar_picking",
            ).minimum_level,
            "approve",
        )

    def test_packing_read_and_work_routes_are_distinct(self):
        for name in (
            "lista_packing",
            "detalle_packing",
        ):
            item = route_action_contract(
                "warehouse",
                name,
            )

            self.assertEqual(
                item.activity_code,
                "packing",
            )

            self.assertEqual(
                item.minimum_level,
                "view",
            )

            self.assertEqual(
                item.capability_code,
                "packing.view",
            )

        for name in (
            "generar_packing",
            "completar_packing",
        ):
            self.assertEqual(
                route_action_contract(
                    "warehouse",
                    name,
                ).minimum_level,
                "operate",
            )

    def test_shipping_read_operate_approve_routes_are_distinct(self):
        self.assertEqual(route_action_contract("warehouse", "lista_shipping").minimum_level, "view")
        self.assertEqual(route_action_contract("warehouse", "marcar_enviado").minimum_level, "operate")
        self.assertEqual(route_action_contract("warehouse", "marcar_entregado").minimum_level, "approve")
        self.assertEqual(route_action_contract("shipping_dispatch", "mark_delivered").minimum_level, "approve")

    def test_shipping_configuration_never_enters_shipping_activity(self):
        for name in (
            "integrations_list",
            "integration_create",
            "integration_edit",
            "integration_test",
            "integration_toggle",
            "origins_list",
            "origin_create",
            "origin_edit",
            "custom_carriers",
            "local_delivery_resources",
        ):
            self.assertEqual(
                route_action_contract("shipping_hub", name).classification,
                CLASS_PRIVILEGED,
            )

    def test_accounting_mutations_start_at_operate(self):
        for name in ("reconcile_balance", "expense_create", "expense_cancel"):
            item = route_action_contract("accounting", name)
            self.assertEqual(item.activity_code, "accounting")
            self.assertEqual(item.minimum_level, "operate")
            self.assertEqual(item.capability_code, "accounting.manage")

    def test_crm_routes_are_explicitly_operate_until_read_capability_exists(self):
        crm = [item for item in ROUTE_ACTION_CONTRACTS if item.source == "crm"]
        self.assertEqual(len(crm), 24)
        self.assertEqual({item.activity_code for item in crm}, {"sales_customers"})
        self.assertEqual({item.minimum_level for item in crm}, {"operate"})
        self.assertEqual({item.capability_code for item in crm}, {"crm.manage"})

    def test_known_enforcement_gap_codes_are_exact_and_unique(self):
        codes = [gap.code for gap in KNOWN_ENFORCEMENT_GAPS]
        self.assertEqual(set(codes), EXPECTED_GAP_CODES)
        self.assertEqual(len(codes), len(set(codes)))
        self.assertEqual(set(KNOWN_GAP_BY_CODE), EXPECTED_GAP_CODES)

    def test_every_known_gap_references_classified_routes(self):
        for gap in KNOWN_ENFORCEMENT_GAPS:
            self.assertTrue(gap.route_keys, msg=gap.code)
            for key in gap.route_keys:
                self.assertIn(key, ROUTE_CONTRACT_BY_KEY, msg=gap.code)
            self.assertTrue(gap.description.strip())
            self.assertTrue(gap.planned_fix.startswith("PS-3") or "PS-3" in gap.planned_fix)

    def test_route_lookup_rejects_unclassified_route(self):
        with self.assertRaises(ValueError):
            route_action_contract("sales", "not-real")


class PS2BRouteActionCoverageDatabaseTests(TestCase):
    def test_every_contract_capability_exists_and_is_active(self):
        required = {
            item.capability_code
            for item in ROUTE_ACTION_CONTRACTS
            if item.capability_code
        }
        active = set(
            Capability.objects.filter(
                code__in=required,
                is_active=True,
            ).values_list("code", flat=True)
        )
        self.assertEqual(active, required)
