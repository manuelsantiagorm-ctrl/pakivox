from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("crm", "0001_initial"),
        ("sales", "0005_order_fulfillment_mode"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrderResolutionEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_type", models.CharField(choices=[("cancel_requested", "Cancelación solicitada"), ("cancelled", "Pedido cancelado"), ("resolution_required", "Requiere resolución segura"), ("blocked", "Acción bloqueada")], db_index=True, max_length=40)),
                ("reason_code", models.CharField(choices=[("human_error", "Error humano / operativo"), ("customer_cancelled", "Cliente canceló la compra"), ("duplicate", "Pedido duplicado"), ("data_entry_error", "Error de captura"), ("out_of_stock", "Imposibilidad de surtir"), ("other", "Otro")], max_length=40)),
                ("reason_text", models.TextField(blank=True)),
                ("previous_status", models.CharField(blank=True, max_length=30)),
                ("new_status", models.CharField(blank=True, max_length=30)),
                ("amount_paid_before", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("amount_paid_after", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("balance_due_before", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("balance_due_after", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("idempotency_key", models.CharField(blank=True, max_length=64, null=True, unique=True)),
                ("message", models.CharField(blank=True, max_length=500)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="order_resolution_events", to=settings.AUTH_USER_MODEL)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="order_resolution_events", to="companies.company")),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="order_resolution_events", to="crm.customer")),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="resolution_events", to="sales.order")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddIndex(model_name="orderresolutionevent", index=models.Index(fields=["company", "event_type", "created_at"], name="sales_res_company_evt_idx")),
        migrations.AddIndex(model_name="orderresolutionevent", index=models.Index(fields=["company", "order", "created_at"], name="sales_res_company_ord_idx")),
        migrations.AddIndex(model_name="orderresolutionevent", index=models.Index(fields=["company", "customer", "created_at"], name="sales_res_company_cus_idx")),
    ]
