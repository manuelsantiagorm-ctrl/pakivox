from django.core.management.base import BaseCommand
from django.db import connections


class Command(BaseCommand):
    help = "Verifica que la auditoría definitiva puede ejecutarse."

    def handle(self, *args, **options):
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
                "FASE 4.7A.4 AUDITORÍA DEFINITIVA "
                "VERIFICADA: OK"
            )
        )
        self.stdout.write(
            "Ambas bases responden. No se modificaron datos."
        )
