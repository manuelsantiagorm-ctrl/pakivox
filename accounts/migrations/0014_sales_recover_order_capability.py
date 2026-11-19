from django.db import migrations

CODE = "sales.recover_order"


def forwards(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    JobTemplate = apps.get_model("accounts", "JobTemplate")

    capability, _ = Capability.objects.update_or_create(
        code=CODE,
        defaults={
            "name": "Recuperar pedidos por error humano",
            "description": (
                "Recuperar de forma segura el mismo pedido pagado, "
                "sin duplicar cobros ni reservas."
            ),
            "area_code": "sales_service",
            "is_active": True,
        },
    )

    # Recuperar un pedido pagado puede volver a reservar mercancía.
    # Se concede solo a gerencia general/ventas; owner conserva acceso implícito.
    for template in JobTemplate.objects.filter(
        code__in=["gerente_ventas", "gerente_general"],
        is_active=True,
    ).iterator():
        template.capabilities.add(capability)


def backwards(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    Capability.objects.filter(code=CODE).update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0013_sales_cancel_order_capability"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
