from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("accounts", "0019_pending_registration_legal_snapshot"),
        ("companies", "0001_initial"),
    ]
    operations = [
        migrations.CreateModel(
            name="LegalAcceptance",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("document_slug", models.SlugField(max_length=80)),
                ("document_version", models.CharField(max_length=40)),
                ("document_sha256", models.CharField(max_length=64)),
                ("purpose", models.CharField(choices=[("registration", "Registro de empresa")], default="registration", max_length=30)),
                ("accepted_at", models.DateTimeField()),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.CharField(blank=True, max_length=500)),
                ("company", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="legal_acceptances", to="companies.company")),
                ("pending_registration", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="legal_acceptances", to="accounts.pendingcompanyregistration")),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="legal_acceptances", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-accepted_at", "document_slug"]},
        ),
        migrations.AddConstraint(
            model_name="legalacceptance",
            constraint=models.UniqueConstraint(fields=("pending_registration", "document_slug", "document_version", "purpose"), name="legal_unique_pending_acceptance"),
        ),
        migrations.AddIndex(
            model_name="legalacceptance",
            index=models.Index(fields=["company", "document_slug", "-accepted_at"], name="legal_company_doc_idx"),
        ),
    ]
