from django.db import migrations


SIMPLE_DEDICATED_CODES = (
    "activity_picking",
    "activity_packing",
    "activity_receiving",
)


def _ensure_view_for_jobs(
    JobTemplate,
    *,
    execute_capability,
    view_capability,
):
    """
    Operate implica poder consultar su propia cola.

    Es semantico:
    no depende del nombre del puesto.
    """
    if (
        execute_capability is None
        or view_capability is None
    ):
        return

    jobs = (
        JobTemplate.objects
        .filter(
            capabilities=execute_capability,
        )
        .distinct()
    )

    for job in jobs.iterator(
        chunk_size=500
    ):
        job.capabilities.add(
            view_capability
        )


def _ensure_direct_view(
    EmployeeCapabilityAssignment,
    *,
    execute_capability,
    view_capability,
):
    """
    Si EXECUTE fue concedido directamente,
    VIEW tambien debe persistir como autoridad directa.

    La constraint historica permite una sola fila por
    usuario+capability. Si existe una proyeccion source=job,
    se convierte a direct porque el EXECUTE directo debe
    conservar su VIEW incluso si despues cambia el puesto.
    """
    if (
        execute_capability is None
        or view_capability is None
    ):
        return

    rows = (
        EmployeeCapabilityAssignment.objects
        .filter(
            capability=execute_capability,
            source="direct",
            is_active=True,
        )
        .values(
            "company_id",
            "user_id",
        )
        .distinct()
    )

    for row in rows.iterator(
        chunk_size=500
    ):

        existing = (
            EmployeeCapabilityAssignment.objects
            .filter(
                user_id=row["user_id"],
                capability=view_capability,
            )
            .first()
        )

        if existing is None:
            EmployeeCapabilityAssignment.objects.create(
                company_id=row["company_id"],
                user_id=row["user_id"],
                capability=view_capability,
                source="direct",
                is_active=True,
            )
            continue

        fields = []

        if (
            existing.company_id
            != row["company_id"]
        ):
            existing.company_id = row[
                "company_id"
            ]
            fields.append(
                "company_id"
            )

        if existing.source != "direct":
            existing.source = "direct"
            fields.append(
                "source"
            )

        if not existing.is_active:
            existing.is_active = True
            fields.append(
                "is_active"
            )

        if fields:
            existing.save(
                update_fields=fields
            )


def forwards(apps, schema_editor):
    Capability = apps.get_model(
        "accounts",
        "Capability",
    )

    JobTemplate = apps.get_model(
        "accounts",
        "JobTemplate",
    )

    EmployeeCapabilityAssignment = (
        apps.get_model(
            "accounts",
            "EmployeeCapabilityAssignment",
        )
    )

    inventory_view = (
        Capability.objects
        .filter(
            code="inventory.view"
        )
        .first()
    )

    picking_execute = (
        Capability.objects
        .filter(
            code="picking.execute"
        )
        .first()
    )

    packing_execute = (
        Capability.objects
        .filter(
            code="packing.execute"
        )
        .first()
    )

    receiving_execute = (
        Capability.objects
        .filter(
            code="receiving.execute"
        )
        .first()
    )

    receiving_view = (
        Capability.objects
        .filter(
            code="receiving.view"
        )
        .first()
    )

    picking_area = (
        getattr(
            picking_execute,
            "area_code",
            "",
        )
        or "inventory_warehouse"
    )

    packing_area = (
        getattr(
            packing_execute,
            "area_code",
            "",
        )
        or "inventory_warehouse"
    )

    receiving_area = (
        getattr(
            receiving_execute,
            "area_code",
            "",
        )
        or "inventory_warehouse"
    )

    picking_view, _ = (
        Capability.objects
        .update_or_create(
            code="picking.view",
            defaults={
                "name": "Consultar picking",
                "description": (
                    "Consultar tareas de picking "
                    "sin acceso general a Inventario."
                ),
                "area_code": picking_area,
                "is_active": True,
            },
        )
    )

    packing_view, _ = (
        Capability.objects
        .update_or_create(
            code="packing.view",
            defaults={
                "name": "Consultar packing",
                "description": (
                    "Consultar tareas de packing "
                    "sin acceso general a Inventario."
                ),
                "area_code": packing_area,
                "is_active": True,
            },
        )
    )

    if receiving_view is None:
        receiving_view = (
            Capability.objects.create(
                code="receiving.view",
                name="Consultar recepciones",
                description=(
                    "Consultar recepciones de mercancia."
                ),
                area_code=receiving_area,
                is_active=True,
            )
        )

    elif not receiving_view.is_active:
        receiving_view.is_active = True
        receiving_view.save(
            update_fields=[
                "is_active",
            ]
        )

    # --------------------------------------------------------
    # EXECUTE -> VIEW
    # Para TODOS los puestos, incluidos personalizados.
    # --------------------------------------------------------

    for execute_capability, view_capability in (
        (
            picking_execute,
            picking_view,
        ),
        (
            packing_execute,
            packing_view,
        ),
        (
            receiving_execute,
            receiving_view,
        ),
    ):
        _ensure_view_for_jobs(
            JobTemplate,
            execute_capability=execute_capability,
            view_capability=view_capability,
        )

    # --------------------------------------------------------
    # PAKIVOX SIMPLE
    #
    # Solo los templates activity_* tienen contrato reservado
    # de frontera estricta.
    #
    # NO se modifica:
    # surtidor
    # empacador
    # receptor
    #
    # Esos puestos legacy pueden tener responsabilidades
    # adicionales y se retiraran en la fase correspondiente.
    # --------------------------------------------------------

    if inventory_view is not None:

        for code in SIMPLE_DEDICATED_CODES:

            jobs = (
                JobTemplate.objects
                .filter(
                    code=code,
                    is_system=True,
                )
            )

            for job in jobs.iterator(
                chunk_size=500
            ):
                job.capabilities.remove(
                    inventory_view
                )

    # --------------------------------------------------------
    # GRANTS DIRECTOS
    #
    # execute directo implica view directo.
    # DENY explicito sigue teniendo precedencia en el motor.
    # --------------------------------------------------------

    for execute_capability, view_capability in (
        (
            picking_execute,
            picking_view,
        ),
        (
            packing_execute,
            packing_view,
        ),
        (
            receiving_execute,
            receiving_view,
        ),
    ):
        _ensure_direct_view(
            EmployeeCapabilityAssignment,
            execute_capability=execute_capability,
            view_capability=view_capability,
        )


def backwards(apps, schema_editor):
    """
    Rollback conservador.

    Restaura la autoridad legacy requerida por el codigo anterior
    sin borrar capabilities/grants aditivos, porque despues de haber
    estado activa la migracion ya no es seguro asumir su procedencia.

    El objetivo del rollback es que el codigo anterior vuelva a
    funcionar sin ensanchar puestos SIMPLE mas alla de inventory.view.
    """
    Capability = apps.get_model(
        "accounts",
        "Capability",
    )

    JobTemplate = apps.get_model(
        "accounts",
        "JobTemplate",
    )

    inventory_view = (
        Capability.objects
        .filter(
            code="inventory.view"
        )
        .first()
    )

    if inventory_view is None:
        return

    for code in SIMPLE_DEDICATED_CODES:

        jobs = (
            JobTemplate.objects
            .filter(
                code=code,
                is_system=True,
            )
        )

        for job in jobs.iterator(
            chunk_size=500
        ):
            job.capabilities.add(
                inventory_view
            )


class Migration(migrations.Migration):

    dependencies = [
        (
            "accounts",
            "0025_simple_employee_activities",
        ),
    ]

    operations = [
        migrations.RunPython(
            forwards,
            backwards,
        ),
    ]