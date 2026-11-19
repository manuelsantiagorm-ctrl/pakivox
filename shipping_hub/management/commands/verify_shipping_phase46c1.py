import ast
import inspect

from django.core.management.base import BaseCommand

from shipping_hub.management.commands import replay_shipping_provider_event
from shipping_hub.services import provider_event_engine


def has_direct_status_assignment(callable_object, variable_names):
    tree = ast.parse(inspect.getsource(callable_object))
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            if isinstance(node, ast.Assign):
                targets.extend(node.targets)
            else:
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
    help = "Verifica la migración Event Engine -> Lifecycle Service."

    def handle(self, *args, **options):
        functions = (
            provider_event_engine.claim_event,
            provider_event_engine._finish_event,
            provider_event_engine.release_expired_event_leases,
            provider_event_engine.process_provider_event,
        )
        combined = "\n".join(inspect.getsource(item) for item in functions)

        assert "transition_provider_event(" in combined
        assert not has_direct_status_assignment(
            provider_event_engine.claim_event,
            {"event"},
        )
        assert not has_direct_status_assignment(
            provider_event_engine._finish_event,
            {"event"},
        )
        assert not has_direct_status_assignment(
            provider_event_engine.release_expired_event_leases,
            {"event"},
        )
        assert not has_direct_status_assignment(
            provider_event_engine.process_provider_event,
            {"locked"},
        )

        replay_source = inspect.getsource(
            replay_shipping_provider_event.Command.handle
        )
        assert "transition_provider_event(" in replay_source
        assert "source=SOURCE_MANUAL_REPLAY" in replay_source
        assert "allow_terminal_reopen=True" in replay_source
        assert not has_direct_status_assignment(
            replay_shipping_provider_event.Command.handle,
            {"locked"},
        )

        self.stdout.write(
            self.style.SUCCESS(
                "FASE 4.6C.1 EVENT ENGINE LIFECYCLE VERIFICADA: OK"
            )
        )
        self.stdout.write(
            "Event Engine y replay ya no escriben estados directamente."
        )
