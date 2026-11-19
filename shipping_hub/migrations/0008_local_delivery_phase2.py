from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("inventory", "0015_alter_product_cost_price_and_more"),
        ("shipping_hub", "0007_shipping_hub_universal_core"),
    ]
    operations = [
        migrations.CreateModel(
            name="LocalDeliveryDriver",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=150)),
                ("phone", models.CharField(blank=True, max_length=30)),
                ("employee_number", models.CharField(blank=True, max_length=60)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="shipping_hub_localdeliverydriver_records", to="companies.company")),
            ], options={"ordering": ["name", "id"]},
        ),
        migrations.CreateModel(
            name="LocalDeliveryVehicle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("plate", models.CharField(max_length=30)),
                ("vehicle_type", models.CharField(blank=True, max_length=80)),
                ("max_weight_kg", models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ("is_active", models.BooleanField(db_index=True, default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="shipping_hub_localdeliveryvehicle_records", to="companies.company")),
            ], options={"ordering": ["name", "plate"]},
        ),
        migrations.CreateModel(
            name="LocalDeliveryRoute",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=150)),
                ("scheduled_date", models.DateField(db_index=True)),
                ("status", models.CharField(choices=[("planned", "Programada"), ("loading", "Cargando"), ("in_progress", "En ruta"), ("completed", "Completada"), ("cancelled", "Cancelada")], db_index=True, default="planned", max_length=20)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="shipping_hub_localdeliveryroute_records", to="companies.company")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_local_delivery_routes", to=settings.AUTH_USER_MODEL)),
                ("driver", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="routes", to="shipping_hub.localdeliverydriver")),
                ("vehicle", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="routes", to="shipping_hub.localdeliveryvehicle")),
                ("warehouse", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="local_routes", to="inventory.warehouse")),
            ], options={"ordering": ["scheduled_date", "name", "id"]},
        ),
        migrations.CreateModel(
            name="LocalDeliveryAssignment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("stop_order", models.PositiveIntegerField(default=1)),
                ("status", models.CharField(choices=[("ready", "Listo para carga"), ("loaded", "Cargado"), ("in_transit", "En ruta"), ("failed_attempt", "Intento fallido"), ("delivered", "Entregado")], db_index=True, default="ready", max_length=25)),
                ("loaded_at", models.DateTimeField(blank=True, null=True)),
                ("departed_at", models.DateTimeField(blank=True, null=True)),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
                ("failed_attempts", models.PositiveIntegerField(default=0)),
                ("last_failure_reason", models.CharField(blank=True, max_length=300)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("carrier_shipment", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="local_assignment", to="shipping_hub.carriershipment")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="shipping_hub_localdeliveryassignment_records", to="companies.company")),
                ("route", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="assignments", to="shipping_hub.localdeliveryroute")),
            ], options={"ordering": ["route", "stop_order", "id"]},
        ),
        migrations.AddConstraint(model_name="localdeliverydriver", constraint=models.UniqueConstraint(condition=~models.Q(employee_number=""), fields=("company", "employee_number"), name="uniq_driver_employee_company")),
        migrations.AddConstraint(model_name="localdeliveryvehicle", constraint=models.UniqueConstraint(fields=("company", "plate"), name="uniq_vehicle_plate_company")),
        migrations.AddConstraint(model_name="localdeliveryroute", constraint=models.UniqueConstraint(fields=("company", "warehouse", "scheduled_date", "name"), name="uniq_local_route_day_name")),
        migrations.AddConstraint(model_name="localdeliveryassignment", constraint=models.CheckConstraint(condition=models.Q(stop_order__gt=0), name="local_stop_order_positive")),
    ]
