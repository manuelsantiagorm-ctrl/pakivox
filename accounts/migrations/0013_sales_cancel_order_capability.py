from django.db import migrations

CODE = "sales.cancel_order"


def forwards(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    JobTemplate = apps.get_model("accounts", "JobTemplate")
    capability, _ = Capability.objects.update_or_create(
        code=CODE,
        defaults={
            "name": "Cancelar pedidos de forma segura",
            "description": "Solicitar cancelación segura de pedidos comerciales.",
            "area_code": "sales_service",
            "is_active": True,
        },
    )
    for template in JobTemplate.objects.filter(code__in=["gerente_ventas", "gerente_general"], is_active=True).iterator():
        template.capabilities.add(capability)


def backwards(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    Capability.objects.filter(code=CODE).update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0012_purchase_permissions")]
    operations = [migrations.RunPython(forwards, backwards)]
