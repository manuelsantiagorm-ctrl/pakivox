from django.core.management.base import BaseCommand, CommandError

from messaging.production_readiness import collect_production_issues


class Command(BaseCommand):
    help = "Valida configuración, secretos, HTTPS y base antes del despliegue."

    def add_arguments(self, parser):
        parser.add_argument("--require-ycloud", action="store_true")
        parser.add_argument("--database", action="store_true")

    def handle(self, *args, **options):
        issues = collect_production_issues(
            require_ycloud=options["require_ycloud"],
            check_database=options["database"],
        )
        errors = [item for item in issues if item.id.startswith("wms360.E")]
        for issue in issues:
            writer = self.stderr if issue in errors else self.stdout
            writer.write(f"{issue.id}: {issue.msg}")
        if errors:
            raise CommandError(f"DESPLIEGUE NO LISTO: {len(errors)} error(es).")
        self.stdout.write(self.style.SUCCESS("DESPLIEGUE WMS360 LISTO"))
