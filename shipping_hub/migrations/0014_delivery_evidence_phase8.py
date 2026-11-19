import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0015_alter_product_cost_price_and_more"),
        ("shipping_hub", "0013_tracking_center_phase7"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DeliveryEvidence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("result", models.CharField(choices=[("delivered", "Entrega completa"), ("partial", "Entrega parcial"), ("rejected", "Entrega rechazada")], db_index=True, max_length=20)),
                ("receiver_name", models.CharField(blank=True, max_length=180)),
                ("receiver_relationship", models.CharField(blank=True, max_length=120)),
                ("notes", models.TextField(blank=True)),
                ("latitude", models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
                ("longitude", models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True)),
                ("gps_accuracy_m", models.DecimalField(blank=True, decimal_places=2, max_digits=9, null=True)),
                ("location_consent", models.BooleanField(default=False)),
                ("idempotency_key", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("recorded_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("carrier_shipment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="delivery_evidences", to="shipping_hub.carriershipment")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="shipping_delivery_evidences_created", to=settings.AUTH_USER_MODEL)),
                ("warehouse", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="shipping_delivery_evidences", to="inventory.warehouse")),
            ],
            options={"ordering": ["-recorded_at", "-pk"]},
        ),
        migrations.CreateModel(
            name="DeliveryEvidencePackage",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company")),
                ("evidence", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="package_items", to="shipping_hub.deliveryevidence")),
                ("package", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="delivery_evidence_items", to="shipping_hub.shipmentpackage")),
            ],
        ),
        migrations.CreateModel(
            name="DeliveryEvidenceAsset",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("asset_type", models.CharField(choices=[("photo", "Fotografía"), ("signature", "Firma"), ("document", "Documento")], db_index=True, max_length=20)),
                ("file", models.FileField(upload_to="private/delivery_evidence/%Y/%m/")),
                ("original_name", models.CharField(max_length=255)),
                ("content_type", models.CharField(max_length=100)),
                ("size_bytes", models.PositiveBigIntegerField()),
                ("sha256", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company")),
                ("evidence", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="assets", to="shipping_hub.deliveryevidence")),
            ],
            options={"ordering": ["created_at", "pk"]},
        ),
        migrations.AddConstraint(model_name="deliveryevidencepackage", constraint=models.UniqueConstraint(fields=("evidence", "package"), name="uniq_delivery_evidence_package")),
        migrations.AddConstraint(model_name="deliveryevidenceasset", constraint=models.UniqueConstraint(fields=("evidence", "sha256"), name="uniq_delivery_evidence_asset_hash")),
        migrations.AddIndex(model_name="deliveryevidence", index=models.Index(fields=["company", "warehouse", "recorded_at"], name="devid_comp_wh_date_idx")),
        migrations.AddIndex(model_name="deliveryevidence", index=models.Index(fields=["company", "carrier_shipment", "result"], name="devid_comp_ship_res_idx")),
        migrations.AddIndex(model_name="deliveryevidenceasset", index=models.Index(fields=["company", "evidence", "asset_type"], name="devas_comp_ev_type_idx")),
    ]
