from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0017_lot_foundation"),
        ("purchases", "0003_purchaseorderalertlink"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="GoodsReceiptLot",
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
                    "lot_number",
                    models.CharField(
                        max_length=120,
                        verbose_name="Número de lote",
                    ),
                ),
                (
                    "quantity_received",
                    models.PositiveIntegerField(
                        verbose_name="Cantidad de este lote",
                    ),
                ),
                (
                    "manufactured_date",
                    models.DateField(
                        blank=True,
                        null=True,
                        verbose_name="Fecha de fabricación",
                    ),
                ),
                (
                    "expiration_date",
                    models.DateField(
                        blank=True,
                        null=True,
                        verbose_name="Fecha de caducidad",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="goods_receipt_lots",
                        to="companies.company",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_goods_receipt_lots",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "product_lot",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="goods_receipt_allocations",
                        to="inventory.productlot",
                    ),
                ),
                (
                    "receipt_item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lot_allocations",
                        to="purchases.goodsreceiptitem",
                    ),
                ),
            ],
            options={
                "verbose_name": "Lote recibido",
                "verbose_name_plural": "Lotes recibidos",
                "ordering": ["receipt_item_id", "id"],
            },
        ),
        migrations.AddConstraint(
            model_name="goodsreceiptlot",
            constraint=models.UniqueConstraint(
                fields=("receipt_item", "lot_number"),
                name="uniq_receipt_item_lot_number",
            ),
        ),
        migrations.AddConstraint(
            model_name="goodsreceiptlot",
            constraint=models.CheckConstraint(
                condition=models.Q(quantity_received__gt=0),
                name="receipt_lot_qty_gt_zero",
            ),
        ),
        migrations.AddIndex(
            model_name="goodsreceiptlot",
            index=models.Index(
                fields=["company", "receipt_item"],
                name="greclot_co_item_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="goodsreceiptlot",
            index=models.Index(
                fields=["company", "lot_number"],
                name="greclot_co_lotno_idx",
            ),
        ),
    ]
