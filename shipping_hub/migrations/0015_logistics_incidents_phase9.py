import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0015_alter_product_cost_price_and_more"),
        ("shipping_hub", "0014_delivery_evidence_phase8"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.CreateModel(name="LogisticsIncident", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("incident_type", models.CharField(choices=[("lost", "Extraviado"), ("damaged", "Dañado"), ("held", "Retenido"), ("failed_delivery", "Entrega fallida"), ("delay", "Retraso"), ("other", "Otro")], db_index=True, max_length=30)),
            ("severity", models.CharField(choices=[("low", "Baja"), ("medium", "Media"), ("high", "Alta"), ("critical", "Crítica")], db_index=True, default="medium", max_length=12)),
            ("status", models.CharField(choices=[("open", "Abierta"), ("investigating", "En investigación"), ("escalated", "Escalada"), ("resolved", "Resuelta"), ("cancelled", "Cancelada")], db_index=True, default="open", max_length=20)),
            ("title", models.CharField(max_length=180)), ("description", models.TextField()),
            ("provider_reference", models.CharField(blank=True, max_length=200)), ("resolution", models.TextField(blank=True)),
            ("idempotency_key", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
            ("opened_at", models.DateTimeField(auto_now_add=True, db_index=True)), ("resolved_at", models.DateTimeField(blank=True, null=True)), ("updated_at", models.DateTimeField(auto_now=True)),
            ("assigned_to", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="shipping_incidents_assigned", to=settings.AUTH_USER_MODEL)),
            ("carrier_shipment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="logistics_incidents", to="shipping_hub.carriershipment")),
            ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company")),
            ("opened_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="shipping_incidents_opened", to=settings.AUTH_USER_MODEL)),
            ("package", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="logistics_incidents", to="shipping_hub.shipmentpackage")),
            ("warehouse", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="shipping_incidents", to="inventory.warehouse")),
        ], options={"ordering": ["-opened_at", "-pk"]}),
        migrations.CreateModel(name="LogisticsIncidentEvent", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("event_type", models.CharField(max_length=40)),
            ("from_status", models.CharField(blank=True, max_length=20)), ("to_status", models.CharField(blank=True, max_length=20)), ("notes", models.TextField(blank=True)), ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
            ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="shipping_incident_events", to=settings.AUTH_USER_MODEL)),
            ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company")),
            ("incident", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="events", to="shipping_hub.logisticsincident")),
        ], options={"ordering": ["-created_at", "-pk"]}),
        migrations.CreateModel(name="ShipmentReturn", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("return_type", models.CharField(choices=[("total", "Devolución total"), ("partial", "Devolución parcial")], max_length=12)),
            ("status", models.CharField(choices=[("requested", "Solicitada"), ("authorized", "Autorizada"), ("in_transit", "En tránsito"), ("received", "Recibida"), ("rejected", "Rechazada"), ("cancelled", "Cancelada")], db_index=True, default="requested", max_length=20)),
            ("reason", models.TextField()), ("package_ids", models.JSONField(blank=True, default=list)), ("provider_reference", models.CharField(blank=True, max_length=200)), ("requested_at", models.DateTimeField(auto_now_add=True, db_index=True)), ("received_at", models.DateTimeField(blank=True, null=True)), ("updated_at", models.DateTimeField(auto_now=True)),
            ("carrier_shipment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="returns", to="shipping_hub.carriershipment")), ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company")),
            ("incident", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="returns", to="shipping_hub.logisticsincident")), ("requested_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="shipping_returns_requested", to=settings.AUTH_USER_MODEL)), ("warehouse", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="shipping_returns", to="inventory.warehouse")),
        ], options={"ordering": ["-requested_at", "-pk"]}),
        migrations.CreateModel(name="DeliveryRetry", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("attempt_number", models.PositiveIntegerField()), ("scheduled_for", models.DateTimeField(db_index=True)),
            ("status", models.CharField(choices=[("scheduled", "Programado"), ("dispatched", "En reparto"), ("delivered", "Entregado"), ("failed", "Fallido"), ("cancelled", "Cancelado")], db_index=True, default="scheduled", max_length=20)), ("instructions", models.TextField(blank=True)), ("created_at", models.DateTimeField(auto_now_add=True)), ("updated_at", models.DateTimeField(auto_now=True)),
            ("carrier_shipment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="delivery_retries", to="shipping_hub.carriershipment")), ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company")), ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="shipping_delivery_retries_created", to=settings.AUTH_USER_MODEL)), ("incident", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="delivery_retries", to="shipping_hub.logisticsincident")), ("warehouse", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="shipping_delivery_retries", to="inventory.warehouse")),
        ], options={"ordering": ["-scheduled_for", "-pk"]}),
        migrations.CreateModel(name="ShippingReconciliation", fields=[
            ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("status", models.CharField(choices=[("pending", "Pendiente"), ("matched", "Conciliado"), ("mismatch", "Con diferencia"), ("resolved", "Resuelto")], db_index=True, default="pending", max_length=20)),
            ("expected_status", models.CharField(blank=True, max_length=30)), ("provider_status", models.CharField(blank=True, max_length=100)), ("expected_cost", models.DecimalField(decimal_places=2, default=0, max_digits=12)), ("provider_cost", models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)), ("currency", models.CharField(default="MXN", max_length=10)), ("differences", models.JSONField(blank=True, default=dict)), ("resolution_notes", models.TextField(blank=True)), ("checked_at", models.DateTimeField(auto_now_add=True, db_index=True)), ("resolved_at", models.DateTimeField(blank=True, null=True)),
            ("carrier_shipment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="reconciliations", to="shipping_hub.carriershipment")), ("checked_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="shipping_reconciliations_checked", to=settings.AUTH_USER_MODEL)), ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company")), ("warehouse", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="shipping_reconciliations", to="inventory.warehouse")),
        ], options={"ordering": ["-checked_at", "-pk"]}),
        migrations.AddConstraint(model_name="deliveryretry", constraint=models.UniqueConstraint(fields=("carrier_shipment", "attempt_number"), name="uniq_delivery_retry_attempt")),
        migrations.AddIndex(model_name="logisticsincident", index=models.Index(fields=["company", "warehouse", "status"], name="linc_comp_wh_status_idx")),
        migrations.AddIndex(model_name="logisticsincident", index=models.Index(fields=["company", "carrier_shipment", "status"], name="linc_comp_ship_stat_idx")),
        migrations.AddIndex(model_name="shipmentreturn", index=models.Index(fields=["company", "warehouse", "status"], name="sret_comp_wh_status_idx")),
        migrations.AddIndex(model_name="shippingreconciliation", index=models.Index(fields=["company", "warehouse", "status"], name="srec_comp_wh_status_idx")),
    ]
