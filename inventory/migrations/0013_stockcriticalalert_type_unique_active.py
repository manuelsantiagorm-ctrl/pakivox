from django.db import migrations, models
from django.db.models import Q
from django.utils import timezone


def prepare_existing_alerts(apps, schema_editor):
    StockCriticalAlert = apps.get_model(
        "inventory",
        "StockCriticalAlert",
    )

    now = timezone.now()

    active_alerts = StockCriticalAlert.objects.filter(status="active").order_by(
        "company_id",
        "warehouse_id",
        "variant_id",
        "-created_at",
        "-pk",
    )

    retained_keys = set()

    for alert in active_alerts.iterator():
        key = (
            alert.company_id,
            alert.warehouse_id,
            alert.variant_id,
        )

        alert_type = "out_of_stock" if alert.current_available <= 0 else "low_stock"

        if key not in retained_keys:
            retained_keys.add(key)

            StockCriticalAlert.objects.filter(
                pk=alert.pk,
            ).update(
                alert_type=alert_type,
            )

            continue

        StockCriticalAlert.objects.filter(
            pk=alert.pk,
        ).update(
            alert_type=alert_type,
            status="resolved",
            is_read=True,
            resolved_at=now,
            updated_at=now,
        )


class Migration(migrations.Migration):

    dependencies = [
        (
            "inventory",
            "0012_stockmovement_created_by_stockmovement_stock_after_and_more",
        ),
    ]

    operations = [
        migrations.AddField(
            model_name="stockcriticalalert",
            name="alert_type",
            field=models.CharField(
                choices=[
                    ("low_stock", "Stock bajo"),
                    ("out_of_stock", "Sin stock"),
                ],
                default="low_stock",
                max_length=20,
                verbose_name="Tipo de alerta",
            ),
        ),
        migrations.RunPython(
            prepare_existing_alerts,
            migrations.RunPython.noop,
        ),
        migrations.AddConstraint(
            model_name="stockcriticalalert",
            constraint=models.UniqueConstraint(
                condition=Q(status="active"),
                fields=(
                    "company",
                    "warehouse",
                    "variant",
                ),
                name="uniq_active_stock_alert_company_wh_variant",
            ),
        ),
        migrations.AddIndex(
            model_name="stockcriticalalert",
            index=models.Index(
                fields=[
                    "company",
                    "status",
                    "is_read",
                ],
                name="stock_alert_company_status_idx",
            ),
        ),
    ]
