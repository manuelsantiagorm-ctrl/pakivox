from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connections


EXCLUDES = (
    "admin.logentry",
    "auth.permission",
    "contenttypes.contenttype",
    "sessions.session",
)


class Command(BaseCommand):
    help = (
        "Exporta datos de SQLite a JSON natural para PostgreSQL. "
        "No modifica ninguna base."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            default="backups/postgres_migration/wms360_sqlite_data.json",
        )

    def handle(self, *args, **options):
        if connections["sqlite_legacy"].vendor != "sqlite":
            raise CommandError("sqlite_legacy no apunta a SQLite.")

        output = Path(options["output"])
        if not output.is_absolute():
            output = Path(settings.BASE_DIR) / output
        output.parent.mkdir(parents=True, exist_ok=True)

        with output.open("w", encoding="utf-8") as stream:
            call_command(
                "dumpdata",
                database="sqlite_legacy",
                exclude=EXCLUDES,
                natural_foreign=True,
                natural_primary=True,
                indent=2,
                stdout=stream,
            )

        if not output.exists() or output.stat().st_size == 0:
            raise CommandError("La exportación quedó vacía.")

        self.stdout.write(
            self.style.SUCCESS("EXPORTACIÓN SQLITE: OK")
        )
        self.stdout.write(f"Archivo: {output}")
        self.stdout.write(f"Tamaño: {output.stat().st_size} bytes")
        self.stdout.write(
            "No se modificó SQLite ni PostgreSQL."
        )
