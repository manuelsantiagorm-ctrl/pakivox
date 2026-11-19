import uuid

from django.db import migrations, models


def assign_unique_onboarding_references(apps, schema_editor):
    WhatsAppNumber = apps.get_model("messaging", "WhatsAppNumber")
    database_alias = schema_editor.connection.alias

    for record in WhatsAppNumber.objects.using(database_alias).filter(
        onboarding_reference__isnull=True
    ).iterator():
        record.onboarding_reference = uuid.uuid4()
        record.save(
            using=database_alias,
            update_fields=["onboarding_reference"],
        )


class Migration(migrations.Migration):
    dependencies = [("messaging", "0014_ycloud_message_reconciliation")]

    operations = [
        # Se agrega primero sin UNIQUE ni default para no asignar el mismo UUID
        # a todos los registros que ya existen en SQLite.
        migrations.AddField(
            model_name="whatsappnumber",
            name="onboarding_reference",
            field=models.UUIDField(
                editable=False,
                null=True,
                verbose_name="Folio de incorporación",
            ),
        ),
        migrations.AddField(
            model_name="whatsappnumber",
            name="connection_mode",
            field=models.CharField(
                choices=[
                    ("coexistence", "Conservar WhatsApp Business en el celular"),
                    ("api_only", "Usar el número únicamente en WMS360"),
                ],
                default="coexistence",
                max_length=20,
                verbose_name="Modalidad de conexión",
            ),
        ),
        migrations.AddField(
            model_name="whatsappnumber",
            name="coexistence_consent_at",
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name="Consentimiento para Coexistence",
            ),
        ),
        migrations.AddField(
            model_name="whatsappnumber",
            name="onboarding_public_note",
            field=models.TextField(
                blank=True,
                default="",
                help_text="No guardar secretos, tokens ni datos internos en este campo.",
                verbose_name="Mensaje de seguimiento para el cliente",
            ),
        ),
        migrations.AlterField(
            model_name="whatsappnumber",
            name="ycloud_waba_id",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Identificador validado por WMS360 contra la API central de YCloud.",
                max_length=120,
                verbose_name="YCloud WABA ID",
            ),
        ),
        migrations.RunPython(
            assign_unique_onboarding_references,
            migrations.RunPython.noop,
        ),
        migrations.AlterField(
            model_name="whatsappnumber",
            name="onboarding_reference",
            field=models.UUIDField(
                default=uuid.uuid4,
                editable=False,
                unique=True,
                verbose_name="Folio de incorporación",
            ),
        ),
    ]
