import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import F, Q


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0016_stock_production_index"),
        ("warehouse", "0015_reservation_production_indexes"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="productvariant",
            name="inventory_control",
            field=models.CharField(
                choices=[
                    ("normal", "Normal / sin lote"),
                    ("lot", "Control por lote"),
                    ("lot_expiry", "Lote + caducidad"),
                ],
                db_index=True,
                default="normal",
                help_text=(
                    "NORMAL mantiene el flujo actual. LOT exige lote. "
                    "LOT_EXPIRY exige lote y fecha de caducidad."
                ),
                max_length=20,
                verbose_name="Control de inventario",
            ),
        ),
        migrations.CreateModel(
            name="ProductLot",
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
                ("lot_number", models.CharField(max_length=120, verbose_name="Número de lote")),
                (
                    "manufactured_date",
                    models.DateField(blank=True, null=True, verbose_name="Fecha de fabricación"),
                ),
                (
                    "expiration_date",
                    models.DateField(
                        blank=True,
                        db_index=True,
                        null=True,
                        verbose_name="Fecha de caducidad",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("active", "Activo"),
                            ("blocked", "Bloqueado"),
                            ("recalled", "Retirado / Recall"),
                        ],
                        db_index=True,
                        default="active",
                        max_length=20,
                        verbose_name="Estado global",
                    ),
                ),
                (
                    "status_reason",
                    models.TextField(blank=True, default="", verbose_name="Motivo del estado"),
                ),
                (
                    "status_changed_at",
                    models.DateTimeField(
                        blank=True,
                        null=True,
                        verbose_name="Fecha del cambio de estado",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Fecha de creación")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Última actualización")),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="product_lots",
                        to="companies.company",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_product_lots",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "status_changed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="product_lot_status_changes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "variant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="lots",
                        to="inventory.productvariant",
                    ),
                ),
            ],
            options={
                "verbose_name": "Lote de producto",
                "verbose_name_plural": "Lotes de producto",
                "ordering": ["expiration_date", "lot_number", "id"],
                "indexes": [
                    models.Index(
                        fields=["company", "variant", "status", "expiration_date"],
                        name="lot_co_var_st_exp_idx",
                    ),
                    models.Index(
                        fields=["company", "status"],
                        name="lot_co_status_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("company", "variant", "lot_number"),
                        name="uniq_lot_co_var_number",
                    ),
                    models.CheckConstraint(
                        condition=~Q(lot_number=""),
                        name="lot_number_not_empty",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="LotBalance",
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
                    "status",
                    models.CharField(
                        choices=[
                            ("available", "Disponible"),
                            ("quarantine", "Cuarentena"),
                            ("blocked", "Bloqueado"),
                            ("waste", "Merma / Desecho"),
                        ],
                        db_index=True,
                        default="available",
                        max_length=20,
                        verbose_name="Estado operativo",
                    ),
                ),
                ("physical_quantity", models.IntegerField(default=0, verbose_name="Cantidad física")),
                ("reserved_quantity", models.IntegerField(default=0, verbose_name="Cantidad reservada")),
                ("committed_quantity", models.IntegerField(default=0, verbose_name="Cantidad comprometida")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Fecha de creación")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Última actualización")),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lot_balances",
                        to="companies.company",
                    ),
                ),
                (
                    "location",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="lot_balances",
                        to="warehouse.warehouselocation",
                    ),
                ),
                (
                    "lot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="balances",
                        to="inventory.productlot",
                    ),
                ),
            ],
            options={
                "verbose_name": "Saldo por lote",
                "verbose_name_plural": "Saldos por lote",
                "ordering": ["lot_id", "location_id", "status", "id"],
                "indexes": [
                    models.Index(
                        fields=["company", "lot", "status"],
                        name="lotbal_co_lot_st_idx",
                    ),
                    models.Index(
                        fields=["company", "location", "status"],
                        name="lotbal_co_loc_st_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("company", "lot", "location"),
                        name="uniq_lotbal_co_lot_loc",
                    ),
                    models.CheckConstraint(
                        condition=Q(physical_quantity__gte=0),
                        name="lotbal_physical_nonneg",
                    ),
                    models.CheckConstraint(
                        condition=Q(reserved_quantity__gte=0),
                        name="lotbal_reserved_nonneg",
                    ),
                    models.CheckConstraint(
                        condition=Q(committed_quantity__gte=0),
                        name="lotbal_committed_nonneg",
                    ),
                    models.CheckConstraint(
                        condition=Q(
                            physical_quantity__gte=(
                                F("reserved_quantity") + F("committed_quantity")
                            )
                        ),
                        name="lotbal_res_comm_lte_phys",
                    ),
                    models.CheckConstraint(
                        condition=(
                            Q(status="available")
                            | (Q(reserved_quantity=0) & Q(committed_quantity=0))
                        ),
                        name="lotbal_nonavail_unreserved",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="LotMovement",
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
                    "movement_type",
                    models.CharField(
                        choices=[
                            ("receipt", "Recepción"),
                            ("reservation", "Reserva"),
                            ("release", "Liberación"),
                            ("commit", "Compromiso"),
                            ("consume", "Consumo"),
                            ("transfer_out", "Transferencia - salida"),
                            ("transfer_in", "Transferencia - entrada"),
                            ("adjustment", "Ajuste"),
                            ("return_quarantine", "Devolución a cuarentena"),
                            ("quarantine_release", "Liberación de cuarentena"),
                            ("block", "Bloqueo"),
                            ("unblock", "Desbloqueo"),
                            ("recall", "Recall / retiro"),
                            ("waste", "Merma / desecho"),
                            ("reconciliation", "Conciliación"),
                        ],
                        max_length=30,
                        verbose_name="Tipo de movimiento",
                    ),
                ),
                ("quantity", models.PositiveIntegerField(default=0, verbose_name="Cantidad")),
                (
                    "reference_type",
                    models.CharField(blank=True, default="", max_length=60, verbose_name="Tipo de referencia"),
                ),
                (
                    "reference_id",
                    models.PositiveBigIntegerField(blank=True, null=True, verbose_name="ID de referencia"),
                ),
                (
                    "reference_code",
                    models.CharField(blank=True, default="", max_length=150, verbose_name="Referencia visible"),
                ),
                (
                    "event_key",
                    models.CharField(
                        blank=True,
                        max_length=180,
                        null=True,
                        verbose_name="Clave idempotente del evento",
                    ),
                ),
                ("physical_before", models.IntegerField(blank=True, editable=False, null=True)),
                ("physical_after", models.IntegerField(blank=True, editable=False, null=True)),
                ("reserved_before", models.IntegerField(blank=True, editable=False, null=True)),
                ("reserved_after", models.IntegerField(blank=True, editable=False, null=True)),
                ("committed_before", models.IntegerField(blank=True, editable=False, null=True)),
                ("committed_after", models.IntegerField(blank=True, editable=False, null=True)),
                ("notes", models.TextField(blank=True, default="", verbose_name="Notas")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Fecha")),
                (
                    "balance",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="movements",
                        to="inventory.lotbalance",
                    ),
                ),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lot_movements",
                        to="companies.company",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_lot_movements",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "lot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="movements",
                        to="inventory.productlot",
                    ),
                ),
            ],
            options={
                "verbose_name": "Movimiento de lote",
                "verbose_name_plural": "Movimientos de lote",
                "ordering": ["-created_at", "-id"],
                "indexes": [
                    models.Index(
                        fields=["company", "lot", "created_at"],
                        name="lotmove_co_lot_dt_idx",
                    ),
                    models.Index(
                        fields=["company", "reference_type", "reference_id"],
                        name="lotmove_co_ref_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        condition=Q(event_key__isnull=False),
                        fields=("company", "event_key"),
                        name="uniq_lotmove_co_eventkey",
                    ),
                    models.CheckConstraint(
                        condition=(Q(physical_before__isnull=True) | Q(physical_before__gte=0)),
                        name="lotmove_phys_before_nonneg",
                    ),
                    models.CheckConstraint(
                        condition=(Q(physical_after__isnull=True) | Q(physical_after__gte=0)),
                        name="lotmove_phys_after_nonneg",
                    ),
                    models.CheckConstraint(
                        condition=(Q(reserved_before__isnull=True) | Q(reserved_before__gte=0)),
                        name="lotmove_res_before_nonneg",
                    ),
                    models.CheckConstraint(
                        condition=(Q(reserved_after__isnull=True) | Q(reserved_after__gte=0)),
                        name="lotmove_res_after_nonneg",
                    ),
                    models.CheckConstraint(
                        condition=(Q(committed_before__isnull=True) | Q(committed_before__gte=0)),
                        name="lotmove_com_before_nonneg",
                    ),
                    models.CheckConstraint(
                        condition=(Q(committed_after__isnull=True) | Q(committed_after__gte=0)),
                        name="lotmove_com_after_nonneg",
                    ),
                ],
            },
        ),
    ]
