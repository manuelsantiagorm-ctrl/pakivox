from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("messaging", "0013_ycloud_onboarding")]

    operations = [
        migrations.AddField(
            model_name="message",
            name="ycloud_message_id",
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True, verbose_name="ID interno del mensaje en YCloud"),
        ),
        migrations.AddField(
            model_name="message",
            name="whatsapp_message_id",
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True, verbose_name="WAMID de WhatsApp"),
        ),
        migrations.AddField(
            model_name="message",
            name="provider_external_id",
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True, verbose_name="Referencia externa de WMS360"),
        ),
    ]
