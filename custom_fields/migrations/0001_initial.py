import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ("companies", "0003_company_media_profile"),
        ("crm", "0008_customeraddress_municipality"),
    ]

    operations = [
        migrations.CreateModel(
            name="CustomFieldDefinition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, verbose_name="Nombre")),
                ("slug", models.SlugField(max_length=120, verbose_name="Clave interna")),
                ("field_type", models.CharField(choices=[("text", "Texto"), ("number", "Número"), ("date", "Fecha"), ("boolean", "Sí / No"), ("select", "Lista de opciones"), ("email", "Correo"), ("phone", "Teléfono")], default="text", max_length=20, verbose_name="Tipo de campo")),
                ("options", models.JSONField(blank=True, default=list, help_text="Solo se usa para campos de lista.", verbose_name="Opciones")),
                ("help_text", models.CharField(blank=True, default="", max_length=250, verbose_name="Texto de ayuda")),
                ("required", models.BooleanField(default=False, verbose_name="Obligatorio")),
                ("show_in_form", models.BooleanField(default=True, verbose_name="Mostrar al editar clientes")),
                ("allow_import", models.BooleanField(default=True, verbose_name="Permitir en importaciones")),
                ("allow_export", models.BooleanField(default=True, verbose_name="Permitir en exportaciones")),
                ("is_active", models.BooleanField(default=True, verbose_name="Activo")),
                ("order", models.PositiveIntegerField(default=0, verbose_name="Orden")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="custom_field_definitions", to="companies.company")),
            ],
            options={"ordering": ["order", "name", "id"]},
        ),
        migrations.CreateModel(
            name="CustomerCustomFieldValue",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("value", models.JSONField(blank=True, null=True, verbose_name="Valor")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="customer_custom_field_values", to="companies.company")),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="custom_field_values", to="crm.customer")),
                ("definition", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="customer_values", to="custom_fields.customfielddefinition")),
            ],
        ),
        migrations.AddConstraint(
            model_name="customfielddefinition",
            constraint=models.UniqueConstraint(fields=("company", "slug"), name="unique_custom_field_slug_company"),
        ),
        migrations.AddConstraint(
            model_name="customfielddefinition",
            constraint=models.UniqueConstraint(fields=("company", "name"), name="unique_custom_field_name_company"),
        ),
        migrations.AddIndex(
            model_name="customfielddefinition",
            index=models.Index(fields=["company", "is_active", "order"], name="custom_field_company_idx"),
        ),
        migrations.AddConstraint(
            model_name="customercustomfieldvalue",
            constraint=models.UniqueConstraint(fields=("customer", "definition"), name="unique_customer_custom_field_value"),
        ),
        migrations.AddIndex(
            model_name="customercustomfieldvalue",
            index=models.Index(fields=["company", "customer"], name="custom_value_company_idx"),
        ),
    ]

