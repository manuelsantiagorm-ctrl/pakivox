from __future__ import annotations

import hashlib
import json
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.core.management.color import no_style
from django.db import connections, transaction


EXCLUDED_LABELS = {
    "admin.logentry",
    "auth.permission",
    "contenttypes.contenttype",
    "sessions.session",
}


def model_counts(alias: str) -> dict[str, int]:
    connection = connections[alias]
    existing_tables = set(connection.introspection.table_names())
    result: dict[str, int] = {}

    for model in apps.get_models():
        label = model._meta.label_lower
        if label in EXCLUDED_LABELS:
            continue
        if model._meta.proxy or not model._meta.managed:
            continue
        if model._meta.db_table not in existing_tables:
            continue
        result[label] = model.objects.using(alias).count()

    return dict(sorted(result.items()))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def reset_postgres_sequences() -> int:
    connection = connections["default"]
    models = [
        model
        for model in apps.get_models()
        if model._meta.managed and not model._meta.proxy
    ]
    sql_list = connection.ops.sequence_reset_sql(
        no_style(),
        models,
    )

    with connection.cursor() as cursor:
        for statement in sql_list:
            cursor.execute(statement)

    return len(sql_list)


class Command(BaseCommand):
    help = (
        "Importa de SQLite a PostgreSQL dentro de una transacción, "
        "resetea secuencias y compara todos los conteos."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--fixture",
            default=(
                "backups/postgres_migration/"
                "wms360_sqlite_data.json"
            ),
        )
        parser.add_argument(
            "--report",
            default=(
                "backups/postgres_migration/"
                "import_validation.json"
            ),
        )
        parser.add_argument(
            "--confirm-import",
            action="store_true",
        )

    def handle(self, *args, **options):
        if not options["confirm_import"]:
            raise CommandError(
                "Debes agregar --confirm-import para autorizar "
                "la escritura en PostgreSQL."
            )

        if connections["default"].vendor != "postgresql":
            raise CommandError(
                "La base default no es PostgreSQL."
            )
        if connections["sqlite_legacy"].vendor != "sqlite":
            raise CommandError(
                "sqlite_legacy no apunta a SQLite."
            )

        fixture = Path(options["fixture"])
        if not fixture.is_absolute():
            fixture = Path(settings.BASE_DIR) / fixture
        if not fixture.exists():
            raise CommandError(
                f"No existe la exportación: {fixture}"
            )
        if fixture.stat().st_size == 0:
            raise CommandError(
                "La exportación está vacía."
            )

        source_before = model_counts("sqlite_legacy")
        target_before = model_counts("default")
        target_before_total = sum(target_before.values())

        if target_before_total:
            nonzero = {
                key: value
                for key, value in target_before.items()
                if value
            }
            raise CommandError(
                "PostgreSQL ya contiene datos de aplicación: "
                f"{nonzero}"
            )

        fixture_hash = file_sha256(fixture)
        self.stdout.write(
            f"Fixture: {fixture}"
        )
        self.stdout.write(
            f"SHA256: {fixture_hash}"
        )
        self.stdout.write(
            f"Registros esperados: {sum(source_before.values())}"
        )

        try:
            with transaction.atomic(using="default"):
                call_command(
                    "loaddata",
                    str(fixture),
                    database="default",
                    verbosity=1,
                )

                reset_count = reset_postgres_sequences()

                target_after = model_counts("default")
                mismatches = {
                    label: {
                        "sqlite": source_before.get(label, 0),
                        "postgresql": target_after.get(label, 0),
                    }
                    for label in sorted(
                        set(source_before) | set(target_after)
                    )
                    if source_before.get(label, 0)
                    != target_after.get(label, 0)
                }

                if mismatches:
                    raise CommandError(
                        "Los conteos no coinciden. "
                        "La transacción será revertida: "
                        f"{mismatches}"
                    )
        except Exception:
            self.stderr.write(
                self.style.ERROR(
                    "IMPORTACIÓN REVERTIDA: PostgreSQL vuelve "
                    "al estado previo."
                )
            )
            raise

        target_after = model_counts("default")
        report = {
            "fixture": str(fixture),
            "fixture_sha256": fixture_hash,
            "source_total": sum(source_before.values()),
            "target_total": sum(target_after.values()),
            "models_compared": len(source_before),
            "sequence_statements": reset_count,
            "mismatches": {},
            "source_counts": source_before,
            "target_counts": target_after,
        }

        report_path = Path(options["report"])
        if not report_path.is_absolute():
            report_path = Path(settings.BASE_DIR) / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        self.stdout.write(
            self.style.SUCCESS(
                "IMPORTACIÓN SQLITE → POSTGRESQL: OK"
            )
        )
        self.stdout.write(
            f"Registros SQLite: {report['source_total']}"
        )
        self.stdout.write(
            f"Registros PostgreSQL: {report['target_total']}"
        )
        self.stdout.write(
            f"Modelos comparados: {report['models_compared']}"
        )
        self.stdout.write(
            "Diferencias: 0"
        )
        self.stdout.write(
            f"Secuencias reajustadas: {reset_count}"
        )
        self.stdout.write(
            f"Reporte: {report_path}"
        )
        self.stdout.write(
            "db.sqlite3 permanece intacta."
        )
