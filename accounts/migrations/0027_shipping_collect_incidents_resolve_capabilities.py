from django.db import migrations

CODE_COLLECT = "shipping.collect"
CODE_INCIDENTS_RESOLVE = "shipping.incidents.resolve"

TEMPLATE_CODES = (
    "administrador",
    "coordinador_logistico",
    "despachador",
    "activity_shipping",
    "repartidor",
)


def forwards(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    JobTemplate = apps.get_model("accounts", "JobTemplate")

    collect, _ = Capability.objects.update_or_create(
        code=CODE_COLLECT,
        defaults={
            "name": "Recolectar lotes de envío",
            "description": (
                "Permite escanear el punto de acopio, confirmar bultos entregados "
                "a la paquetería y reportar faltantes o rechazos dentro de un lote."
            ),
            "area_code": "logistics_delivery",
            "is_active": True,
        },
    )
    incidents_resolve, _ = Capability.objects.update_or_create(
        code=CODE_INCIDENTS_RESOLVE,
        defaults={
            "name": "Resolver incidencias de envío",
            "description": (
                "Permite resolver incidencias logísticas: reempacar, regenerar "
                "guía, registrar gasto de campo o cancelar el pedido afectado."
            ),
            "area_code": "logistics_delivery",
            "is_active": True,
        },
    )

    templates = JobTemplate.objects.filter(
        code__in=TEMPLATE_CODES,
        is_active=True,
    ).distinct()

    for template in templates.iterator():
        template.capabilities.add(collect, incidents_resolve)


def backwards(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    Capability.objects.filter(
        code__in=(CODE_COLLECT, CODE_INCIDENTS_RESOLVE),
    ).update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0026_ps6p4_operational_read_boundaries")]
    operations = [migrations.RunPython(forwards, backwards)]
