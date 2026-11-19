from django.core.management.base import BaseCommand
from django.db import connection
from django.conf import settings


class Command(BaseCommand):
    help = "Verifica el backend y capacidades de la base de datos."

    def handle(self, *args, **options):
        features = connection.features

        self.stdout.write(
            f"DATABASE_ENGINE configurado: "
            f"{getattr(settings, 'DATABASE_ENGINE', 'desconocido')}"
        )
        self.stdout.write(f"Backend activo: {connection.vendor}")
        self.stdout.write(
            "select_for_update: "
            f"{'sí' if features.has_select_for_update else 'no'}"
        )
        self.stdout.write(
            "skip_locked: "
            f"{'sí' if features.has_select_for_update_skip_locked else 'no'}"
        )

        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            value = cursor.fetchone()[0]

        if value != 1:
            raise RuntimeError(
                "La prueba básica de conexión no devolvió el valor esperado."
            )

        self.stdout.write(
            self.style.SUCCESS("CONEXIÓN A BASE DE DATOS: OK")
        )

        if connection.vendor == "postgresql":
            self.stdout.write(
                self.style.SUCCESS(
                    "POSTGRESQL LISTO PARA PRUEBAS DE CONCURRENCIA."
                )
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "SQLite continúa activo. Todavía no se ejecutarán "
                    "pruebas reales de concurrencia."
                )
            )
