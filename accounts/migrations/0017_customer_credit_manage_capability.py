from django.db import migrations

CODE = "payments.customer_credit.manage"


def forwards(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    JobTemplate = apps.get_model("accounts", "JobTemplate")
    capability, _ = Capability.objects.update_or_create(
        code=CODE,
        defaults={
            "name": "Gestionar saldo a favor de clientes",
            "description": (
                "Permite convertir una devolución pendiente en saldo a favor auditable "
                "sin registrar un reembolso ficticio ni duplicar cobros."
            ),
            "area_code": "collections_payments",
            "is_active": True,
        },
    )
    for template in JobTemplate.objects.filter(
        code__in=("supervisor_cobranza", "gerente_financiero", "gerente_general"),
        is_active=True,
    ).iterator():
        template.capabilities.add(capability)


def backwards(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    Capability.objects.filter(code=CODE).update(is_active=False)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0016_sales_commercial_cancel_capability")]
    operations = [migrations.RunPython(forwards, backwards)]
