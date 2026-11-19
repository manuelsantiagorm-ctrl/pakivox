from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core import serializers
from django.core.management.base import BaseCommand, CommandError
from django.db import connections


TARGET_LABELS = (
    "messaging.messagingcustomer",
    "messaging.conversation",
    "messaging.message",
    "messaging.whatsappnotificationlog",
)


def normalize(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def object_snapshot(obj) -> dict[str, Any]:
    values: dict[str, Any] = {
        "pk": normalize(obj.pk),
    }

    for field in obj._meta.concrete_fields:
        if field.primary_key:
            continue
        attname = field.attname
        try:
            value = getattr(obj, attname)
        except Exception as error:
            values[attname] = f"<error: {error}>"
            continue

        if field.is_relation:
            values[attname] = normalize(value)
        else:
            values[attname] = normalize(value)

    return values


def pk_set(alias: str, model) -> set[str]:
    return {
        str(value)
        for value in model.objects.using(alias)
        .values_list(model._meta.pk.attname, flat=True)
    }


def extra_objects(label: str) -> list[dict[str, Any]]:
    model = apps.get_model(label)
    sqlite_pks = pk_set("sqlite_legacy", model)
    postgres_pks = pk_set("default", model)
    extra_pks = sorted(postgres_pks - sqlite_pks)

    if not extra_pks:
        return []

    objects = model.objects.using("default").filter(
        pk__in=extra_pks
    ).order_by(model._meta.pk.attname)

    return [object_snapshot(obj) for obj in objects]


def natural_key_audit() -> dict[str, Any]:
    User = apps.get_model("auth", "User")
    Site = apps.get_model("sites", "Site")

    sqlite_users = {
        user.get_username(): {
            "pk": user.pk,
            "email": user.email,
            "is_active": user.is_active,
            "is_staff": user.is_staff,
            "is_superuser": user.is_superuser,
        }
        for user in User.objects.using("sqlite_legacy").all()
    }
    postgres_users = {
        user.get_username(): {
            "pk": user.pk,
            "email": user.email,
            "is_active": user.is_active,
            "is_staff": user.is_staff,
            "is_superuser": user.is_superuser,
        }
        for user in User.objects.using("default").all()
    }

    sqlite_sites = {
        site.domain: {
            "pk": site.pk,
            "name": site.name,
        }
        for site in Site.objects.using("sqlite_legacy").all()
    }
    postgres_sites = {
        site.domain: {
            "pk": site.pk,
            "name": site.name,
        }
        for site in Site.objects.using("default").all()
    }

    user_content_mismatches = {}
    for username in sorted(set(sqlite_users) | set(postgres_users)):
        source = sqlite_users.get(username)
        target = postgres_users.get(username)
        if source is None or target is None:
            user_content_mismatches[username] = {
                "sqlite": source,
                "postgresql": target,
            }
            continue

        source_without_pk = {
            key: value
            for key, value in source.items()
            if key != "pk"
        }
        target_without_pk = {
            key: value
            for key, value in target.items()
            if key != "pk"
        }
        if source_without_pk != target_without_pk:
            user_content_mismatches[username] = {
                "sqlite": source,
                "postgresql": target,
            }

    site_content_mismatches = {}
    for domain in sorted(set(sqlite_sites) | set(postgres_sites)):
        source = sqlite_sites.get(domain)
        target = postgres_sites.get(domain)
        if source is None or target is None:
            site_content_mismatches[domain] = {
                "sqlite": source,
                "postgresql": target,
            }
            continue

        source_without_pk = {
            key: value
            for key, value in source.items()
            if key != "pk"
        }
        target_without_pk = {
            key: value
            for key, value in target.items()
            if key != "pk"
        }
        if source_without_pk != target_without_pk:
            site_content_mismatches[domain] = {
                "sqlite": source,
                "postgresql": target,
            }

    return {
        "users": {
            "sqlite_count": len(sqlite_users),
            "postgresql_count": len(postgres_users),
            "content_mismatches": user_content_mismatches,
            "pk_mapping": {
                username: {
                    "sqlite_pk": sqlite_users[username]["pk"],
                    "postgresql_pk": postgres_users[username]["pk"],
                }
                for username in sorted(
                    set(sqlite_users) & set(postgres_users)
                )
                if sqlite_users[username]["pk"]
                != postgres_users[username]["pk"]
            },
        },
        "sites": {
            "sqlite_count": len(sqlite_sites),
            "postgresql_count": len(postgres_sites),
            "content_mismatches": site_content_mismatches,
            "pk_mapping": {
                domain: {
                    "sqlite_pk": sqlite_sites[domain]["pk"],
                    "postgresql_pk": postgres_sites[domain]["pk"],
                }
                for domain in sorted(
                    set(sqlite_sites) & set(postgres_sites)
                )
                if sqlite_sites[domain]["pk"]
                != postgres_sites[domain]["pk"]
            },
        },
    }


class Command(BaseCommand):
    help = (
        "Diagnostica los registros adicionales creados en PostgreSQL "
        "y valida usuarios/sitios por clave natural. No modifica datos."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--json-output",
            default=(
                "backups/postgres_migration/"
                "diagnostic_postgres_differences.json"
            ),
        )

    def handle(self, *args, **options):
        if connections["default"].vendor != "postgresql":
            raise CommandError("default debe ser PostgreSQL.")
        if connections["sqlite_legacy"].vendor != "sqlite":
            raise CommandError("sqlite_legacy debe ser SQLite.")

        extras = {
            label: extra_objects(label)
            for label in TARGET_LABELS
        }
        natural = natural_key_audit()

        report = {
            "extra_records": extras,
            "extra_record_counts": {
                label: len(records)
                for label, records in extras.items()
            },
            "extra_total": sum(
                len(records) for records in extras.values()
            ),
            "natural_key_audit": natural,
        }

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
            "DIAGNÓSTICO DE DIFERENCIAS POSTGRESQL"
        )
        self.stdout.write(
            f"Registros adicionales identificados: "
            f"{report['extra_total']}"
        )

        for label, records in extras.items():
            self.stdout.write("")
            self.stdout.write(
                f"{label}: {len(records)} adicionales"
            )
            for record in records:
                summary = {
                    key: value
                    for key, value in record.items()
                    if key in {
                        "pk",
                        "company_id",
                        "customer_id",
                        "conversation_id",
                        "whatsapp_number_id",
                        "provider_message_id",
                        "direction",
                        "message_type",
                        "status",
                        "created_at",
                        "updated_at",
                        "event_type",
                        "order_id",
                        "payment_id",
                    }
                }
                self.stdout.write(
                    "  " + json.dumps(
                        summary,
                        ensure_ascii=False,
                        default=str,
                    )
                )

        user_audit = natural["users"]
        site_audit = natural["sites"]

        self.stdout.write("")
        self.stdout.write("VALIDACIÓN POR CLAVE NATURAL")
        self.stdout.write(
            "Usuarios con contenido distinto: "
            f"{len(user_audit['content_mismatches'])}"
        )
        self.stdout.write(
            "Usuarios con PK reasignada: "
            f"{len(user_audit['pk_mapping'])}"
        )
        for username, mapping in user_audit["pk_mapping"].items():
            self.stdout.write(
                f"- usuario {username}: "
                f"SQLite={mapping['sqlite_pk']} | "
                f"PostgreSQL={mapping['postgresql_pk']}"
            )

        self.stdout.write(
            "Sites con contenido distinto: "
            f"{len(site_audit['content_mismatches'])}"
        )
        self.stdout.write(
            "Sites con PK reasignada: "
            f"{len(site_audit['pk_mapping'])}"
        )
        for domain, mapping in site_audit["pk_mapping"].items():
            self.stdout.write(
                f"- site {domain}: "
                f"SQLite={mapping['sqlite_pk']} | "
                f"PostgreSQL={mapping['postgresql_pk']}"
            )

        self.stdout.write("")
        self.stdout.write(f"Reporte JSON: {output}")
        self.stdout.write(
            self.style.SUCCESS(
                "DIAGNÓSTICO POSTGRESQL COMPLETADO: OK"
            )
        )
        self.stdout.write(
            "No se modificó SQLite ni PostgreSQL."
        )
