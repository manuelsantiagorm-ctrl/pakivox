from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("shipping_hub", "0006_carriershipment_external_registered_at_and_more"),
        ("warehouse", "0014_warehousetransfer_unique_active_reversal"),
    ]

    operations = [
        migrations.AlterField(
            model_name="carriershipment",
            name="source",
            field=models.CharField(
                choices=[
                    ("automatic", "Guía automática"),
                    ("external", "Guía externa"),
                    ("local", "Entrega propia / local"),
                    ("custom", "Transportista personalizado"),
                    ("direct", "Convenio directo"),
                ],
                db_index=True,
                default="automatic",
                max_length=20,
                verbose_name="Origen del envío",
            ),
        ),
        migrations.AlterField(
            model_name="carriershipment",
            name="provider",
            field=models.CharField(
                choices=[
                    ("envia", "Envia.com"),
                    ("estafeta_direct", "Estafeta — contrato directo"),
                    ("fedex_direct", "FedEx — contrato directo"),
                    ("dhl_direct", "DHL — contrato directo"),
                    ("skydropx", "Skydropx"),
                    ("manual", "Guía externa"),
                    ("local", "Entrega propia / local"),
                    ("custom", "Transportista personalizado"),
                    ("direct", "Convenio directo"),
                ],
                max_length=30,
            ),
        ),
        migrations.CreateModel(
            name="ShippingAuditEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action", models.CharField(db_index=True, max_length=60)),
                ("detail", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="shipping_hub_audit_events", to=settings.AUTH_USER_MODEL)),
                ("carrier_shipment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="audit_events", to="shipping_hub.carriershipment")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="shipping_hub_shippingauditevent_records", to="companies.company")),
                ("shipping_order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="shipping_hub_audit_events", to="warehouse.shippingorder")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddIndex(
            model_name="shippingauditevent",
            index=models.Index(fields=["company", "shipping_order", "created_at"], name="shipaudit_comp_order_idx"),
        ),
    ]
