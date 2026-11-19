from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0007_alter_customeraddress_options_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="customeraddress",
            name="municipality",
            field=models.CharField(
                blank=True,
                default="",
                max_length=150,
                verbose_name="Municipio o alcaldía",
            ),
        ),
    ]
