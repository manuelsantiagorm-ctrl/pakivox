from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("shipping_hub", "0009_external_labels_phase3"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(model_name="carriershipment", name="purchase_quote_amount", field=models.DecimalField(blank=True, decimal_places=2, help_text="Importe que el Owner confirmó antes de comprar la guía.", max_digits=12, null=True)),
        migrations.AddField(model_name="carriershipment", name="purchase_currency", field=models.CharField(blank=True, max_length=10)),
        migrations.AddField(model_name="carriershipment", name="purchase_provider", field=models.CharField(blank=True, max_length=30)),
        migrations.AddField(model_name="carriershipment", name="purchase_environment", field=models.CharField(blank=True, max_length=20)),
        migrations.AddField(model_name="carriershipment", name="purchase_confirmed_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="carriershipment", name="purchase_started_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="carriershipment", name="purchase_confirmed_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="confirmed_shipping_purchases", to=settings.AUTH_USER_MODEL)),
    ]
