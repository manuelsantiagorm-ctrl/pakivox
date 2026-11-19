from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F

from inventory.models import Warehouse

from .models import (
    EmployeeAccessAudit,
    EmployeeAreaAssignment,
    EmployeeCapabilityAssignment,
    EmployeeJobAssignment,
    JobTemplate,
    UserProfile,
    UserWarehouseAccess,
    WorkArea,
)
from .simple_employee_access import (
    ACTIVITY_JOB_CODES,
    normalize_activity_codes,
    resolve_activity_job_templates,
)


PRESERVE_WAREHOUSE_ACCESS_LEVEL = "preserve"
VALID_WAREHOUSE_ACCESS_LEVELS = frozenset(
    {
        UserWarehouseAccess.LEVEL_VIEW,
        UserWarehouseAccess.LEVEL_OPERATE,
        UserWarehouseAccess.LEVEL_APPROVE,
        UserWarehouseAccess.LEVEL_MANAGE,
    }
)


def _management_context(actor, employee_id):
    profile = getattr(actor, "profile", None)
    if not profile or not profile.is_active or profile.role not in {"owner", "admin"}:
        raise PermissionDenied("No puedes administrar accesos del equipo.")
    employee_profile = (
        UserProfile.objects.select_related("user")
        .filter(company=profile.company, user_id=employee_id)
        .first()
    )
    if not employee_profile:
        raise PermissionDenied("El empleado no pertenece a tu empresa.")
    if employee_profile.role == "owner":
        raise ValidationError("El acceso del propietario no se administra aquí.")
    return profile.company, employee_profile.user


def sync_employee_effective_access(*, company, employee, actor):
    assignments = list(
        EmployeeJobAssignment.objects.filter(
            company=company, user=employee, is_active=True
        ).select_related("job_template").prefetch_related("job_template__capabilities")
    )
    capability_ids = {
        capability.pk
        for assignment in assignments
        for capability in assignment.job_template.capabilities.all()
        if capability.is_active
    }
    area_codes = {
        capability.area_code
        for assignment in assignments
        for capability in assignment.job_template.capabilities.all()
        if capability.is_active
    }

    # Solo las asignaciones con source="job" pertenecen a esta proyección.
    # Las asignaciones directas son independientes y nunca deben revocarse
    # como efecto secundario de editar los puestos del empleado.
    EmployeeCapabilityAssignment.objects.filter(
        company=company,
        user=employee,
        source=EmployeeCapabilityAssignment.SOURCE_JOB,
    ).exclude(capability_id__in=capability_ids).update(is_active=False)

    existing_by_capability = {
        item.capability_id: item
        for item in EmployeeCapabilityAssignment.objects.filter(
            company=company,
            user=employee,
            capability_id__in=capability_ids,
        )
    }

    for capability_id in capability_ids:
        assignment = existing_by_capability.get(capability_id)

        if assignment is None:
            EmployeeCapabilityAssignment.objects.create(
                company=company,
                user=employee,
                capability_id=capability_id,
                assigned_by=actor,
                source=EmployeeCapabilityAssignment.SOURCE_JOB,
                is_active=True,
            )
            continue

        # La constraint histórica permite una sola fila por (user, capability).
        # Si esa fila es directa, el puesto ya concede acceso dinámicamente y
        # el sincronizador no debe convertirla ni tocar su estado.
        if assignment.source != EmployeeCapabilityAssignment.SOURCE_JOB:
            continue

        fields_to_update = []
        if not assignment.is_active:
            assignment.is_active = True
            fields_to_update.append("is_active")
        actor_id = getattr(actor, "id", None)
        if assignment.assigned_by_id != actor_id:
            assignment.assigned_by = actor
            fields_to_update.append("assigned_by")
        if fields_to_update:
            assignment.save(update_fields=fields_to_update)

    areas = WorkArea.objects.filter(
        company=company, code__in=area_codes, is_active=True
    )
    area_ids = set(areas.values_list("id", flat=True))
    EmployeeAreaAssignment.objects.filter(
        company=company, user=employee
    ).exclude(area_id__in=area_ids).update(is_active=False)
    for area in areas:
        EmployeeAreaAssignment.objects.update_or_create(
            company=company,
            user=employee,
            area=area,
            defaults={"assigned_by": actor, "is_active": True},
        )


@transaction.atomic
def update_employee_jobs(*, actor, employee_id, job_ids, primary_job_id):
    company, employee = _management_context(actor, employee_id)
    selected_jobs = list(
        JobTemplate.objects.filter(
            company=company, is_active=True, id__in=set(job_ids)
        ).prefetch_related("capabilities")
    )
    selected_ids = {job.pk for job in selected_jobs}
    if len(selected_ids) != len(set(job_ids)):
        raise PermissionDenied("Uno de los puestos no pertenece a tu empresa.")
    if not selected_ids:
        raise ValidationError("El empleado debe conservar al menos un puesto.")
    if primary_job_id not in selected_ids:
        raise ValidationError("El puesto principal debe estar seleccionado.")

    current = {
        item.job_template_id: item
        for item in EmployeeJobAssignment.objects.select_for_update().filter(
            company=company, user=employee
        )
    }
    removed = []
    added = []
    for job_id, assignment in current.items():
        should_be_active = job_id in selected_ids
        if assignment.is_active and not should_be_active:
            removed.append(assignment.job_template.name)
        assignment.is_active = should_be_active
        assignment.is_primary = should_be_active and job_id == primary_job_id
        assignment.assigned_by = actor
        assignment.save()
    for job in selected_jobs:
        if job.pk not in current:
            EmployeeJobAssignment.objects.create(
                company=company,
                user=employee,
                job_template=job,
                assigned_by=actor,
                is_primary=job.pk == primary_job_id,
            )
            added.append(job.name)

    sync_employee_effective_access(company=company, employee=employee, actor=actor)
    primary_job = next(job for job in selected_jobs if job.pk == primary_job_id)
    employee.profile.role = primary_job.role
    employee.profile.access_version = F("access_version") + 1
    employee.profile.save(update_fields=["role", "access_version", "updated_at"])
    EmployeeAccessAudit.objects.create(
        company=company,
        employee=employee,
        actor=actor,
        action="jobs_updated",
        detail={
            "added": added,
            "removed": removed,
            "active_job_ids": sorted(selected_ids),
            "primary_job_id": primary_job_id,
        },
    )
    return employee


@transaction.atomic
def update_employee_activities(
    *,
    actor,
    employee_id,
    activity_codes,
    warehouse_levels=None,
):
    """
    Edita el acceso simple de una persona.

    Siempre actualiza las actividades reservadas de PS-1. Si se recibe
    warehouse_levels (diccionario {warehouse_id: nivel}), también
    actualiza las bodegas y su nivel humano (Consultar / Trabajar /
    Supervisar / Administrar) dentro de la misma transacción. Cada
    bodega tiene su propio nivel independiente.

    Los puestos legacy, grants directos y overrides se conservan. El
    valor "preserve" para una bodega específica existe únicamente para
    empleados legacy que ya tienen un nivel asignado en esa bodega:
    permite quitar bodegas o conservar el nivel actual de esa bodega
    puntual sin normalizarlo accidentalmente. Una bodega nueva (sin
    acceso previo) nunca puede usar "preserve".
    """
    company, employee = _management_context(actor, employee_id)
    normalized_codes = normalize_activity_codes(activity_codes)

    selected_warehouse_ids = None
    selected_warehouses = {}
    warehouse_level_by_id = {}

    if warehouse_levels is not None:
        try:
            warehouse_level_by_id = {
                int(warehouse_id): str(level)
                for warehouse_id, level in warehouse_levels.items()
            }
        except (TypeError, ValueError, AttributeError):
            raise ValidationError("Seleccionaste una bodega inválida.")

        selected_warehouse_ids = set(warehouse_level_by_id)

        if not selected_warehouse_ids:
            raise ValidationError("Selecciona al menos una bodega.")

        selected_warehouses = {
            warehouse.pk: warehouse
            for warehouse in Warehouse.objects.filter(
                company=company,
                is_active=True,
                id__in=selected_warehouse_ids,
            ).only("id", "company_id")
        }

        if set(selected_warehouses) != selected_warehouse_ids:
            raise PermissionDenied(
                "Una de las bodegas seleccionadas no pertenece a tu empresa "
                "o ya no está activa."
            )

        for level in warehouse_level_by_id.values():
            if (
                level != PRESERVE_WAREHOUSE_ACCESS_LEVEL
                and level not in VALID_WAREHOUSE_ACCESS_LEVELS
            ):
                raise ValidationError(
                    "Selecciona un nivel válido para cada bodega: "
                    "Consultar, Trabajar, Supervisar o Administrar."
                )

    selected_templates = resolve_activity_job_templates(
        company,
        normalized_codes,
        ensure=True,
    )
    selected_by_code = {item.code: item for item in selected_templates}
    selected_job_codes = set(selected_by_code)

    assignments = list(
        EmployeeJobAssignment.objects.select_for_update()
        .filter(
            company=company,
            user=employee,
        )
        .select_related("job_template")
    )

    current_activity = {
        item.job_template.code: item
        for item in assignments
        if item.job_template.code in ACTIVITY_JOB_CODES
    }

    previously_active_codes = {
        item.job_template.code for item in current_activity.values() if item.is_active
    }

    # La edición simple define un nuevo puesto principal de compatibilidad.
    # Los puestos legacy permanecen activos, pero dejan de ser "primary".
    EmployeeJobAssignment.objects.filter(
        company=company,
        user=employee,
        is_primary=True,
    ).update(is_primary=False)

    for job_code, assignment in current_activity.items():
        should_be_active = job_code in selected_job_codes
        fields = []

        if assignment.is_active != should_be_active:
            assignment.is_active = should_be_active
            fields.append("is_active")

        if assignment.is_primary:
            assignment.is_primary = False
            fields.append("is_primary")

        actor_id = getattr(actor, "id", None)
        if assignment.assigned_by_id != actor_id:
            assignment.assigned_by = actor
            fields.append("assigned_by")

        if fields:
            assignment.save(update_fields=fields)

    for template in selected_templates:
        assignment = current_activity.get(template.code)
        if assignment is None:
            assignment = EmployeeJobAssignment.objects.create(
                company=company,
                user=employee,
                job_template=template,
                assigned_by=actor,
                is_primary=False,
                is_active=True,
            )
            current_activity[template.code] = assignment

    primary_template = selected_templates[0]
    primary_assignment = current_activity[primary_template.code]
    if not primary_assignment.is_active:
        primary_assignment.is_active = True
    primary_assignment.is_primary = True
    primary_assignment.assigned_by = actor
    primary_assignment.save(
        update_fields=[
            "is_active",
            "is_primary",
            "assigned_by",
            "updated_at",
        ]
    )

    warehouse_change_detail = None

    if selected_warehouse_ids is not None:
        current_warehouse_accesses = {
            item.warehouse_id: item
            for item in UserWarehouseAccess.objects.select_for_update().filter(
                company=company,
                user=employee,
            )
        }

        new_warehouse_ids = selected_warehouse_ids - set(current_warehouse_accesses)
        preserve_requested_for_new = {
            warehouse_id
            for warehouse_id in new_warehouse_ids
            if warehouse_level_by_id[warehouse_id] == PRESERVE_WAREHOUSE_ACCESS_LEVEL
        }
        if preserve_requested_for_new:
            raise ValidationError(
                "Para agregar una bodega nueva, elige un nivel: Consultar, "
                "Trabajar, Supervisar o Administrar."
            )

        activated = []
        deactivated = []
        level_changed = []

        for warehouse_id, access in current_warehouse_accesses.items():
            should_be_active = warehouse_id in selected_warehouse_ids
            fields = []

            if access.is_active != should_be_active:
                access.is_active = should_be_active
                fields.append("is_active")
                if should_be_active:
                    activated.append(warehouse_id)
                else:
                    deactivated.append(warehouse_id)

            if should_be_active:
                requested_level = warehouse_level_by_id[warehouse_id]
                if (
                    requested_level != PRESERVE_WAREHOUSE_ACCESS_LEVEL
                    and access.access_level != requested_level
                ):
                    access.access_level = requested_level
                    fields.append("access_level")
                    level_changed.append(warehouse_id)

            actor_id = getattr(actor, "id", None)
            if should_be_active and access.assigned_by_id != actor_id:
                access.assigned_by = actor
                fields.append("assigned_by")

            if fields:
                access.save(update_fields=[*fields, "updated_at"])

        for warehouse_id in sorted(new_warehouse_ids):
            UserWarehouseAccess.objects.create(
                company=company,
                user=employee,
                warehouse=selected_warehouses[warehouse_id],
                access_level=warehouse_level_by_id[warehouse_id],
                is_active=True,
                assigned_by=actor,
            )
            activated.append(warehouse_id)

        warehouse_change_detail = {
            "warehouse_ids": sorted(selected_warehouse_ids),
            "warehouse_levels": {
                str(warehouse_id): warehouse_level_by_id[warehouse_id]
                for warehouse_id in sorted(selected_warehouse_ids)
            },
            "activated_warehouse_ids": sorted(activated),
            "deactivated_warehouse_ids": sorted(deactivated),
            "level_changed_warehouse_ids": sorted(level_changed),
        }

    sync_employee_effective_access(
        company=company,
        employee=employee,
        actor=actor,
    )

    employee.profile.role = primary_template.role
    employee.profile.access_version = F("access_version") + 1
    employee.profile.save(
        update_fields=[
            "role",
            "access_version",
            "updated_at",
        ]
    )

    preserved_legacy_job_ids = sorted(
        item.job_template_id
        for item in assignments
        if item.is_active and item.job_template.code not in ACTIVITY_JOB_CODES
    )

    audit_detail = {
        "activity_codes": list(normalized_codes),
        "activity_job_ids": sorted(item.pk for item in selected_templates),
        "added_activity_job_codes": sorted(
            selected_job_codes - previously_active_codes
        ),
        "removed_activity_job_codes": sorted(
            previously_active_codes - selected_job_codes
        ),
        "preserved_legacy_job_ids": preserved_legacy_job_ids,
        "primary_activity_job_id": primary_template.pk,
    }
    if warehouse_change_detail is not None:
        audit_detail["warehouse_access"] = warehouse_change_detail

    EmployeeAccessAudit.objects.create(
        company=company,
        employee=employee,
        actor=actor,
        action=(
            "simple_access_updated"
            if warehouse_change_detail is not None
            else "activities_updated"
        ),
        detail=audit_detail,
    )

    return employee
