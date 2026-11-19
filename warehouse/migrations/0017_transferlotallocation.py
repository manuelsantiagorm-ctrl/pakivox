from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0017_lot_foundation"),
        ("warehouse", "0016_pickinglotallocation"),
    ]

    operations = [
        migrations.AlterField(
            model_name="pickingitem",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pendiente"),
                    ("picked", "Recolectado"),
                    ("error", "Error"),
                    ("damaged", "Dañado"),
                    ("damaged_resolved", "Dañado · reemplazo asignado"),
                    ("reassigned", "Reasignado"),
                ],
                default="pending",
                max_length=30,
                verbose_name="Estado",
            ),
        ),
        migrations.AlterField(
            model_name="pickinglotallocation",
            name="status",
            field=models.CharField(
                choices=[
                    ("reserved", "Reservado"),
                    ("released", "Liberado"),
                    ("consumed", "Consumido"),
                    ("damaged", "Dañado / merma"),
                ],
                db_index=True,
                default="reserved",
                max_length=20,
                verbose_name="Estado",
            ),
        ),
        migrations.CreateModel(
            name="TransferLotAllocation",
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
                ("quantity", models.PositiveIntegerField(verbose_name="Cantidad asignada")),
                (
                    "received_quantity",
                    models.PositiveIntegerField(
                        default=0,
                        verbose_name="Cantidad recibida conforme",
                    ),
                ),
                (
                    "damaged_quantity",
                    models.PositiveIntegerField(
                        default=0,
                        verbose_name="Cantidad dañada",
                    ),
                ),
                (
                    "rejected_quantity",
                    models.PositiveIntegerField(
                        default=0,
                        verbose_name="Cantidad rechazada",
                    ),
                ),
                (
                    "missing_quantity",
                    models.PositiveIntegerField(
                        default=0,
                        verbose_name="Cantidad faltante",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("reserved", "Reservado"),
                            ("in_transit", "En tránsito"),
                            ("partial", "Recepción parcial"),
                            ("closed", "Contabilizado"),
                            ("released", "Liberado"),
                        ],
                        db_index=True,
                        default="reserved",
                        max_length=20,
                        verbose_name="Estado",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        verbose_name="Fecha de creación",
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
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="transfer_lot_allocations",
                        to="companies.company",
                    ),
                ),
                (
                    "destination_balance",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="transfer_in_allocations",
                        to="inventory.lotbalance",
                    ),
                ),
                (
                    "lot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="transfer_allocations",
                        to="inventory.productlot",
                    ),
                ),
                (
                    "source_balance",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="transfer_out_allocations",
                        to="inventory.lotbalance",
                    ),
                ),
                (
                    "transfer_item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lot_allocations",
                        to="warehouse.warehousetransferitem",
                    ),
                ),
            ],
            options={
                "verbose_name": "Asignación de lote de traspaso",
                "verbose_name_plural": "Asignaciones de lote de traspaso",
                "ordering": ["transfer_item_id", "lot_id", "id"],
                "indexes": [
                    models.Index(
                        fields=["company", "status"],
                        name="transferlot_co_status_idx",
                    ),
                    models.Index(
                        fields=["company", "lot"],
                        name="transferlot_co_lot_idx",
                    ),
                    models.Index(
                        fields=["company", "transfer_item"],
                        name="transferlot_co_item_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("transfer_item", "lot", "source_balance"),
                        name="uniq_transferlot_item_lot_balance",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(quantity__gt=0),
                        name="transferlot_qty_gt_zero",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(received_quantity__lte=models.F("quantity")),
                        name="transferlot_received_lte_qty",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(damaged_quantity__lte=models.F("quantity")),
                        name="transferlot_damaged_lte_qty",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(rejected_quantity__lte=models.F("quantity")),
                        name="transferlot_rejected_lte_qty",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(missing_quantity__lte=models.F("quantity")),
                        name="transferlot_missing_lte_qty",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            quantity__gte=(
                                models.F("received_quantity")
                                + models.F("damaged_quantity")
                                + models.F("rejected_quantity")
                                + models.F("missing_quantity")
                            )
                        ),
                        name="transferlot_accounted_lte_qty",
                    ),
                ],
            },
        ),
    ]
