from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0001_initial"),
        ("inventory", "0018_lotexpiryalert"),
    ]

    operations = [
        migrations.CreateModel(
            name="LotReconciliationRun",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "mode",
                    models.CharField(
                        choices=[
                            ("dry_run", "Solo diagnóstico"),
                            ("apply", "Aplicar reparación"),
                        ],
                        db_index=True,
                        default="dry_run",
                        max_length=20,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("clean", "Sin inconsistencias"),
                            ("issues", "Con inconsistencias"),
                            ("repaired", "Reparado"),
                            ("failed", "Fallido"),
                        ],
                        db_index=True,
                        default="clean",
                        max_length=20,
                    ),
                ),
                ("controlled_variants", models.PositiveIntegerField(default=0)),
                ("lot_balances", models.PositiveIntegerField(default=0)),
                ("issues_found", models.PositiveIntegerField(default=0)),
                ("repairs_applied", models.PositiveIntegerField(default=0)),
                ("report", models.JSONField(blank=True, default=dict)),
                ("repairs", models.JSONField(blank=True, default=list)),
                ("database_name", models.CharField(blank=True, default="", max_length=150)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                (
                    "company",
                    models.ForeignKey(
                        blank=True,
                        help_text="Empresa limitada por el run. NULL significa alcance global.",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="lot_reconciliation_runs",
                        to="companies.company",
                    ),
                ),
            ],
            options={
                "verbose_name": "Ejecución de conciliación de lotes",
                "verbose_name_plural": "Ejecuciones de conciliación de lotes",
                "ordering": ["-started_at", "-id"],
                "indexes": [
                    models.Index(
                        fields=["company", "started_at"],
                        name="lotrecon_co_started_idx",
                    ),
                    models.Index(
                        fields=["status", "started_at"],
                        name="lotrecon_st_started_idx",
                    ),
                ],
            },
        ),
    ]
