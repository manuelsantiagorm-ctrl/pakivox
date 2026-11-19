from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("shipping_hub", "0017_shipping_purchase_queue_phase3"),
    ]

    operations = [
        migrations.CreateModel(
            name="ShippingProviderEvent",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "provider",
                    models.CharField(db_index=True, max_length=50),
                ),
                (
                    "environment",
                    models.CharField(
                        db_index=True,
                        default="production",
                        max_length=20,
                    ),
                ),
                (
                    "provider_event_id",
                    models.CharField(max_length=255),
                ),
                (
                    "deduplication_key",
                    models.CharField(max_length=64),
                ),
                (
                    "event_type",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        max_length=100,
                    ),
                ),
                (
                    "provider_purchase_id",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        max_length=200,
                    ),
                ),
                (
                    "tracking_number",
                    models.CharField(
                        blank=True,
                        db_index=True,
                        max_length=100,
                    ),
                ),
                (
                    "payload",
                    models.JSONField(blank=True, default=dict),
                ),
                (
                    "sanitized_headers",
                    models.JSONField(blank=True, default=dict),
                ),
                (
                    "payload_sha256",
                    models.CharField(max_length=64),
                ),
                (
                    "signature_status",
                    models.CharField(
                        choices=[
                            ("valid", "Firma válida"),
                            ("invalid", "Firma inválida"),
                            ("not_provided", "Firma no proporcionada"),
                            ("not_supported", "Proveedor sin firma compatible"),
                        ],
                        db_index=True,
                        default="not_provided",
                        max_length=20,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("received", "Recibido"),
                            ("processing", "Procesando"),
                            ("processed", "Procesado"),
                            ("retry", "Reintento pendiente"),
                            ("dead_letter", "Revisión manual"),
                            ("ignored", "Ignorado"),
                        ],
                        db_index=True,
                        default="received",
                        max_length=20,
                    ),
                ),
                (
                    "processing_attempts",
                    models.PositiveIntegerField(default=0),
                ),
                (
                    "next_retry_at",
                    models.DateTimeField(
                        blank=True,
                        db_index=True,
                        null=True,
                    ),
                ),
                (
                    "processing_started_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "lease_expires_at",
                    models.DateTimeField(
                        blank=True,
                        db_index=True,
                        null=True,
                    ),
                ),
                (
                    "processed_at",
                    models.DateTimeField(blank=True, null=True),
                ),
                (
                    "error_code",
                    models.CharField(blank=True, max_length=100),
                ),
                (
                    "error_message",
                    models.TextField(blank=True),
                ),
                (
                    "received_at",
                    models.DateTimeField(auto_now_add=True, db_index=True),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True),
                ),
                (
                    "carrier_shipment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="provider_events",
                        to="shipping_hub.carriershipment",
                    ),
                ),
                (
                    "company",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="%(app_label)s_%(class)s_records",
                        to="companies.company",
                    ),
                ),
                (
                    "integration",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="provider_events",
                        to="shipping_hub.companyshippingintegration",
                    ),
                ),
                (
                    "purchase",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="provider_events",
                        to="shipping_hub.shippingpurchase",
                    ),
                ),
            ],
            options={
                "ordering": ["received_at", "pk"],
                "indexes": [
                    models.Index(
                        fields=["status", "next_retry_at", "id"],
                        name="spevent_retry_queue_idx",
                    ),
                    models.Index(
                        fields=["company", "provider", "received_at"],
                        name="spevent_comp_prov_date_idx",
                    ),
                    models.Index(
                        fields=["provider", "provider_purchase_id"],
                        name="spevent_prov_purchase_idx",
                    ),
                    models.Index(
                        fields=["provider", "tracking_number"],
                        name="spevent_prov_track_idx",
                    ),
                    models.Index(
                        fields=["status", "lease_expires_at"],
                        name="spevent_status_lease_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=(
                            "company",
                            "provider",
                            "environment",
                            "provider_event_id",
                        ),
                        name="uniq_ship_provider_event",
                    ),
                    models.UniqueConstraint(
                        fields=(
                            "company",
                            "provider",
                            "environment",
                            "deduplication_key",
                        ),
                        name="uniq_ship_provider_dedupe",
                    ),
                ],
            },
        ),
    ]
