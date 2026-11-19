import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import F, Q


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("companies", "0004_company_timezone"),
        ("inventory", "0019_lotreconciliationrun"),
        ("sales", "0010_order_confirmation_timeline"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="WarehouseFinancialFact",
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
                    "fact_type",
                    models.CharField(
                        choices=[
                            ("sale", "Venta"),
                            ("collection", "Cobro"),
                            ("refund", "Reembolso"),
                            ("reversal", "Reversión"),
                            ("adjustment", "Ajuste auditado"),
                        ],
                        db_index=True,
                        max_length=24,
                    ),
                ),
                ("currency", models.CharField(default="MXN", max_length=3)),
                ("source_version", models.PositiveIntegerField(default=1)),
                ("idempotency_key", models.CharField(max_length=160)),
                ("subtotal_cents", models.BigIntegerField(default=0)),
                ("discount_cents", models.BigIntegerField(default=0)),
                ("tax_cents", models.BigIntegerField(default=0)),
                ("shipping_cents", models.BigIntegerField(default=0)),
                ("cost_cents", models.BigIntegerField(default=0)),
                ("total_cents", models.BigIntegerField(default=0)),
                ("gross_profit_cents", models.BigIntegerField(default=0)),
                ("collected_cents", models.BigIntegerField(default=0)),
                ("rounding_residual_cents", models.BigIntegerField(default=0)),
                ("legacy_difference_cents", models.BigIntegerField(default=0)),
                (
                    "has_legacy_inconsistency",
                    models.BooleanField(db_index=True, default=False),
                ),
                (
                    "legacy_inconsistency_reason",
                    models.CharField(blank=True, max_length=255),
                ),
                ("effective_at_utc", models.DateTimeField(db_index=True)),
                ("effective_local_date", models.DateField(db_index=True)),
                (
                    "date_basis",
                    models.CharField(
                        choices=[
                            ("order_confirmed", "Confirmación del pedido"),
                            ("payment_approved", "Aprobación del cobro"),
                            ("refund_confirmed", "Confirmación del reembolso"),
                            ("reversal_effective", "Aplicación de la reversión"),
                            ("manual_effective", "Fecha efectiva manual"),
                        ],
                        max_length=32,
                    ),
                ),
                ("occurred_at", models.DateTimeField(db_index=True)),
                ("recorded_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("timezone_snapshot", models.CharField(max_length=64)),
                (
                    "quality_label",
                    models.CharField(
                        choices=[
                            ("real", "Real"),
                            ("attributed", "Atribuido"),
                            ("estimated", "Estimado"),
                        ],
                        db_index=True,
                        max_length=16,
                    ),
                ),
                ("is_estimated", models.BooleanField(db_index=True, default=False)),
                (
                    "allocation_method",
                    models.CharField(
                        choices=[
                            ("exact_line", "Asignación exacta por línea"),
                            ("proportional", "Prorrateo proporcional"),
                            ("unassigned", "Sin asignar"),
                            ("manual", "Asignación manual auditada"),
                        ],
                        db_index=True,
                        max_length=24,
                    ),
                ),
                (
                    "actor_type",
                    models.CharField(
                        choices=[
                            ("user", "Usuario"),
                            ("system", "Sistema"),
                            ("integration", "Integración"),
                            ("migration", "Migración"),
                        ],
                        max_length=16,
                    ),
                ),
                ("actor_snapshot", models.JSONField(blank=True, default=dict)),
                ("source_event_type", models.CharField(max_length=80)),
                ("source_event_id", models.CharField(max_length=160)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="warehouse_financial_facts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="warehouse_financial_facts",
                        to="companies.company",
                    ),
                ),
                (
                    "order",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="warehouse_financial_facts",
                        to="sales.order",
                    ),
                ),
                (
                    "reversal_of",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="compensating_facts",
                        to="dashboard.warehousefinancialfact",
                    ),
                ),
                (
                    "warehouse",
                    models.ForeignKey(
                        blank=True,
                        help_text="NULL representa exclusivamente el bucket Sin asignar.",
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="financial_facts",
                        to="inventory.warehouse",
                    ),
                ),
            ],
            options={
                "verbose_name": "Hecho financiero por bodega",
                "verbose_name_plural": "Hechos financieros por bodega",
                "ordering": ["-effective_at_utc", "-id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("company", "idempotency_key"),
                        name="dash_wff_company_idem_uq",
                    ),
                    models.CheckConstraint(
                        condition=Q(("source_version__gte", 1)),
                        name="dash_wff_version_pos_ck",
                    ),
                    models.CheckConstraint(
                        condition=Q(
                            (
                                "total_cents",
                                F("subtotal_cents")
                                - F("discount_cents")
                                + F("tax_cents")
                                + F("shipping_cents")
                                + F("legacy_difference_cents"),
                            )
                        ),
                        name="dash_wff_total_formula_ck",
                    ),
                    models.CheckConstraint(
                        condition=Q(
                            (
                                "gross_profit_cents",
                                F("subtotal_cents")
                                - F("discount_cents")
                                - F("cost_cents"),
                            )
                        ),
                        name="dash_wff_profit_formula_ck",
                    ),
                    models.CheckConstraint(
                        condition=(
                            Q(
                                ("has_legacy_inconsistency", False),
                                ("legacy_difference_cents", 0),
                            )
                            | (
                                Q(("has_legacy_inconsistency", True))
                                & ~Q(("legacy_difference_cents", 0))
                            )
                        ),
                        name="dash_wff_legacy_flag_ck",
                    ),
                    models.CheckConstraint(
                        condition=(
                            Q(("is_estimated", True), ("quality_label", "estimated"))
                            | Q(
                                ("is_estimated", False),
                                ("quality_label__in", ["real", "attributed"]),
                            )
                        ),
                        name="dash_wff_quality_flag_ck",
                    ),
                    models.CheckConstraint(
                        condition=(
                            Q(
                                ("allocation_method", "unassigned"),
                                ("warehouse__isnull", True),
                            )
                            | (
                                ~Q(("allocation_method", "unassigned"))
                                & Q(("warehouse__isnull", False))
                            )
                        ),
                        name="dash_wff_scope_ck",
                    ),
                ],
                "indexes": [
                    models.Index(
                        fields=["company", "effective_local_date", "fact_type"],
                        name="dash_wff_co_date_type_idx",
                    ),
                    models.Index(
                        fields=["company", "warehouse", "effective_local_date"],
                        name="dash_wff_co_wh_date_idx",
                    ),
                    models.Index(
                        fields=["company", "order", "effective_at_utc"],
                        name="dash_wff_co_ord_time_idx",
                    ),
                    models.Index(
                        fields=["company", "source_event_type", "source_event_id"],
                        name="dash_wff_co_source_idx",
                    ),
                ],
            },
        ),
    ]
