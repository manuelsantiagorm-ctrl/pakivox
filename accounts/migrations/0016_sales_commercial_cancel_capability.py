from django.db import migrations

CODE = "sales.commercial_cancel"


def forwards(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    JobTemplate = apps.get_model("accounts", "JobTemplate")
    capability, _ = Capability.objects.update_or_create(
        code=CODE,
        defaults={
            "name": "Cancelar comercialmente pedidos pagados",
            "description": (
                "Cerrar de forma segura una venta con dinero recibido, liberar la operación "
                "warehouse permitida y generar solicitudes de reembolso sin marcar dinero como devuelto."
            ),
            "area_code": "sales_service",
            "is_active": True,
        },
    )
    # Acción de alto impacto: por defecto solo gerencia general. El owner conserva acceso implícito.
    for template in JobTemplate.objects.filter(code="gerente_general", is_active=True).iterator():
        template.capabilities.add(capability)


def backwards(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    Capability.objects.filter(code=CODE).update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0015_payment_refund_confirm_capability")]
    operations = [migrations.RunPython(forwards, backwards)]
