from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("payments", "0003_payment_refund_request"),
    ]

    operations = [
        migrations.AlterField(
            model_name="paymentrefundrequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("requested", "Solicitado"),
                    ("confirmed", "Confirmado"),
                    ("rejected", "Rechazado"),
                    ("cancelled", "Cancelado"),
                    ("converted_to_credit", "Convertido a saldo a favor"),
                ],
                db_index=True,
                default="requested",
                max_length=20,
            ),
        ),
        migrations.CreateModel(
            name="CustomerCredit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("remaining_amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("status", models.CharField(choices=[("active", "Disponible"), ("exhausted", "Agotado"), ("cancelled", "Cancelado")], db_index=True, default="active", max_length=20)),
                ("reason", models.TextField()),
                ("idempotency_key", models.CharField(blank=True, max_length=64, null=True, unique=True)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="customer_credits", to="companies.company")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_customer_credits", to=settings.AUTH_USER_MODEL)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="credits", to="crm.customer")),
                ("source_order", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="customer_credits", to="sales.order")),
                ("source_payment", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="customer_credits", to="payments.payment")),
                ("source_refund_request", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="customer_credit", to="payments.paymentrefundrequest")),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddIndex(
            model_name="customercredit",
            index=models.Index(fields=["company", "customer", "status"], name="cust_credit_comp_cust_idx"),
        ),
        migrations.AddIndex(
            model_name="customercredit",
            index=models.Index(fields=["company", "source_payment"], name="cust_credit_comp_pay_idx"),
        ),
        migrations.AddConstraint(
            model_name="customercredit",
            constraint=models.CheckConstraint(condition=models.Q(("amount__gt", 0)), name="cust_credit_amount_positive"),
        ),
        migrations.AddConstraint(
            model_name="customercredit",
            constraint=models.CheckConstraint(condition=models.Q(("remaining_amount__gte", 0)), name="cust_credit_remaining_nonnegative"),
        ),
        migrations.AddConstraint(
            model_name="customercredit",
            constraint=models.CheckConstraint(condition=models.Q(("remaining_amount__lte", models.F("amount"))), name="cust_credit_remaining_lte_amount"),
        ),
    ]
