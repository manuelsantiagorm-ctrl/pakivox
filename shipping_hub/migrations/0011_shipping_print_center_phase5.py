from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("shipping_hub", "0010_envia_owner_purchase_phase4"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ShippingPrintJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("mode", models.CharField(choices=[("single", "Individual"), ("batch", "Masiva")], max_length=10)),
                ("status", models.CharField(choices=[("created", "Creado"), ("completed", "Completado"), ("cancelled", "Cancelado")], db_index=True, default="created", max_length=15)),
                ("warehouse_id_snapshot", models.PositiveBigIntegerField(db_index=True)),
                ("reason", models.CharField(blank=True, max_length=250)),
                ("item_count", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="shipping_hub_shippingprintjob_records", to="companies.company")),
                ("requested_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="shipping_print_jobs", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.CreateModel(
            name="ShippingPrintItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sequence", models.PositiveIntegerField()),
                ("label_format_snapshot", models.CharField(max_length=10)),
                ("source_snapshot", models.CharField(max_length=20)),
                ("was_reprint", models.BooleanField(default=False)),
                ("previous_print_count", models.PositiveIntegerField(default=0)),
                ("printed_at", models.DateTimeField(auto_now_add=True)),
                ("carrier_shipment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="print_items", to="shipping_hub.carriershipment")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="shipping_hub_shippingprintitem_records", to="companies.company")),
                ("job", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="shipping_hub.shippingprintjob")),
                ("package", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="print_items", to="shipping_hub.shipmentpackage")),
            ],
            options={"ordering": ["sequence", "id"]},
        ),
        migrations.AddIndex(
            model_name="shippingprintjob",
            index=models.Index(fields=["company", "warehouse_id_snapshot", "created_at"], name="printjob_comp_wh_idx"),
        ),
        migrations.AddConstraint(
            model_name="shippingprintitem",
            constraint=models.UniqueConstraint(fields=("job", "package"), name="uniq_print_job_package"),
        ),
        migrations.AddConstraint(
            model_name="shippingprintitem",
            constraint=models.CheckConstraint(condition=models.Q(("sequence__gt", 0)), name="print_item_sequence_positive"),
        ),
    ]
