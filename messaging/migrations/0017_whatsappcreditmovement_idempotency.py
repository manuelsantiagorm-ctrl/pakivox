from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("messaging", "0016_onboarding_status_event"),
    ]

    operations = [
        migrations.AddField(
            model_name="whatsappcreditmovement",
            name="idempotency_key",
            field=models.CharField(
                "Clave idempotente",
                max_length=180,
                blank=True,
                null=True,
                help_text=(
                    "Identifica de forma única esta recarga para evitar "
                    "que un doble clic o un reenvío de formulario la "
                    "procese dos veces."
                ),
            ),
        ),
        migrations.AddConstraint(
            model_name="whatsappcreditmovement",
            constraint=models.UniqueConstraint(
                fields=["company", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="uniq_whatsappcreditmovement_co_idempotency",
            ),
        ),
    ]