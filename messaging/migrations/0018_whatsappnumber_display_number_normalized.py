from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("messaging", "0017_whatsappcreditmovement_idempotency"),
    ]

    operations = [
        migrations.AddField(
            model_name="whatsappnumber",
            name="display_number_normalized",
            field=models.CharField(
                "Número visible normalizado",
                max_length=30,
                blank=True,
                db_index=True,
                help_text=(
                    "Se calcula automáticamente en save() a partir de "
                    "display_number, para permitir búsquedas O(1) en vez "
                    "de normalizar cada candidato en Python (BUG-066)."
                ),
            ),
        ),
    ]