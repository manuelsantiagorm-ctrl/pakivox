from django.db import migrations

CODE = "payments.customer_credit.apply"


def forwards(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    JobTemplate = apps.get_model("accounts", "JobTemplate")
    capability, _ = Capability.objects.update_or_create(
        code=CODE,
        defaults={
            "name": "Aplicar saldo a favor a pedidos",
            "description": (
                "Permite usar saldo a favor existente para cubrir pedidos del mismo "
                "cliente sin registrar un cobro nuevo ni fabricar Payments."
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
    dependencies = [("accounts", "0017_customer_credit_manage_capability")]
    operations = [migrations.RunPython(forwards, backwards)]
