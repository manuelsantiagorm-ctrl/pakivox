import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("shipping_hub", "0015_logistics_incidents_phase9"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ShippingPurchase",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("generation", models.PositiveIntegerField(default=1)),
                ("idempotency_key", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("provider", models.CharField(db_index=True, max_length=30)),
                ("environment", models.CharField(blank=True, max_length=20)),
                ("status", models.CharField(choices=[("created", "Creada"), ("queued", "En cola"), ("sending", "Enviando al proveedor"), ("provider_pending", "Pendiente del proveedor"), ("reconciling", "Conciliando"), ("completed", "Completada"), ("failed_final", "Fallo definitivo"), ("cancelled", "Cancelada"), ("manual_review", "Revisión manual")], db_index=True, default="created", max_length=30)),
                ("charge_status", models.CharField(choices=[("unknown", "Desconocido"), ("not_charged", "No cobrado"), ("possible", "Posible cobro"), ("confirmed", "Cobro confirmado"), ("refunded", "Reembolsado")], db_index=True, default="unknown", max_length=20)),
                ("provider_request_id", models.CharField(blank=True, db_index=True, max_length=200)),
                ("provider_purchase_id", models.CharField(blank=True, db_index=True, max_length=200)),
                ("provider_reference", models.CharField(blank=True, db_index=True, max_length=200)),
                ("quoted_amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("charged_amount", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ("currency", models.CharField(default="MXN", max_length=10)),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("next_retry_at", models.DateTimeField(blank=True, db_index=True, null=True)),
                ("last_error_code", models.CharField(blank=True, max_length=100)),
                ("last_error_message", models.TextField(blank=True)),
                ("requested_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("carrier_shipment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchases", to="shipping_hub.carriershipment")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company")),
                ("integration", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchases", to="shipping_hub.companyshippingintegration")),
                ("requested_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="shipping_purchases_requested", to=settings.AUTH_USER_MODEL)),
                ("selected_quote", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="purchases", to="shipping_hub.shippingquote")),
                ("warehouse", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="shipping_purchases", to="inventory.warehouse")),
            ],
            options={"ordering": ["-requested_at", "-pk"]},
        ),
        migrations.CreateModel(
            name="ShippingPurchaseAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("attempt_number", models.PositiveIntegerField()),
                ("correlation_id", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("outcome", models.CharField(choices=[("completed", "Completado"), ("pending", "Pendiente"), ("uncertain", "Resultado incierto"), ("failed_final", "Fallo definitivo")], db_index=True, max_length=20)),
                ("http_status", models.PositiveIntegerField(blank=True, null=True)),
                ("provider_request_id", models.CharField(blank=True, max_length=200)),
                ("provider_purchase_id", models.CharField(blank=True, max_length=200)),
                ("request_payload", models.JSONField(blank=True, default=dict)),
                ("response_payload", models.JSONField(blank=True, default=dict)),
                ("error_code", models.CharField(blank=True, max_length=100)),
                ("error_message", models.TextField(blank=True)),
                ("started_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("duration_ms", models.PositiveIntegerField(blank=True, null=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company")),
                ("purchase", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attempts", to="shipping_hub.shippingpurchase")),
            ],
            options={"ordering": ["-started_at", "-pk"]},
        ),
        migrations.CreateModel(
            name="ShippingPurchaseEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_type", models.CharField(db_index=True, max_length=50)),
                ("from_status", models.CharField(blank=True, max_length=30)),
                ("to_status", models.CharField(blank=True, max_length=30)),
                ("message", models.TextField(blank=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="shipping_purchase_events", to=settings.AUTH_USER_MODEL)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company")),
                ("purchase", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="shipping_hub.shippingpurchase")),
            ],
            options={"ordering": ["-created_at", "-pk"]},
        ),
        migrations.AddConstraint(model_name="shippingpurchase", constraint=models.UniqueConstraint(fields=("company", "carrier_shipment", "generation"), name="uniq_ship_purchase_generation")),
        migrations.AddConstraint(model_name="shippingpurchase", constraint=models.UniqueConstraint(condition=Q(status__in=["created", "queued", "sending", "provider_pending", "reconciling", "manual_review"]), fields=("company", "carrier_shipment"), name="uniq_open_ship_purchase")),
        migrations.AddConstraint(model_name="shippingpurchaseattempt", constraint=models.UniqueConstraint(fields=("purchase", "attempt_number"), name="uniq_ship_purchase_attempt")),
        migrations.AddIndex(model_name="shippingpurchase", index=models.Index(fields=["company", "warehouse", "status"], name="spurch_comp_wh_stat_idx")),
        migrations.AddIndex(model_name="shippingpurchase", index=models.Index(fields=["provider", "status", "next_retry_at"], name="spurch_prov_retry_idx")),
        migrations.AddIndex(model_name="shippingpurchase", index=models.Index(fields=["company", "carrier_shipment"], name="spurch_comp_ship_idx")),
        migrations.AddIndex(model_name="shippingpurchaseattempt", index=models.Index(fields=["company", "purchase", "outcome"], name="spatt_comp_pur_out_idx")),
        migrations.AddIndex(model_name="shippingpurchaseevent", index=models.Index(fields=["company", "purchase", "created_at"], name="spevt_comp_pur_date_idx")),
    ]
