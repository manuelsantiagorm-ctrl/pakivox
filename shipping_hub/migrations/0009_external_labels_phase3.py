from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("shipping_hub", "0008_local_delivery_phase2"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CustomCarrier",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=150)),
                ("contact_name", models.CharField(blank=True, max_length=150)),
                ("phone", models.CharField(blank=True, max_length=30)),
                ("tracking_url_template", models.URLField(blank=True, max_length=1000)),
                ("notes", models.TextField(blank=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="shipping_hub_customcarrier_records", to="companies.company")),
            ],
            options={"ordering": ["name", "id"]},
        ),
        migrations.AddConstraint(
            model_name="customcarrier",
            constraint=models.UniqueConstraint(fields=("company", "name"), name="uniq_custom_carrier_company"),
        ),
        migrations.AddField(model_name="shipmentpackage", name="external_label_original_name", field=models.CharField(blank=True, max_length=255)),
        migrations.AddField(model_name="shipmentpackage", name="external_label_content_type", field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name="shipmentpackage", name="external_registered_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="shipmentpackage", name="external_registered_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="registered_external_package_labels", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="shipmentpackage", name="print_count", field=models.PositiveIntegerField(default=0)),
        migrations.AlterField(
            model_name="shipmentpackage", name="label_format",
            field=models.CharField(choices=[("pdf", "PDF"), ("png", "PNG"), ("zpl", "ZPL")], default="pdf", max_length=10),
        ),
    ]
