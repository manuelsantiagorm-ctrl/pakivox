from django.core.management.base import BaseCommand, CommandError

from shipping_hub.services.lifecycle import (
    TRANSITION_MAPS,
    decide_transition,
)


class Command(BaseCommand):
    help = "Consulta una transición del Lifecycle Engine sin modificar datos."

    def add_arguments(self, parser):
        parser.add_argument(
            "--entity",
            required=True,
            choices=sorted(TRANSITION_MAPS.keys()),
        )
        parser.add_argument("--from-state", required=True)
        parser.add_argument("--to-state", required=True)

    def handle(self, *args, **options):
        try:
            decision = decide_transition(
                entity=options["entity"],
                from_state=options["from_state"],
                to_state=options["to_state"],
            )
        except Exception as error:
            raise CommandError(str(error)) from error

        style = (
            self.style.SUCCESS
            if decision.allowed
            else self.style.ERROR
        )
        self.stdout.write(
            style(
                f"{decision.entity}: {decision.from_state} -> "
                f"{decision.to_state} | "
                f"{'PERMITIDA' if decision.allowed else 'BLOQUEADA'}"
            )
        )
        self.stdout.write(
            f"idempotente={decision.idempotent}, "
            f"origen_terminal={decision.terminal_source}"
        )
        self.stdout.write(decision.reason)
