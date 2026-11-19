from __future__ import annotations

import json
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections


EXCLUDED_LABELS = {
    "admin.logentry",
    "auth.permission",
    "contenttypes.contenttype",
    "sessions.session",
}


def model_counts(alias: str) -> dict[str, int]:
    result = {}
    connection = connections[alias]
    existing_tables = set(connection.introspection.table_names())

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


class Command(BaseCommand):
    help = (
        "Audita SQLite y PostgreSQL antes de migrar. "
        "No modifica ninguna base."
    )

    def add_arguments(self, parser):
        parser.add_argument("--json-output", default="")
        parser.add_argument("--strict-target-empty", action="store_true")

    def handle(self, *args, **options):
        if "sqlite_legacy" not in settings.DATABASES:
            raise CommandError("No existe el alias sqlite_legacy.")

        if connections["default"].vendor != "postgresql":
            raise CommandError(
                "La base default debe ser PostgreSQL para esta fase."
            )

        source = model_counts("sqlite_legacy")
        target = model_counts("default")

        source_total = sum(source.values())
        target_total = sum(target.values())
        source_nonzero = {
            label: count for label, count in source.items() if count
        }
        target_nonzero = {
            label: count for label, count in target.items() if count
        }

        payload = {
            "source": {
                "alias": "sqlite_legacy",
                "vendor": connections["sqlite_legacy"].vendor,
                "total": source_total,
                "nonzero_models": source_nonzero,
            },
            "target": {
                "alias": "default",
                "vendor": connections["default"].vendor,
                "total": target_total,
                "nonzero_models": target_nonzero,
            },
        }

        self.stdout.write("MIGRACIÓN SQLITE → POSTGRESQL — PREFLIGHT")
        self.stdout.write(f"Registros SQLite: {source_total}")
        self.stdout.write(f"Registros PostgreSQL: {target_total}")
        self.stdout.write(
            f"Modelos SQLite con datos: {len(source_nonzero)}"
        )
        self.stdout.write(
            f"Modelos PostgreSQL con datos: {len(target_nonzero)}"
        )

        for label, count in source_nonzero.items():
            self.stdout.write(f"- {label}: {count}")

        if options["json_output"]:
            path = Path(options["json_output"])
            if not path.is_absolute():
                path = Path(settings.BASE_DIR) / path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.stdout.write(f"Reporte: {path}")

        if options["strict_target_empty"] and target_total:
            raise CommandError(
                "PostgreSQL contiene datos de aplicación. "
                "No es seguro importar todavía."
            )

        self.stdout.write(
            self.style.SUCCESS("PREFLIGHT DE MIGRACIÓN: OK")
        )
        self.stdout.write(
            "No se modificó SQLite ni PostgreSQL."
        )
