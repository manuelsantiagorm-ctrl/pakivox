from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("warehouse", "0013_warehousetransferitem_rejected_quantity_and_more"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="warehousetransfer",
            constraint=models.UniqueConstraint(
                fields=("company", "reversal_of"),
                condition=(
                    models.Q(reversal_of__isnull=False) & ~models.Q(status="cancelled")
                ),
                name="wh_transfer_unique_active_reversal",
            ),
        ),
    ]
