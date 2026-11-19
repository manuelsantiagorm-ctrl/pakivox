from django.core.management.base import BaseCommand
from django.db import transaction

from subscriptions.models import Plan
from subscriptions.plan_catalog import PLAN_CATALOG


class Command(BaseCommand):
    help = (
        "Crea o actualiza los planes oficiales de PAKIVOX "
        "(Trial, Esencial, Profesional y Maximo)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Muestra que cambiaria en cada plan, sin escribir nada.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        with transaction.atomic():
            for entry in PLAN_CATALOG:
                self._apply(entry, dry_run=dry_run)

            if dry_run:
                # Nada de lo anterior escribio; la reversa es solo por seguridad.
                transaction.set_rollback(True)

        if dry_run:
            self.stdout.write(self.style.WARNING("Simulacion: no se escribio nada."))
        else:
            self.stdout.write(
                self.style.SUCCESS("Planes oficiales creados/actualizados.")
            )

    def _apply(self, entry, *, dry_run):
        slug = entry["slug"]
        synced = entry["synced"]
        create_only = entry["create_only"]

        plan = Plan.objects.filter(slug=slug).first()

        if plan is None:
            self.stdout.write("%s: se CREA" % slug)
            if not dry_run:
                Plan.objects.create(slug=slug, **synced, **create_only)
            return

        changes = {
            field: (getattr(plan, field), value)
            for field, value in synced.items()
            if getattr(plan, field) != value
        }

        if not changes:
            self.stdout.write("%s: sin cambios" % slug)
            return

        self.stdout.write("%s: se ACTUALIZA" % slug)
        for field, (old, new) in changes.items():
            self.stdout.write("    %s: %r -> %r" % (field, old, new))

        if not dry_run:
            for field, (_old, new) in changes.items():
                setattr(plan, field, new)
            plan.save()
