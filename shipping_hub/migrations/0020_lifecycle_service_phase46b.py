from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("shipping_hub", "0019_secret_manager_phase1"),
    ]

    operations = [
        migrations.CreateModel(
            name="ShippingLifecycleTransition",
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
                    "entity_type",
                    models.CharField(
                        choices=[
                            ("purchase", "Compra de guía"),
                            ("shipment", "Envío"),
                            ("package", "Bulto"),
                            ("event", "Evento de proveedor"),
                        ],
                        db_index=True,
                        max_length=20,
                    ),
                ),
                (
                    "entity_id",
                    models.PositiveBigIntegerField(db_index=True),
                ),
                ("from_state", models.CharField(max_length=30)),
                ("to_state", models.CharField(max_length=30)),
                (
                    "source",
                    models.CharField(
                        db_index=True,
                        max_length=50,
                    ),
                ),
                (
                    "reason",
                    models.CharField(blank=True, max_length=500),
                ),
                (
                    "correlation_id",
                    models.UUIDField(
                        db_index=True,
                        default=uuid.uuid4,
                        editable=False,
                    ),
                ),
                (
                    "idempotent",
                    models.BooleanField(default=False),
                ),
                (
                    "metadata",
                    models.JSONField(blank=True, default=dict),
                ),
                (
                    "occurred_at",
                    models.DateTimeField(
                        auto_now_add=True,
                        db_index=True,
                    ),
                ),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="shipping_lifecycle_transitions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "carrier_shipment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="lifecycle_transitions",
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
                    "provider_event",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="lifecycle_transitions",
                        to="shipping_hub.shippingproviderevent",
                    ),
                ),
                (
                    "purchase",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="lifecycle_transitions",
                        to="shipping_hub.shippingpurchase",
                    ),
                ),
                (
                    "shipment_package",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="lifecycle_transitions",
                        to="shipping_hub.shipmentpackage",
                    ),
                ),
            ],
            options={
                "ordering": ["-occurred_at", "-pk"],
                "indexes": [
                    models.Index(
                        fields=["company", "entity_type", "entity_id"],
                        name="slife_comp_entity_idx",
                    ),
                    models.Index(
                        fields=["source", "occurred_at"],
                        name="slife_source_date_idx",
                    ),
                    models.Index(
                        fields=["company", "occurred_at"],
                        name="slife_comp_date_idx",
                    ),
                ],
            },
        ),
    ]
