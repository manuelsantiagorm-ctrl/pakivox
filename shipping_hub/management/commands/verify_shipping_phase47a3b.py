from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connections


class Command(BaseCommand):
    help = "Verifica que la importación controlada está lista."

    def handle(self, *args, **options):
        fixture = (
            Path(settings.BASE_DIR)
            / "backups"
            / "postgres_migration"
            / "wms360_sqlite_data.json"
        )

        assert connections["default"].vendor == "postgresql"
        assert connections["sqlite_legacy"].vendor == "sqlite"
        assert fixture.exists()
        assert fixture.stat().st_size > 0

        self.stdout.write(
            self.style.SUCCESS(
                "FASE 4.7A.3B IMPORTACIÓN CONTROLADA "
                "VERIFICADA: OK"
            )
        )
        self.stdout.write(
            f"Fixture disponible: {fixture}"
        )
        self.stdout.write(
            "Todavía no se importaron datos."
        )
