from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [("messaging", "0012_whatsappnumber_ycloud")]

    operations = [
        migrations.AddField(
            model_name="whatsappnumber", name="ycloud_waba_id",
            field=models.CharField(blank=True, default="", max_length=120, verbose_name="YCloud WABA ID"),
        ),
        migrations.AddField(
            model_name="whatsappnumber", name="ycloud_status",
            field=models.CharField(blank=True, default="", max_length=40, verbose_name="Estado remoto YCloud"),
        ),
        migrations.AddField(
            model_name="whatsappnumber", name="ycloud_last_synced_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Última validación YCloud"),
        ),
        migrations.AddConstraint(
            model_name="whatsappnumber",
            constraint=models.UniqueConstraint(
                fields=("display_number",), condition=Q(provider="ycloud"),
                name="uniq_ycloud_display_number",
            ),
        ),
    ]
