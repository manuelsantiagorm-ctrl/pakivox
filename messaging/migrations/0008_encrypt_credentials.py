from django.db import migrations

import messaging.crypto_fields

SENSITIVE_FIELDS = {
    "WhatsAppConfig": (
        "permanent_access_token",
        "webhook_verify_token",
    ),
    "WhatsAppNumber": (
        "twilio_auth_token",
        "meta_access_token",
    ),
}


def encrypt_existing_credentials(apps, schema_editor):
    for model_name, field_names in SENSITIVE_FIELDS.items():
        model = apps.get_model("messaging", model_name)
        for instance in model.objects.all().iterator():
            values = {
                field_name: getattr(instance, field_name) for field_name in field_names
            }
            if any(value not in (None, "") for value in values.values()):
                model.objects.filter(pk=instance.pk).update(**values)


class Migration(migrations.Migration):
    dependencies = [
        ("messaging", "0007_whatsappaiofferdraft"),
    ]

    operations = [
        migrations.AlterField(
            model_name="whatsappconfig",
            name="permanent_access_token",
            field=messaging.crypto_fields.EncryptedTextField(
                blank=True,
                help_text="Campo sensible. No mostrar públicamente ni imprimir en logs.",
                null=True,
                verbose_name="Token permanente de Meta",
            ),
        ),
        migrations.AlterField(
            model_name="whatsappconfig",
            name="webhook_verify_token",
            field=messaging.crypto_fields.EncryptedTextField(
                blank=True,
                null=True,
                verbose_name="Token de verificación del webhook",
            ),
        ),
        migrations.AlterField(
            model_name="whatsappnumber",
            name="twilio_auth_token",
            field=messaging.crypto_fields.EncryptedTextField(
                blank=True,
                default="",
                help_text="Auth Token cifrado. Déjalo vacío al editar para conservar el actual.",
                verbose_name="Twilio Auth Token",
            ),
        ),
        migrations.AlterField(
            model_name="whatsappnumber",
            name="meta_access_token",
            field=messaging.crypto_fields.EncryptedTextField(
                blank=True,
                default="",
                help_text="Token cifrado de Meta Cloud API para este canal.",
                verbose_name="Meta Access Token",
            ),
        ),
        migrations.RunPython(
            encrypt_existing_credentials,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
