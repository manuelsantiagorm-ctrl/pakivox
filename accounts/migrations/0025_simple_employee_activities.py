from django.db import migrations


ACTIVITY_TEMPLATES = (
    (
        "activity_sales_customers",
        "Ventas y clientes",
        "sales",
        "Crear y consultar pedidos, clientes y oportunidades.",
        ("sales.view", "sales.create", "crm.manage"),
    ),
    (
        "activity_collections",
        "Cobros",
        "accounting",
        "Consultar y registrar pagos y revisar comprobantes.",
        ("payments.view", "payments.create", "payments.proof.view"),
    ),
    (
        "activity_purchasing",
        "Compras",
        "manager",
        "Crear y editar compras y administrar proveedores.",
        (
            "purchases.view",
            "purchases.create",
            "purchases.edit",
            "suppliers.view",
            "suppliers.manage",
        ),
    ),
    (
        "activity_receiving",
        "Recepción",
        "warehouse",
        "Consultar y capturar la mercancía que llega a una bodega.",
        ("receiving.view", "receiving.execute", "inventory.view"),
    ),
    (
        "activity_inventory",
        "Inventario",
        "warehouse",
        "Consultar existencias, ubicaciones y disponibilidad.",
        ("inventory.view",),
    ),
    (
        "activity_picking",
        "Preparar pedidos",
        "warehouse",
        "Surtir productos de los pedidos asignados.",
        ("inventory.view", "picking.execute"),
    ),
    (
        "activity_packing",
        "Empacar",
        "warehouse",
        "Empacar pedidos listos para despacho.",
        ("inventory.view", "packing.execute"),
    ),
    (
        "activity_shipping",
        "Envíos",
        "warehouse",
        "Consultar, cotizar y despachar envíos.",
        ("shipping.view", "shipping.execute", "shipping.quote"),
    ),
    (
        "activity_accounting",
        "Contabilidad",
        "accounting",
        "Trabajar con contabilidad y registrar pagos a proveedores.",
        (
            "accounting.view",
            "accounting.manage",
            "supplier_payments.view",
            "supplier_payments.create",
            "supplier_payments.proof.view",
        ),
    ),
)


def seed_simple_activity_templates(apps, schema_editor):
    Company = apps.get_model("companies", "Company")
    Capability = apps.get_model("accounts", "Capability")
    JobTemplate = apps.get_model("accounts", "JobTemplate")

    required_codes = {
        capability_code
        for _, _, _, _, capability_codes in ACTIVITY_TEMPLATES
        for capability_code in capability_codes
    }
    capabilities = {
        item.code: item
        for item in Capability.objects.filter(
            code__in=required_codes,
            is_active=True,
        )
    }
    missing = sorted(required_codes - set(capabilities))
    if missing:
        raise RuntimeError(
            "PS-1 no puede sembrar actividades porque faltan capabilities: "
            + ", ".join(missing)
        )

    activity_codes = [item[0] for item in ACTIVITY_TEMPLATES]

    for company in Company.objects.all().iterator(chunk_size=200):
        existing = {
            item.code: item
            for item in JobTemplate.objects.filter(
                company_id=company.pk,
                code__in=activity_codes,
            )
        }

        collisions = sorted(
            code
            for code, template in existing.items()
            if not template.is_system
        )
        if collisions:
            raise RuntimeError(
                "PS-1 encontró códigos reservados usados por puestos "
                f"personalizados en company={company.pk}: "
                + ", ".join(collisions)
            )

        for code, name, role, description, capability_codes in ACTIVITY_TEMPLATES:
            template = existing.get(code)
            defaults = {
                "name": name,
                "role": role,
                "description": description,
                "is_system": True,
                "is_active": True,
            }

            if template is None:
                template = JobTemplate.objects.create(
                    company_id=company.pk,
                    code=code,
                    **defaults,
                )
            else:
                changed = []
                for field, value in defaults.items():
                    if getattr(template, field) != value:
                        setattr(template, field, value)
                        changed.append(field)
                if changed:
                    template.save(update_fields=changed)

            template.capabilities.set(
                [capabilities[item] for item in capability_codes]
            )


def reverse_simple_activity_templates(apps, schema_editor):
    """
    Un rollback posterior a uso real no debe borrar puestos ya referenciados.
    Los templates sin uso sí se eliminan para restaurar un despliegue fallido.
    """
    JobTemplate = apps.get_model("accounts", "JobTemplate")
    EmployeeJobAssignment = apps.get_model("accounts", "EmployeeJobAssignment")
    InvitationJobAssignment = apps.get_model("accounts", "InvitationJobAssignment")
    EmployeeInvitation = apps.get_model("accounts", "EmployeeInvitation")

    activity_codes = [item[0] for item in ACTIVITY_TEMPLATES]
    templates = JobTemplate.objects.filter(
        code__in=activity_codes,
        is_system=True,
    )

    used_ids = set(
        EmployeeJobAssignment.objects.filter(
            job_template__in=templates,
        ).values_list("job_template_id", flat=True)
    )
    used_ids.update(
        InvitationJobAssignment.objects.filter(
            job_template__in=templates,
        ).values_list("job_template_id", flat=True)
    )
    used_ids.update(
        EmployeeInvitation.objects.filter(
            job_template__in=templates,
        ).values_list("job_template_id", flat=True)
    )

    templates.exclude(pk__in=used_ids).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0024_employee_capability_assignment_source"),
    ]

    operations = [
        migrations.RunPython(
            seed_simple_activity_templates,
            reverse_simple_activity_templates,
        ),
    ]
