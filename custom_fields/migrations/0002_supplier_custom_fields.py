from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("custom_fields", "0001_initial"),
        ("purchases", "0003_purchaseorderalertlink"),
    ]

    operations = [
        migrations.AddField(
            model_name="customfielddefinition",
            name="module",
            field=models.CharField(
                choices=[("customer", "Clientes"), ("supplier", "Proveedores")],
                default="customer",
                max_length=30,
                verbose_name="Módulo",
            ),
        ),
        migrations.RemoveConstraint(
            model_name="customfielddefinition",
            name="unique_custom_field_slug_company",
        ),
        migrations.RemoveConstraint(
            model_name="customfielddefinition",
            name="unique_custom_field_name_company",
        ),
        migrations.AddConstraint(
            model_name="customfielddefinition",
            constraint=models.UniqueConstraint(
                fields=("company", "module", "slug"),
                name="unique_custom_field_slug_module",
            ),
        ),
        migrations.AddConstraint(
            model_name="customfielddefinition",
            constraint=models.UniqueConstraint(
                fields=("company", "module", "name"),
                name="unique_custom_field_name_module",
            ),
        ),
        migrations.CreateModel(
            name="SupplierCustomFieldValue",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("value", models.JSONField(blank=True, null=True, verbose_name="Valor")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="supplier_custom_field_values", to="companies.company")),
                ("definition", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="supplier_values", to="custom_fields.customfielddefinition")),
                ("supplier", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="custom_field_values", to="purchases.supplier")),
            ],
        ),
        migrations.AddConstraint(
            model_name="suppliercustomfieldvalue",
            constraint=models.UniqueConstraint(
                fields=("supplier", "definition"),
                name="unique_supplier_custom_field_value",
            ),
        ),
        migrations.AddIndex(
            model_name="suppliercustomfieldvalue",
            index=models.Index(
                fields=["company", "supplier"],
                name="supplier_value_company_idx",
            ),
        ),
    ]
