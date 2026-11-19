from django.db import migrations, models
from django.db.models import F


def backfill_confirmation_dates(apps, schema_editor):
    """Reconstruye fechas históricas sin presentarlas como fechas exactas."""
    Order = apps.get_model("sales", "Order")
    (
        Order.objects.filter(confirmed_at__isnull=True)
        .exclude(status="draft")
        .update(
            confirmed_at=F("created_at"),
            confirmation_date_is_inferred=True,
        )
    )


class Migration(migrations.Migration):
    dependencies = [("sales", "0009_cs1d_duplicate_order_detector")]

    operations = [
        migrations.AddField(
            model_name="order",
            name="confirmed_at",
            field=models.DateTimeField(
                blank=True,
                db_index=True,
                help_text=(
                    "Momento inmutable en que el pedido dejó de ser borrador."
                ),
                null=True,
                verbose_name="Fecha de confirmación comercial",
            ),
        ),
        migrations.AddField(
            model_name="order",
            name="confirmation_date_is_inferred",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text=(
                    "Indica que la fecha proviene de una reconstrucción histórica."
                ),
                verbose_name="Fecha de confirmación inferida",
            ),
        ),
        migrations.RunPython(
            backfill_confirmation_dates,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
