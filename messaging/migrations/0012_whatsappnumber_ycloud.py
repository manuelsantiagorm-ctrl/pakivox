from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("messaging", "0011_alter_webhookevent_provider_message_id_and_more")]

    operations = [
        migrations.AddField(
            model_name="whatsappnumber",
            name="ycloud_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Activa el transporte central de YCloud para este canal.",
                verbose_name="YCloud activo",
            ),
        ),
        migrations.AlterField(
            model_name="whatsappnumber",
            name="provider",
            field=models.CharField(
                choices=[
                    ("twilio", "Twilio WhatsApp"),
                    ("meta", "Meta WhatsApp Cloud API"),
                    ("ycloud", "YCloud WhatsApp API"),
                ],
                default="twilio",
                help_text="Proveedor usado para enviar/recibir mensajes de este canal.",
                max_length=20,
                verbose_name="Proveedor WhatsApp",
            ),
        ),
    ]
