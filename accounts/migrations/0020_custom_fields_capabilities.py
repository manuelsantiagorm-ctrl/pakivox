from django.db import migrations


def create_custom_fields_capabilities(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")

    Capability.objects.update_or_create(
        code="custom_fields.view",
        defaults={
            "name": "Consultar campos personalizados",
            "description": (
                "Permite consultar las definiciones de campos "
                "personalizados de la empresa."
            ),
            "area_code": "direction",
            "is_active": True,
        },
    )

    Capability.objects.update_or_create(
        code="custom_fields.manage",
        defaults={
            "name": "Administrar campos personalizados",
            "description": (
                "Permite crear, editar, activar y desactivar "
                "definiciones de campos personalizados de la empresa."
            ),
            "area_code": "direction",
            "is_active": True,
        },
    )


def remove_custom_fields_capabilities(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    Capability.objects.filter(
        code__in=["custom_fields.view", "custom_fields.manage"]
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0019_pending_registration_legal_snapshot"),
    ]

    operations = [
        migrations.RunPython(
            create_custom_fields_capabilities,
            remove_custom_fields_capabilities,
        ),
    ]