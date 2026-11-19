from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections


DEFAULT_EXCLUDED_LABELS = {
    "admin.logentry",
    "auth.permission",
    "contenttypes.contenttype",
    "sessions.session",
}


def eligible_models(alias: str):
    tables = set(connections[alias].introspection.table_names())

    for model in apps.get_models():
        label = model._meta.label_lower

        if label in DEFAULT_EXCLUDED_LABELS:
            continue
        if model._meta.proxy or not model._meta.managed:
            continue
        if model._meta.db_table not in tables:
            continue

        yield model


def model_count_map(alias: str) -> dict[str, int]:
    return {
        model._meta.label_lower: model.objects.using(alias).count()
        for model in eligible_models(alias)
    }


def ordered_primary_keys(alias: str, model) -> list[Any]:
    pk_name = model._meta.pk.attname
    return list(
        model.objects.using(alias)
        .order_by(pk_name)
        .values_list(pk_name, flat=True)
    )


def normalized_pk(value: Any) -> str:
    return str(value)


def pk_digest(values: list[Any]) -> str:
    payload = "\n".join(normalized_pk(value) for value in values)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def audit_databases() -> dict[str, Any]:
    if "sqlite_legacy" not in settings.DATABASES:
        raise CommandError(
            "No existe el alias sqlite_legacy en DATABASES."
        )

    if connections["default"].vendor != "postgresql":
        raise CommandError(
            "La base default debe ser PostgreSQL."
        )

    if connections["sqlite_legacy"].vendor != "sqlite":
        raise CommandError(
            "sqlite_legacy debe apuntar a SQLite."
        )

    sqlite_models = {
        model._meta.label_lower: model
        for model in eligible_models("sqlite_legacy")
    }
    postgres_models = {
        model._meta.label_lower: model
        for model in eligible_models("default")
    }

    labels = sorted(set(sqlite_models) | set(postgres_models))
    model_results: dict[str, Any] = {}
    count_mismatches: dict[str, Any] = {}
    pk_mismatches: dict[str, Any] = {}

    sqlite_total = 0
    postgres_total = 0

    for label in labels:
        model = sqlite_models.get(label) or postgres_models.get(label)

        sqlite_count = (
            model.objects.using("sqlite_legacy").count()
            if label in sqlite_models
            else 0
        )
        postgres_count = (
            model.objects.using("default").count()
            if label in postgres_models
            else 0
        )

        sqlite_total += sqlite_count
        postgres_total += postgres_count

        sqlite_pks = (
            ordered_primary_keys("sqlite_legacy", model)
            if label in sqlite_models
            else []
        )
        postgres_pks = (
            ordered_primary_keys("default", model)
            if label in postgres_models
            else []
        )

        sqlite_pk_set = {
            normalized_pk(value) for value in sqlite_pks
        }
        postgres_pk_set = {
            normalized_pk(value) for value in postgres_pks
        }

        missing_in_postgres = sorted(
            sqlite_pk_set - postgres_pk_set
        )
        extra_in_postgres = sorted(
            postgres_pk_set - sqlite_pk_set
        )

        model_result = {
            "sqlite_count": sqlite_count,
            "postgresql_count": postgres_count,
            "count_matches": sqlite_count == postgres_count,
            "sqlite_pk_sha256": pk_digest(sqlite_pks),
            "postgresql_pk_sha256": pk_digest(postgres_pks),
            "primary_keys_match": (
                not missing_in_postgres
                and not extra_in_postgres
            ),
            "missing_in_postgresql": missing_in_postgres[:100],
            "extra_in_postgresql": extra_in_postgres[:100],
            "missing_count": len(missing_in_postgres),
            "extra_count": len(extra_in_postgres),
        }
        model_results[label] = model_result

        if sqlite_count != postgres_count:
            count_mismatches[label] = {
                "sqlite": sqlite_count,
                "postgresql": postgres_count,
                "difference": postgres_count - sqlite_count,
            }

        if missing_in_postgres or extra_in_postgres:
            pk_mismatches[label] = {
                "missing_in_postgresql": missing_in_postgres[:100],
                "extra_in_postgresql": extra_in_postgres[:100],
                "missing_count": len(missing_in_postgres),
                "extra_count": len(extra_in_postgres),
            }

    return {
        "source": {
            "alias": "sqlite_legacy",
            "vendor": connections["sqlite_legacy"].vendor,
            "total": sqlite_total,
        },
        "target": {
            "alias": "default",
            "vendor": connections["default"].vendor,
            "total": postgres_total,
        },
        "models_compared": len(labels),
        "count_mismatch_count": len(count_mismatches),
        "pk_mismatch_count": len(pk_mismatches),
        "total_difference": postgres_total - sqlite_total,
        "certified": (
            not count_mismatches
            and not pk_mismatches
            and sqlite_total == postgres_total
        ),
        "count_mismatches": count_mismatches,
        "pk_mismatches": pk_mismatches,
        "models": model_results,
    }


class Command(BaseCommand):
    help = (
        "Audita definitivamente SQLite contra PostgreSQL por "
        "conteos y claves primarias. No modifica datos."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--json-output",
            default=(
                "backups/postgres_migration/"
                "audit_postgres_migration.json"
            ),
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help=(
                "Devuelve error si existe cualquier diferencia."
            ),
        )
        parser.add_argument(
            "--show-matching",
            action="store_true",
        )

    def handle(self, *args, **options):
        report = audit_databases()

        output = Path(options["json_output"])
        if not output.is_absolute():
            output = Path(settings.BASE_DIR) / output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

        self.stdout.write(
            "AUDITORÍA DEFINITIVA SQLITE ↔ POSTGRESQL"
        )
        self.stdout.write(
            f"Total SQLite: {report['source']['total']}"
        )
        self.stdout.write(
            f"Total PostgreSQL: {report['target']['total']}"
        )
        self.stdout.write(
            f"Diferencia total: {report['total_difference']}"
        )
        self.stdout.write(
            f"Modelos comparados: {report['models_compared']}"
        )
        self.stdout.write(
            "Modelos con diferencia de conteo: "
            f"{report['count_mismatch_count']}"
        )
        self.stdout.write(
            "Modelos con diferencia de claves primarias: "
            f"{report['pk_mismatch_count']}"
        )

        if report["count_mismatches"]:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "DIFERENCIAS DE CONTEO"
                )
            )
            for label, values in report[
                "count_mismatches"
            ].items():
                self.stdout.write(
                    f"- {label}: "
                    f"SQLite={values['sqlite']} | "
                    f"PostgreSQL={values['postgresql']} | "
                    f"Diferencia={values['difference']:+d}"
                )

        if report["pk_mismatches"]:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    "DIFERENCIAS DE CLAVES PRIMARIAS"
                )
            )
            for label, values in report[
                "pk_mismatches"
            ].items():
                self.stdout.write(
                    f"- {label}: "
                    f"faltan={values['missing_count']} | "
                    f"extras={values['extra_count']}"
                )
                if values["missing_in_postgresql"]:
                    self.stdout.write(
                        "  Faltan en PostgreSQL: "
                        + ", ".join(
                            values["missing_in_postgresql"]
                        )
                    )
                if values["extra_in_postgresql"]:
                    self.stdout.write(
                        "  Extras en PostgreSQL: "
                        + ", ".join(
                            values["extra_in_postgresql"]
                        )
                    )

        if options["show_matching"]:
            self.stdout.write("")
            self.stdout.write("MODELOS SIN DIFERENCIAS")
            for label, values in report["models"].items():
                if (
                    values["count_matches"]
                    and values["primary_keys_match"]
                ):
                    self.stdout.write(
                        f"- {label}: "
                        f"{values['sqlite_count']}"
                    )

        self.stdout.write("")
        self.stdout.write(f"Reporte JSON: {output}")

        if report["certified"]:
            self.stdout.write(
                self.style.SUCCESS(
                    "MIGRACIÓN POSTGRESQL CERTIFICADA: OK"
                )
            )
            self.stdout.write(
                "Conteos y claves primarias coinciden al 100%."
            )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "MIGRACIÓN POSTGRESQL: REQUIERE REVISIÓN"
                )
            )
            self.stdout.write(
                "No se modificó ninguna base."
            )

        if options["strict"] and not report["certified"]:
            raise CommandError(
                "La auditoría encontró diferencias."
            )
