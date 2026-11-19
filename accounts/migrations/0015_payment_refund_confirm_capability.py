from django.db import migrations


def seed_refund_confirm_capability(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    JobTemplate = apps.get_model("accounts", "JobTemplate")
    capability, _ = Capability.objects.update_or_create(
        code="payments.refund.confirm",
        defaults={
            "name": "Confirmar devolución financiera",
            "area_code": "collections_payments",
        },
    )
    for template in JobTemplate.objects.filter(
        code__in=("supervisor_cobranza", "gerente_financiero", "gerente_general"),
        is_active=True,
    ).iterator():
        template.capabilities.add(capability)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0014_sales_recover_order_capability")]
    operations = [
        migrations.RunPython(seed_refund_confirm_capability, migrations.RunPython.noop),
    ]
