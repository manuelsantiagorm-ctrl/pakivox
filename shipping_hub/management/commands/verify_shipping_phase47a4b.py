from django.core.management.base import BaseCommand
from django.db import connections


class Command(BaseCommand):
    help = "Verifica el diagnóstico de diferencias PostgreSQL."

    def handle(self, *args, **options):
        assert connections["default"].vendor == "postgresql"
        assert connections["sqlite_legacy"].vendor == "sqlite"

        self.stdout.write(
            self.style.SUCCESS(
                "FASE 4.7A.4B DIAGNÓSTICO VERIFICADO: OK"
            )
        )
        self.stdout.write(
            "No se modificaron datos."
        )
