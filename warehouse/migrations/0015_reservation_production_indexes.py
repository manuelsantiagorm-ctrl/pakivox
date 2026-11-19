from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("warehouse", "0014_warehousetransfer_unique_active_reversal")]
    operations = [
        migrations.AddIndex(
            model_name="variantlocation",
            index=models.Index(
                fields=["company", "variant", "location"],
                name="varloc_co_var_loc_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="orderfulfillment",
            index=models.Index(
                fields=["company", "order", "status", "warehouse"],
                name="fulfill_co_ord_st_wh_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="pickingorder",
            constraint=models.UniqueConstraint(
                fields=("company", "order", "warehouse"),
                condition=models.Q(status__in=("pending", "in_progress")),
                name="uniq_active_pick_order_wh",
            ),
        ),
        migrations.AddIndex(
            model_name="pickingorder",
            index=models.Index(
                fields=["company", "order", "status"],
                name="pick_co_ord_status_idx",
            ),
        ),
    ]
