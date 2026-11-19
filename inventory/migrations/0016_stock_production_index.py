from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0015_alter_product_cost_price_and_more")]
    operations = [
        migrations.AddIndex(
            model_name="stock",
            index=models.Index(
                fields=["company", "variant", "warehouse"],
                name="stock_co_var_wh_idx",
            ),
        ),
    ]
