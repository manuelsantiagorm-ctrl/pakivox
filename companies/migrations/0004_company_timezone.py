import companies.models
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("companies", "0003_company_media_profile")]

    operations = [
        migrations.AddField(
            model_name="company",
            name="timezone",
            field=models.CharField(
                default="America/Mexico_City",
                help_text=(
                    "Identificador IANA usado para cerrar días y periodos financieros."
                ),
                max_length=64,
                validators=[companies.models.validate_iana_timezone],
                verbose_name="Zona horaria",
            ),
        ),
    ]
