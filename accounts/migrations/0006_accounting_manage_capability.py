from django.db import migrations


def create_accounting_manage_capability(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")

    Capability.objects.update_or_create(
        code="accounting.manage",
        defaults={
            "name": "Gestionar contabilidad",
            "description": (
                "Permite registrar, contabilizar, cancelar y revertir "
                "movimientos contables."
            ),
            "area_code": "finance",
            "is_active": True,
        },
    )


def remove_accounting_manage_capability(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    Capability.objects.filter(code="accounting.manage").delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0005_team_permissions"),
    ]

    operations = [
        migrations.RunPython(
            create_accounting_manage_capability,
            remove_accounting_manage_capability,
        ),
    ]
