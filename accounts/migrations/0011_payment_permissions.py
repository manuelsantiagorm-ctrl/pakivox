from django.db import migrations


CAPABILITIES = (
    ("payments.create", "Registrar pagos", "collections_payments"),
    (
        "payments.proof.view",
        "Revisar comprobantes y chat asociado",
        "collections_payments",
    ),
    ("payments.cancel", "Cancelar pagos", "collections_payments"),
    ("payments.refund", "Reembolsar pagos", "collections_payments"),
    ("finance.costs.view", "Consultar costos, margen y utilidad", "accounting_finance"),
)


def seed_payment_permissions(apps, schema_editor):
    Capability = apps.get_model("accounts", "Capability")
    JobTemplate = apps.get_model("accounts", "JobTemplate")
    EmployeeCapabilityAssignment = apps.get_model(
        "accounts", "EmployeeCapabilityAssignment"
    )

    capabilities = {}
    for code, name, area_code in CAPABILITIES:
        capability, _ = Capability.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "area_code": area_code,
                "is_active": True,
            },
        )
        capabilities[code] = capability

    # Caja puede consultar, registrar, revisar comprobantes y aprobar/rechazar.
    for template in JobTemplate.objects.filter(
        code="cajero", is_active=True
    ).iterator():
        template.capabilities.add(
            capabilities["payments.create"],
            capabilities["payments.proof.view"],
        )

    # Supervisión de cobranza conserva las acciones sensibles.
    for template in JobTemplate.objects.filter(
        code="supervisor_cobranza", is_active=True
    ).iterator():
        template.capabilities.add(
            capabilities["payments.create"],
            capabilities["payments.proof.view"],
            capabilities["payments.cancel"],
            capabilities["payments.refund"],
        )

    # Finanzas/CEO pueden consultar costos; no se concede a vendedores u operadores.
    for template in JobTemplate.objects.filter(
        code__in=("contador", "gerente_financiero", "gerente_general"),
        is_active=True,
    ).iterator():
        template.capabilities.add(capabilities["finance.costs.view"])

    # Compatibilidad únicamente para asignaciones individuales ya existentes.
    # Consultar pagos habilita registrar/revisar; aprobar habilita las acciones
    # sensibles existentes. Las futuras asignaciones serán granulares.
    view_user_rows = EmployeeCapabilityAssignment.objects.filter(
        is_active=True,
        capability__code="payments.view",
    ).values_list("company_id", "user_id", "assigned_by_id")
    for company_id, user_id, assigned_by_id in view_user_rows.iterator():
        for code in ("payments.create", "payments.proof.view"):
            EmployeeCapabilityAssignment.objects.update_or_create(
                company_id=company_id,
                user_id=user_id,
                capability=capabilities[code],
                defaults={
                    "assigned_by_id": assigned_by_id,
                    "is_active": True,
                },
            )

    approve_user_rows = EmployeeCapabilityAssignment.objects.filter(
        is_active=True,
        capability__code="payments.approve",
    ).values_list("company_id", "user_id", "assigned_by_id")
    for company_id, user_id, assigned_by_id in approve_user_rows.iterator():
        for code in ("payments.cancel", "payments.refund"):
            EmployeeCapabilityAssignment.objects.update_or_create(
                company_id=company_id,
                user_id=user_id,
                capability=capabilities[code],
                defaults={
                    "assigned_by_id": assigned_by_id,
                    "is_active": True,
                },
            )


class Migration(migrations.Migration):
    dependencies = [("accounts", "0010_authorization_core")]

    operations = [
        migrations.RunPython(seed_payment_permissions, migrations.RunPython.noop),
    ]
