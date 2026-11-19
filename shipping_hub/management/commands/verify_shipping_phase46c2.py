import ast
import inspect
import textwrap

from django.core.management.base import BaseCommand

from shipping_hub.services import tracking


def has_direct_status_assignment(callable_object, variable_names):
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
                and target.value.id in variable_names
            ):
                return True
    return False


class Command(BaseCommand):
    help = "Verifica Tracking Engine bajo Lifecycle Service."

    def handle(self, *args, **options):
        source = inspect.getsource(tracking.apply_event)
        assert "transition_shipment(" in source
        assert "transition_package(" in source
        assert "source=SOURCE_TRACKING_ENGINE" in source

        assert not has_direct_status_assignment(
            tracking.apply_event,
            {"shipment"},
        )
        assert not has_direct_status_assignment(
            tracking.apply_event,
            {"package"},
        )

        self.stdout.write(
            self.style.SUCCESS(
                "FASE 4.6C.2 TRACKING ENGINE LIFECYCLE VERIFICADA: OK"
            )
        )
        self.stdout.write(
            "Tracking ya no escribe estados de envío o bulto directamente."
        )
