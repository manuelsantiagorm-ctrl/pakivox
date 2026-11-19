from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0017_lot_foundation"),
        ("warehouse", "0015_reservation_production_indexes"),
    ]

    operations = [
        migrations.CreateModel(
            name="PickingLotAllocation",
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
                    "quantity",
                    models.PositiveIntegerField(
                        verbose_name="Cantidad reservada del lote",
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("reserved", "Reservado"),
                            ("released", "Liberado"),
                            ("consumed", "Consumido"),
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
                    "balance",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="picking_allocations",
                        to="inventory.lotbalance",
                    ),
                ),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="picking_lot_allocations",
                        to="companies.company",
                    ),
                ),
                (
                    "lot",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="picking_allocations",
                        to="inventory.productlot",
                    ),
                ),
                (
                    "picking_item",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lot_allocation",
                        to="warehouse.pickingitem",
                    ),
                ),
            ],
            options={
                "verbose_name": "Asignación de lote de picking",
                "verbose_name_plural": "Asignaciones de lote de picking",
                "ordering": ["picking_item_id", "id"],
                "indexes": [
                    models.Index(
                        fields=["company", "status"],
                        name="picklot_co_status_idx",
                    ),
                    models.Index(
                        fields=["company", "lot"],
                        name="picklot_co_lot_idx",
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=models.Q(quantity__gt=0),
                        name="picklot_qty_gt_zero",
                    ),
                ],
            },
        ),
    ]
