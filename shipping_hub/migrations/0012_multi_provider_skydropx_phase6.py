from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("shipping_hub", "0011_shipping_print_center_phase5")]

    operations = [
        migrations.AddField(
            model_name="companyshippingintegration",
            name="priority",
            field=models.PositiveSmallIntegerField(default=100, help_text="Orden multi-proveedor; el número menor se consulta primero."),
        ),
        migrations.AddField(
            model_name="companyshippingintegration",
            name="allow_quote_fallback",
            field=models.BooleanField(default=True, help_text="Continúa con otro agregador si esta cotización falla."),
        ),
        migrations.AddField(
            model_name="shippingquote",
            name="integration",
            field=models.ForeignKey(blank=True, help_text="Cuenta exacta que originó la tarifa.", null=True, on_delete=django.db.models.deletion.PROTECT, related_name="shipping_quotes", to="shipping_hub.companyshippingintegration"),
        ),
        # Normaliza el estado de los ForeignKey heredados desde el modelo
        # abstracto CompanyScopedModel. Estas operaciones son compatibles con
        # instalaciones existentes y evitan una migración 0013 fantasma.
        migrations.AlterField(
            model_name="customcarrier",
            name="company",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company"),
        ),
        migrations.AlterField(
            model_name="localdeliveryassignment",
            name="company",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company"),
        ),
        migrations.AlterField(
            model_name="localdeliverydriver",
            name="company",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company"),
        ),
        migrations.AlterField(
            model_name="localdeliveryroute",
            name="company",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company"),
        ),
        migrations.AlterField(
            model_name="localdeliveryvehicle",
            name="company",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company"),
        ),
        migrations.AlterField(
            model_name="shippingauditevent",
            name="company",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company"),
        ),
        migrations.AlterField(
            model_name="shippingprintitem",
            name="company",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company"),
        ),
        migrations.AlterField(
            model_name="shippingprintjob",
            name="company",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="%(app_label)s_%(class)s_records", to="companies.company"),
        ),
    ]
