from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("shipping_hub", "0018_shipping_provider_event_phase44"),
    ]

    operations = [
        migrations.CreateModel(
            name="IntegrationSecret",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.SlugField(max_length=100)),
                ("kind", models.CharField(choices=[
                    ("webhook_secret", "Secreto de webhook"),
                    ("api_key", "API key"),
                    ("access_token", "Access token"),
                    ("refresh_token", "Refresh token"),
                    ("client_secret", "Client secret"),
                    ("private_key", "Llave privada"),
                    ("certificate", "Certificado"),
                    ("other", "Otro"),
                ], db_index=True, max_length=30)),
                ("purpose", models.CharField(blank=True, max_length=250)),
                ("is_enabled", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_shipping_secrets", to=settings.AUTH_USER_MODEL)),
                ("integration", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="managed_secrets", to="shipping_hub.companyshippingintegration")),
            ],
            options={
                "ordering": ["company_id", "integration_id", "name"],
                "indexes": [
                    models.Index(fields=["company", "integration", "kind"], name="intsecret_comp_int_kind_idx"),
                    models.Index(fields=["is_enabled", "kind"], name="intsecret_enabled_kind_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("company", "integration", "name"), name="uniq_integration_secret_name"),
                ],
            },
        ),
        migrations.CreateModel(
            name="IntegrationSecretVersion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.PositiveIntegerField()),
                ("encrypted_value", models.TextField()),
                ("fingerprint_sha256", models.CharField(db_index=True, max_length=64)),
                ("masked_hint", models.CharField(blank=True, max_length=32)),
                ("status", models.CharField(choices=[("active", "Activa"), ("revoked", "Revocada"), ("superseded", "Sustituida")], db_index=True, default="active", max_length=20)),
                ("rotation_reason", models.CharField(blank=True, max_length=500)),
                ("activated_at", models.DateTimeField(auto_now_add=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_shipping_secret_versions", to=settings.AUTH_USER_MODEL)),
                ("revoked_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="revoked_shipping_secret_versions", to=settings.AUTH_USER_MODEL)),
                ("secret", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="versions", to="shipping_hub.integrationsecret")),
            ],
            options={
                "ordering": ["secret_id", "-version"],
                "indexes": [
                    models.Index(fields=["company", "secret", "status"], name="intsecver_comp_sec_st_idx"),
                    models.Index(fields=["status", "activated_at"], name="intsecver_status_date_idx"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("secret", "version"), name="uniq_integration_secret_version"),
                    models.UniqueConstraint(condition=Q(("status", "active")), fields=("secret",), name="uniq_active_integration_secret"),
                ],
            },
        ),
        migrations.CreateModel(
            name="IntegrationSecretAuditEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(choices=[
                    ("created", "Creado"),
                    ("rotated", "Rotado"),
                    ("revoked", "Revocado"),
                    ("read", "Consultado"),
                    ("legacy_synced", "Sincronizado con campo heredado"),
                ], db_index=True, max_length=30)),
                ("reason", models.CharField(blank=True, max_length=500)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("occurred_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company")),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="shipping_secret_audit_events", to=settings.AUTH_USER_MODEL)),
                ("secret", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="audit_events", to="shipping_hub.integrationsecret")),
                ("version", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="audit_events", to="shipping_hub.integrationsecretversion")),
            ],
            options={
                "ordering": ["-occurred_at", "-pk"],
                "indexes": [
                    models.Index(fields=["company", "secret", "occurred_at"], name="intsecaudit_comp_secret_idx"),
                    models.Index(fields=["action", "occurred_at"], name="intsecaudit_action_date_idx"),
                ],
            },
        ),
    ]
