import ast
import inspect
import textwrap

from django.test import SimpleTestCase

from shipping_hub.services import (
    purchase_reconciliation,
    recovery_engine,
)


def direct_status_assignments(callable_object, variable_names):
    source = textwrap.dedent(inspect.getsource(callable_object))
    tree = ast.parse(source)
    found = []

    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets.extend(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            targets.append(node.target)

        for target in targets:
            if (
                isinstance(target, ast.Attribute)
                and target.attr == "status"
                and isinstance(target.value, ast.Name)
                and target.value.id in variable_names
            ):
                found.append(
                    f"{target.value.id}.status línea {node.lineno}"
                )
    return found


class RecoveryLifecycleWiringTests(SimpleTestCase):
    def test_expired_leases_use_lifecycle(self):
        source = inspect.getsource(
            recovery_engine.release_expired_recovery_leases
        )
        self.assertIn("transition_purchase(", source)
        self.assertFalse(
            direct_status_assignments(
                recovery_engine.release_expired_recovery_leases,
                {"purchase"},
            )
        )

    def test_claim_uses_lifecycle(self):
        source = inspect.getsource(
            recovery_engine.claim_recovery_purchase
        )
        self.assertIn("transition_purchase(", source)
        self.assertFalse(
            direct_status_assignments(
                recovery_engine.claim_recovery_purchase,
                {"purchase"},
            )
        )

    def test_persist_result_uses_purchase_and_shipment_lifecycle(self):
        source = inspect.getsource(
            recovery_engine._persist_recovery_result
        )
        self.assertIn("transition_purchase(", source)
        self.assertIn("transition_shipment(", source)
        self.assertFalse(
            direct_status_assignments(
                recovery_engine._persist_recovery_result,
                {"purchase", "shipment"},
            )
        )


class ReconciliationLifecycleWiringTests(SimpleTestCase):
    def test_reconciliation_uses_all_lifecycles(self):
        source = inspect.getsource(
            purchase_reconciliation.reconcile_carrier_shipment
        )
        self.assertIn("transition_purchase(", source)
        self.assertIn("transition_shipment(", source)
        self.assertIn("transition_package(", source)
        self.assertFalse(
            direct_status_assignments(
                purchase_reconciliation.reconcile_carrier_shipment,
                {"purchase", "locked", "package"},
            )
        )

    def test_reconciliation_filters_purchase_by_company(self):
        source = inspect.getsource(
            purchase_reconciliation.reconcile_carrier_shipment
        )
        self.assertIn("company_id=locked.company_id", source)
        self.assertIn("carrier_shipment_id=locked.pk", source)

    def test_reconciliation_does_not_rebuy(self):
        source = inspect.getsource(
            purchase_reconciliation.reconcile_carrier_shipment
        )
        self.assertNotIn("request_shipping_purchase(", source)
        self.assertNotIn("purchase_label(", source)
