from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0018_customer_credit_apply_capability")]
    operations = [
        migrations.AddField(
            model_name="pendingcompanyregistration",
            name="accepted_legal_documents",
            field=models.JSONField(default=list, editable=False, verbose_name="Versiones legales aceptadas"),
        ),
        migrations.AddField(
            model_name="pendingcompanyregistration",
            name="registration_user_agent",
            field=models.CharField(blank=True, max_length=500, verbose_name="Navegador del registro"),
        ),
    ]
