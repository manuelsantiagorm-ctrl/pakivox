from __future__ import annotations

import json
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from companies.models import Company
from inventory.models import LotReconciliationRun
from inventory.reconciliation_services import (
    repair_lot_aggregate_compatibility,
    scan_lot_inventory,
)


class Command(BaseCommand):
    help = (
        "LT-1G: reconcilia Stock / VariantLocation contra LotBalance. "
        "Por defecto es dry-run. --apply solo repara agregados compatibles "
        "y requiere --company-id."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--company-id",
            type=int,
            default=None,
            help="Limita la conciliación a una empresa.",
        )
        parser.add_argument(
            "--apply",
            action="store_true",
            help=(
                "Aplica únicamente reparaciones seguras de Stock y "
                "VariantLocation a partir de LotBalance."
            ),
        )
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Devuelve error si la conciliación final no queda limpia.",
        )
        parser.add_argument(
            "--json-output",
            default="",
            help="Ruta opcional donde guardar el reporte JSON.",
        )
        parser.add_argument(
            "--no-record",
            action="store_true",
            help="No persiste LotReconciliationRun. No afecta inventario.",
        )
        parser.add_argument(
            "--max-samples",
            type=int,
            default=50,
            help="Máximo de ejemplos por categoría en el reporte.",
        )

    def handle(self, *args, **options):
        company_id = options["company_id"]
        apply_changes = bool(options["apply"])
        strict = bool(options["strict"])
        no_record = bool(options["no_record"])
        max_samples = max(int(options["max_samples"] or 0), 1)

        company = None
        if company_id is not None:
            company = Company.objects.filter(pk=company_id).first()
            if company is None:
                raise CommandError(
                    f"No existe company_id={company_id}."
                )

        if apply_changes and company is None:
            raise CommandError(
                "--apply requiere --company-id. "
                "No se permiten reparaciones globales."
            )

        mode = (
            LotReconciliationRun.MODE_APPLY
            if apply_changes
            else LotReconciliationRun.MODE_DRY_RUN
        )

        run = None
        if not no_record:
            run = LotReconciliationRun.objects.create(
                company=company,
                mode=mode,
                status=LotReconciliationRun.STATUS_CLEAN,
                database_name=str(
                    connection.settings_dict.get("NAME") or ""
                ),
            )

        repairs = []
        try:
            if apply_changes:
                result = repair_lot_aggregate_compatibility(
                    company=company,
                )
                report = result["after"]
                repairs = result["repairs"]
            else:
                report = scan_lot_inventory(
                    company=company,
                    max_samples=max_samples,
                )

            summary = report["summary"]
            issues = int(summary["issues_found"] or 0)

            if repairs and issues == 0:
                status = LotReconciliationRun.STATUS_REPAIRED
            elif issues:
                status = LotReconciliationRun.STATUS_ISSUES
            else:
                status = LotReconciliationRun.STATUS_CLEAN

            if run is not None:
                run.status = status
                run.controlled_variants = int(
                    summary["controlled_variants"] or 0
                )
                run.lot_balances = int(
                    summary["lot_balances"] or 0
                )
                run.issues_found = issues
                run.repairs_applied = len(repairs)
                run.report = report
                run.repairs = repairs
                run.finished_at = timezone.now()
                run.save(
                    update_fields=[
                        "status",
                        "controlled_variants",
                        "lot_balances",
                        "issues_found",
                        "repairs_applied",
                        "report",
                        "repairs",
                        "finished_at",
                    ]
                )

            json_output = str(options["json_output"] or "").strip()
            if json_output:
                path = Path(json_output).expanduser().resolve()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(
                        {
                            "report": report,
                            "repairs": repairs,
                        },
                        ensure_ascii=False,
                        indent=2,
                        default=str,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                self.stdout.write(f"JSON={path}")

            self.stdout.write(
                "LT-1G RECONCILIACIÓN · "
                f"empresa={company.id if company else 'GLOBAL'} "
                f"controlados={summary['controlled_variants']} "
                f"balances={summary['lot_balances']} "
                f"issues={issues} "
                f"repairs={len(repairs)}"
            )

            aggregates = report["aggregates"]
            self.stdout.write(
                "Stock-LotBalance="
                f"{aggregates['stock_mismatch_count']} · "
                "Ubicación-LotBalance="
                f"{aggregates['location_mismatch_count']} · "
                "Sin clasificar="
                f"{aggregates['unclassified_controlled_stock_count'] + aggregates['unclassified_controlled_location_count']} · "
                "Ledger="
                f"{report['ledger']['ledger_tail_mismatch_count']} · "
                "Índices faltantes="
                f"{len(report['postgres_indexes']['missing'])}"
            )

            if summary["clean"]:
                self.stdout.write(
                    self.style.SUCCESS(
                        "RECONCILIACIÓN LIMPIA"
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        "RECONCILIACIÓN CON INCIDENCIAS"
                    )
                )

            if strict and not summary["clean"]:
                raise CommandError(
                    "La reconciliación LT-1G detectó incidencias."
                )

        except CommandError:
            # --strict puede fallar porque encontró incidencias reales.
            # En ese caso el run conserva STATUS_ISSUES y no se etiqueta
            # falsamente como error técnico del command.
            raise
        except Exception:
            if run is not None:
                run.status = LotReconciliationRun.STATUS_FAILED
                run.finished_at = timezone.now()
                run.save(
                    update_fields=[
                        "status",
                        "finished_at",
                    ]
                )
            raise
