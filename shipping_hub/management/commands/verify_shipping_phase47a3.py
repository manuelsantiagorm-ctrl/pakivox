from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connections


class Command(BaseCommand):
    help = "Verifica el puente SQLite/PostgreSQL."

    def handle(self, *args, **options):
        assert "default" in settings.DATABASES
        assert "sqlite_legacy" in settings.DATABASES
        assert connections["default"].vendor == "postgresql"
        assert connections["sqlite_legacy"].vendor == "sqlite"

        with connections["default"].cursor() as cursor:
            cursor.execute("SELECT 1")
            assert cursor.fetchone()[0] == 1

        with connections["sqlite_legacy"].cursor() as cursor:
            cursor.execute("SELECT 1")
            assert cursor.fetchone()[0] == 1

        self.stdout.write(
            self.style.SUCCESS(
                "PUENTE SQLITE/POSTGRESQL VERIFICADO: OK"
            )
        )
        self.stdout.write(
            "Ambas bases responden y ninguna fue modificada."
        )
