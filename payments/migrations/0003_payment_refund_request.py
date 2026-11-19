from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0006_customer_contact_name_customer_price_level_and_more"),
        ("payments", "0002_alter_payment_payment_method_and_more"),
        ("sales", "0007_cs1b_recovery_events"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PaymentRefundRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("requested", "Solicitado"), ("confirmed", "Confirmado"), ("rejected", "Rechazado"), ("cancelled", "Cancelado")], db_index=True, default="requested", max_length=20)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("method", models.CharField(choices=[("manual_external", "Devolución externa confirmada manualmente")], default="manual_external", max_length=30)),
                ("reason", models.TextField()),
                ("confirmation_reference", models.CharField(blank=True, max_length=180)),
                ("request_idempotency_key", models.CharField(blank=True, max_length=64, null=True, unique=True)),
                ("confirm_idempotency_key", models.CharField(blank=True, max_length=64, null=True, unique=True)),
                ("requested_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="payment_refund_requests", to="companies.company")),
                ("confirmed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="confirmed_payment_refunds", to=settings.AUTH_USER_MODEL)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payment_refund_requests", to="crm.customer")),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="payment_refund_requests", to="sales.order")),
                ("payment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="refund_requests", to="payments.payment")),
                ("requested_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="requested_payment_refunds", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-requested_at", "-id"]},
        ),
        migrations.AddIndex(
            model_name="paymentrefundrequest",
            index=models.Index(fields=["company", "status", "requested_at"], name="pay_ref_company_status_idx"),
        ),
        migrations.AddIndex(
            model_name="paymentrefundrequest",
            index=models.Index(fields=["company", "payment", "status"], name="pay_ref_company_pay_idx"),
        ),
    ]
