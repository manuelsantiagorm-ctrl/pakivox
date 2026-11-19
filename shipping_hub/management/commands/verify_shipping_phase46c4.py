import ast
import inspect
import textwrap

from django.core.management.base import BaseCommand

from shipping_hub.services import (
    purchase_reconciliation,
    recovery_engine,
)


FUNCTIONS = (
    recovery_engine.release_expired_recovery_leases,
    recovery_engine.claim_recovery_purchase,
    recovery_engine._persist_recovery_result,
    purchase_reconciliation.reconcile_carrier_shipment,
)


def has_direct_status_assignment(callable_object):
    source = textwrap.dedent(inspect.getsource(callable_object))
    tree = ast.parse(source)

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
                and target.value.id
                in {"purchase", "shipment", "locked", "package"}
            ):
                return True
    return False


class Command(BaseCommand):
    help = (
        "Verifica Recovery y conciliación bajo Lifecycle "
        "sin llamar proveedores."
    )

    def handle(self, *args, **options):
        combined = "\n".join(
            inspect.getsource(item)
            for item in FUNCTIONS
        )
        assert "transition_purchase(" in combined
        assert "transition_shipment(" in combined
        assert "transition_package(" in combined
        assert all(
            not has_direct_status_assignment(item)
            for item in FUNCTIONS
        )

        self.stdout.write(
            self.style.SUCCESS(
                "FASE 4.6C.4 RECOVERY Y CONCILIACIÓN "
                "LIFECYCLE VERIFICADA: OK"
            )
        )
        self.stdout.write(
            "Recovery y conciliación ya no escriben estados directamente."
        )
