from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0017_lot_foundation"),
    ]

    operations = [
        migrations.CreateModel(
            name="LotExpiryAlert",
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
                    "severity",
                    models.CharField(
                        choices=[
                            ("info", "Informativa"),
                            ("notice", "Aviso"),
                            ("warning", "Advertencia"),
                            ("critical", "Crítica"),
                            ("expired", "Vencida"),
                        ],
                        db_index=True,
                        max_length=20,
                        verbose_name="Severidad",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Activa"),
                            ("resolved", "Resuelta"),
                        ],
                        db_index=True,
                        default="active",
                        max_length=20,
                        verbose_name="Estado",
                    ),
                ),
                ("expiration_date", models.DateField(verbose_name="Caducidad")),
                ("days_to_expiry", models.IntegerField(verbose_name="Días para vencer")),
                (
                    "affected_quantity",
                    models.PositiveIntegerField(
                        default=0,
                        verbose_name="Cantidad física afectada",
                    ),
                ),
                ("is_read", models.BooleanField(default=False, verbose_name="Leída")),
                (
                    "message",
                    models.TextField(
                        blank=True,
                        default="",
                        verbose_name="Mensaje",
                    ),
                ),
                (
                    "first_detected_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        verbose_name="Primera detección",
                    ),
                ),
                (
                    "updated_at",
                    models.DateTimeField(
                        auto_now=True,
                        verbose_name="Última actualización",
                    ),
                ),
                (
                    "resolved_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="Fecha de resolución",
                    ),
                ),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lot_expiry_alerts",
                        to="companies.company",
                    ),
                ),
                (
                    "lot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="expiry_alerts",
                        to="inventory.productlot",
                    ),
                ),
                (
                    "warehouse",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lot_expiry_alerts",
                        to="inventory.warehouse",
                    ),
                ),
            ],
            options={
                "verbose_name": "Alerta de caducidad de lote",
                "verbose_name_plural": "Alertas de caducidad de lotes",
                "ordering": ["days_to_expiry", "expiration_date", "id"],
                "indexes": [
                    models.Index(
                        fields=["company", "status", "severity"],
                        name="lotexp_co_st_sev_idx",
                    ),
                    models.Index(
                        fields=["company", "warehouse", "status"],
                        name="lotexp_co_wh_st_idx",
                    ),
                    models.Index(
                        fields=["company", "lot"],
                        name="lotexp_co_lot_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(status="active"),
                        fields=("company", "warehouse", "lot"),
                        name="uniq_active_lotexp_co_wh_lot",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(affected_quantity__gte=0),
                        name="lotexp_qty_nonneg",
                    ),
                ],
            },
        ),
    ]
