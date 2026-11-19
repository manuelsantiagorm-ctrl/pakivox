import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("shipping_hub", "0012_multi_provider_skydropx_phase6"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="TrackingWebhookReceipt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(db_index=True, max_length=30)),
                ("provider_event_id", models.CharField(max_length=200)),
                ("payload_sha256", models.CharField(max_length=64)),
                ("status", models.CharField(choices=[("received", "Recibido"), ("processed", "Procesado"), ("ignored", "Ignorado"), ("failed", "Fallido")], db_index=True, default="received", max_length=20)),
                ("error_code", models.CharField(blank=True, max_length=100)),
                ("error_message", models.TextField(blank=True)),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                ("processed_at", models.DateTimeField(blank=True, null=True)),
                ("carrier_shipment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="tracking_webhook_receipts", to="shipping_hub.carriershipment")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company")),
                ("integration", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="tracking_webhook_receipts", to="shipping_hub.companyshippingintegration")),
            ],
            options={"ordering": ["-received_at"]},
        ),
        migrations.CreateModel(
            name="TrackingSyncAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("completed", "Completada"), ("failed", "Fallida"), ("skipped", "Omitida")], db_index=True, max_length=20)),
                ("events_created", models.PositiveIntegerField(default=0)),
                ("error_code", models.CharField(blank=True, max_length=100)),
                ("error_message", models.TextField(blank=True)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("carrier_shipment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="tracking_sync_attempts", to="shipping_hub.carriershipment")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company")),
                ("integration", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="tracking_sync_attempts", to="shipping_hub.companyshippingintegration")),
                ("requested_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="requested_tracking_syncs", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-started_at"]},
        ),
        migrations.AddConstraint(
            model_name="trackingwebhookreceipt",
            constraint=models.UniqueConstraint(fields=("integration", "provider_event_id"), name="uniq_tracking_hook_integration_event"),
        ),
        migrations.AddIndex(model_name="trackingwebhookreceipt", index=models.Index(fields=["company", "status"], name="thook_comp_status_idx")),
        migrations.AddIndex(model_name="trackingwebhookreceipt", index=models.Index(fields=["company", "received_at"], name="thook_comp_date_idx")),
        migrations.AddIndex(model_name="trackingsyncattempt", index=models.Index(fields=["company", "started_at"], name="tsync_comp_date_idx")),
    ]
