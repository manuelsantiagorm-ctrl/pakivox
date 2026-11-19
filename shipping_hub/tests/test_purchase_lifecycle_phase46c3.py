import ast
import inspect
import textwrap

from django.test import SimpleTestCase

from shipping_hub.services import purchase_engine


TRACKED_VARIABLES = {"purchase", "shipment", "package"}


def direct_status_assignments(callable_object):
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
                and target.value.id in TRACKED_VARIABLES
            ):
                found.append(
                    f"{target.value.id}.status línea {node.lineno}"
                )
    return found


class PurchaseEngineLifecycleWiringTests(SimpleTestCase):
    def test_request_uses_lifecycle(self):
        source = inspect.getsource(
            purchase_engine.request_shipping_purchase
        )
        self.assertIn("transition_purchase(", source)
        self.assertIn("transition_shipment(", source)
        self.assertIn("transition_package(", source)
        self.assertFalse(
            direct_status_assignments(
                purchase_engine.request_shipping_purchase
            )
        )

    def test_worker_claim_uses_purchase_lifecycle(self):
        source = inspect.getsource(
            purchase_engine.process_shipping_purchase
        )
        self.assertIn("transition_purchase(", source)
        self.assertIn("to_state=ShippingPurchase.STATUS_SENDING", source)
        self.assertFalse(
            direct_status_assignments(
                purchase_engine.process_shipping_purchase
            )
        )

    def test_completed_result_uses_all_lifecycles(self):
        source = inspect.getsource(
            purchase_engine._save_completed_result
        )
        self.assertIn("transition_purchase(", source)
        self.assertIn("transition_shipment(", source)
        self.assertIn("transition_package(", source)
        self.assertFalse(
            direct_status_assignments(
                purchase_engine._save_completed_result
            )
        )

    def test_errors_use_lifecycle(self):
        source = inspect.getsource(purchase_engine._mark_error)
        self.assertIn("transition_purchase(", source)
        self.assertIn("transition_shipment(", source)
        self.assertIn("transition_package(", source)
        self.assertFalse(
            direct_status_assignments(purchase_engine._mark_error)
        )

    def test_provider_noncompleted_uses_lifecycle(self):
        source = inspect.getsource(
            purchase_engine._persist_noncompleted_provider_result
        )
        self.assertIn("transition_purchase(", source)
        self.assertIn("transition_shipment(", source)
        self.assertFalse(
            direct_status_assignments(
                purchase_engine._persist_noncompleted_provider_result
            )
        )

    def test_expired_lease_uses_all_lifecycles(self):
        source = inspect.getsource(
            purchase_engine.mark_expired_purchase_leases_for_reconciliation
        )
        self.assertIn("transition_purchase(", source)
        self.assertIn("transition_shipment(", source)
        self.assertIn("transition_package(", source)
        self.assertFalse(
            direct_status_assignments(
                purchase_engine.mark_expired_purchase_leases_for_reconciliation
            )
        )
