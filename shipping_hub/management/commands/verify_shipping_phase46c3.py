import ast
import inspect
import textwrap

from django.core.management.base import BaseCommand

from shipping_hub.services import purchase_engine


FUNCTIONS = (
    purchase_engine.request_shipping_purchase,
    purchase_engine.process_shipping_purchase,
    purchase_engine._persist_noncompleted_provider_result,
    purchase_engine._mark_error,
    purchase_engine._save_completed_result,
    purchase_engine.mark_expired_purchase_leases_for_reconciliation,
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
                and target.value.id in {"purchase", "shipment", "package"}
            ):
                return True
    return False


class Command(BaseCommand):
    help = "Verifica Purchase Engine bajo Lifecycle sin comprar guías."

    def handle(self, *args, **options):
        combined = "\n".join(inspect.getsource(item) for item in FUNCTIONS)
        assert "transition_purchase(" in combined
        assert "transition_shipment(" in combined
        assert "transition_package(" in combined
        assert all(
            not has_direct_status_assignment(item)
            for item in FUNCTIONS
        )

        self.stdout.write(
            self.style.SUCCESS(
                "FASE 4.6C.3 PURCHASE ENGINE LIFECYCLE VERIFICADA: OK"
            )
        )
        self.stdout.write(
            "El flujo principal de compra ya no escribe estados directamente."
        )
