from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("shipping_hub", "0016_shipping_purchase_core_phase2")]

    operations = [
        migrations.AddField(
            model_name="shippingpurchase",
            name="request_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="shippingpurchase",
            name="queued_at",
            field=models.DateTimeField(blank=True, null=True, db_index=True),
        ),
        migrations.AddField(
            model_name="shippingpurchase",
            name="processing_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="shippingpurchase",
            name="lease_expires_at",
            field=models.DateTimeField(blank=True, null=True, db_index=True),
        ),
        migrations.AddIndex(
            model_name="shippingpurchase",
            index=models.Index(fields=["status", "queued_at", "id"], name="spurch_queue_order_idx"),
        ),
    ]
