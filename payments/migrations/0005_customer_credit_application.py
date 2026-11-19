from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("payments", "0004_customer_credit")]

    operations = [
        migrations.CreateModel(
            name="CustomerCreditApplication",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("status", models.CharField(choices=[("applied", "Aplicado"), ("reversed", "Revertido")], db_index=True, default="applied", max_length=20)),
                ("reason", models.TextField()),
                ("reversal_reason", models.TextField(blank=True)),
                ("idempotency_key", models.CharField(max_length=64)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("applied_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("reversed_at", models.DateTimeField(blank=True, null=True)),
                ("applied_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="applied_customer_credits", to=settings.AUTH_USER_MODEL)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="customer_credit_applications", to="companies.company")),
                ("credit", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="applications", to="payments.customercredit")),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="credit_applications", to="crm.customer")),
                ("order", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="customer_credit_applications", to="sales.order")),
                ("reversed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="reversed_customer_credits", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-applied_at", "-id"]},
        ),
        migrations.AddIndex(
            model_name="customercreditapplication",
            index=models.Index(fields=["company", "order", "status"], name="cred_app_comp_order_idx"),
        ),
        migrations.AddIndex(
            model_name="customercreditapplication",
            index=models.Index(fields=["company", "credit", "status"], name="cred_app_comp_cred_idx"),
        ),
        migrations.AddConstraint(
            model_name="customercreditapplication",
            constraint=models.CheckConstraint(condition=models.Q(("amount__gt", 0)), name="cust_credit_app_amount_positive"),
        ),
        migrations.AddConstraint(
            model_name="customercreditapplication",
            constraint=models.UniqueConstraint(fields=("company", "idempotency_key"), name="uniq_credit_app_company_key"),
        ),
    ]
