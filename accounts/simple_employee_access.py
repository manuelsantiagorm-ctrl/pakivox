from dataclasses import dataclass

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import transaction

from companies.models import Company

from .models import (
    Capability,
    EmployeeCapabilityAssignment,
    JobTemplate,
)


@dataclass(frozen=True)
class SimpleActivity:
    code: str
    label: str
    description: str
    icon: str
    job_code: str
    legacy_role: str
    capability_codes: tuple[str, ...]


ACTIVITIES = (
    SimpleActivity(
        code="sales_customers",
        label="Ventas y clientes",
        description="Crear y consultar pedidos, clientes y oportunidades.",
        icon="bi-people",
        job_code="activity_sales_customers",
        legacy_role="sales",
        capability_codes=(
            "sales.view",
            "sales.create",
            "crm.manage",
            "messaging.operate",
            "sales.supervise",
            "sales.recover_order",
            "sales.cancel_order",
            "sales.commercial_cancel",
        ),
    ),
    SimpleActivity(
        code="collections",
        label="Cobros",
        description="Consultar y registrar pagos y revisar comprobantes.",
        icon="bi-cash-coin",
        job_code="activity_collections",
        legacy_role="accounting",
        capability_codes=(
            "payments.view",
            "payments.create",
            "payments.proof.view",
            "payments.approve",
            "payments.cancel",
            "payments.refund",
            "payments.refund.confirm",
            "payments.customer_credit.manage",
            "payments.customer_credit.apply",
            "collections.manage",
            "messaging.operate",
        ),
    ),
    SimpleActivity(
        code="purchasing",
        label="Compras",
        description="Crear y editar compras y administrar proveedores.",
        icon="bi-cart-check",
        job_code="activity_purchasing",
        legacy_role="manager",
        capability_codes=(
            "purchases.view",
            "purchases.create",
            "purchases.edit",
            "suppliers.view",
            "suppliers.manage",
            "purchases.approve",
            "purchases.cancel",
            "purchases.costs.view",
            "purchases.costs.edit",
        ),
    ),
    SimpleActivity(
        code="receiving",
        label="Recepción",
        description="Consultar y capturar la mercancía que llega a una bodega.",
        icon="bi-box-arrow-in-down",
        job_code="activity_receiving",
        legacy_role="warehouse",
        capability_codes=(
            "receiving.view",
            "receiving.execute",
            "receiving.confirm",
            "receiving.cancel",
        ),
    ),
    SimpleActivity(
        code="inventory",
        label="Inventario",
        description="Consultar existencias, ubicaciones y disponibilidad.",
        icon="bi-boxes",
        job_code="activity_inventory",
        legacy_role="warehouse",
        capability_codes=("inventory.view", "inventory.manage"),
    ),
    SimpleActivity(
        code="picking",
        label="Preparar pedidos",
        description="Surtir productos de los pedidos asignados.",
        icon="bi-list-check",
        job_code="activity_picking",
        legacy_role="warehouse",
        capability_codes=(
            "picking.view",
            "picking.execute",
        ),
    ),
    SimpleActivity(
        code="packing",
        label="Empacar",
        description="Empacar pedidos listos para despacho.",
        icon="bi-box-seam",
        job_code="activity_packing",
        legacy_role="warehouse",
        capability_codes=(
            "packing.view",
            "packing.execute",
        ),
    ),
    SimpleActivity(
        code="shipping",
        label="Envíos",
        description="Consultar, cotizar y despachar envíos.",
        icon="bi-truck",
        job_code="activity_shipping",
        legacy_role="warehouse",
        capability_codes=(
            "shipping.view",
            "shipping.execute",
            "shipping.quote",
            "shipping.collect",
            "shipping.incidents.resolve",
        ),
    ),
    SimpleActivity(
        code="accounting",
        label="Contabilidad",
        description="Trabajar con contabilidad y registrar pagos a proveedores.",
        icon="bi-calculator",
        job_code="activity_accounting",
        legacy_role="accounting",
        capability_codes=(
            "accounting.view",
            "accounting.manage",
            "supplier_payments.view",
            "supplier_payments.create",
            "supplier_payments.proof.view",
            "supplier_payments.approve",
            "supplier_payments.reject",
            "supplier_payments.cancel",
        ),
    ),
)

ACTIVITY_BY_CODE = {item.code: item for item in ACTIVITIES}
ACTIVITY_BY_JOB_CODE = {item.job_code: item for item in ACTIVITIES}
ACTIVITY_JOB_CODES = frozenset(ACTIVITY_BY_JOB_CODE)

# Puestos legacy que pueden mostrarse como actividades humanas sin cambiar su
# asignación. Esto es solo una traducción visual para compatibilidad.
LEGACY_JOB_ACTIVITY_MAP = {
    "vendedor": ("sales_customers",),
    "gerente_ventas": ("sales_customers",),
    "cajero": ("collections",),
    "supervisor_cobranza": ("collections",),
    "comprador": ("purchasing",),
    "gerente_compras": ("purchasing",),
    "receptor": ("receiving",),
    "surtidor": ("picking",),
    "empacador": ("packing",),
    "calidad": ("inventory", "packing"),
    "supervisor": ("receiving", "inventory", "picking", "packing"),
    "coordinador_logistico": ("shipping",),
    "despachador": ("shipping",),
    "repartidor": ("shipping",),
    "contador": ("accounting",),
    "gerente_financiero": ("collections", "accounting"),
    "gerente_general": (
        "sales_customers",
        "collections",
        "purchasing",
        "receiving",
        "inventory",
        "shipping",
        "accounting",
    ),
}

# Capabilities suficientemente representativas para traducir grants directos
# legacy a una etiqueta humana. No se usan para autorizar.
DIRECT_CAPABILITY_ACTIVITY_MAP = {
    "sales.view": "sales_customers",
    "sales.create": "sales_customers",
    "crm.manage": "sales_customers",
    "payments.view": "collections",
    "payments.create": "collections",
    "purchases.view": "purchasing",
    "purchases.create": "purchasing",
    "receiving.view": "receiving",
    "receiving.execute": "receiving",
    "inventory.manage": "inventory",
    "picking.view": "picking",
    "picking.execute": "picking",
    "packing.view": "packing",
    "packing.execute": "packing",
    "shipping.view": "shipping",
    "shipping.execute": "shipping",
    "shipping.collect": "shipping",
    "shipping.incidents.resolve": "shipping",
    "accounting.view": "accounting",
    "accounting.manage": "accounting",
}
# PS-1 usa un baseline deliberadamente conservador. Estas acciones sensibles
# quedan fuera hasta que PS-2/PS-3 definan la matriz de autoridad.
SENSITIVE_BASE_CAPABILITIES = frozenset(
    {
        "quality.approve",
        "finance.costs.view",
        "dashboard.view",
        "control_tower.view",
        "team.manage",
        "company.configure",
        "integrations.configure",
        "imports.manage",
        "messaging.configure",
        "shipping.configure",
    }
)


def activity_choices():
    return [(item.code, item.label) for item in ACTIVITIES]


def normalize_activity_codes(values, *, required=True):
    raw = [str(value or "").strip() for value in (values or ())]
    raw = [value for value in raw if value]
    unknown = sorted(set(raw) - set(ACTIVITY_BY_CODE))
    if unknown:
        raise ValidationError(
            "Seleccionaste una actividad que no pertenece al catálogo de PAKIVOX."
        )

    selected = set(raw)
    ordered = tuple(item.code for item in ACTIVITIES if item.code in selected)
    if required and not ordered:
        raise ValidationError("Selecciona al menos una actividad.")
    return ordered


def _required_capability_codes():
    return {code for activity in ACTIVITIES for code in activity.capability_codes}


@transaction.atomic
def ensure_company_activity_templates(company):
    """
    Crea/normaliza exclusivamente los JobTemplate reservados de PS-1.

    Se serializa por empresa para que dos invitaciones concurrentes no intenten
    sembrar el mismo catálogo al mismo tiempo.
    """
    if not company or not getattr(company, "pk", None):
        raise ValidationError("No se detectó una empresa válida.")

    locked_company = Company.objects.select_for_update().get(pk=company.pk)
    required_codes = _required_capability_codes()

    capabilities = {
        item.code: item
        for item in Capability.objects.filter(
            code__in=required_codes,
            is_active=True,
        )
    }
    missing = sorted(required_codes - set(capabilities))
    if missing:
        raise ImproperlyConfigured(
            "Faltan capabilities requeridas para el acceso simple: "
            + ", ".join(missing)
        )

    existing = {
        item.code: item
        for item in JobTemplate.objects.filter(
            company=locked_company,
            code__in=ACTIVITY_JOB_CODES,
        )
    }
    collisions = sorted(
        code for code, template in existing.items() if not template.is_system
    )
    if collisions:
        raise ImproperlyConfigured(
            "Hay códigos reservados de actividades usados por puestos "
            "personalizados: " + ", ".join(collisions)
        )

    result = []
    for activity in ACTIVITIES:
        template = existing.get(activity.job_code)
        defaults = {
            "name": activity.label,
            "role": activity.legacy_role,
            "description": activity.description,
            "is_system": True,
            "is_active": True,
        }
        if template is None:
            template = JobTemplate.objects.create(
                company=locked_company,
                code=activity.job_code,
                **defaults,
            )
        else:
            changed = []
            for field, value in defaults.items():
                if getattr(template, field) != value:
                    setattr(template, field, value)
                    changed.append(field)
            if changed:
                template.save(update_fields=[*changed])

        template.capabilities.set(
            [capabilities[code] for code in activity.capability_codes]
        )
        result.append(template)

    return result


def resolve_activity_job_templates(company, activity_codes, *, ensure=False):
    normalized = normalize_activity_codes(activity_codes)
    if ensure:
        ensure_company_activity_templates(company)

    wanted = {ACTIVITY_BY_CODE[code].job_code for code in normalized}
    templates = {
        item.code: item
        for item in JobTemplate.objects.filter(
            company=company,
            code__in=wanted,
            is_system=True,
            is_active=True,
        ).prefetch_related("capabilities")
    }

    missing = sorted(wanted - set(templates))
    if missing:
        raise ImproperlyConfigured(
            "El catálogo simple de esta empresa está incompleto: " + ", ".join(missing)
        )

    return [templates[ACTIVITY_BY_CODE[code].job_code] for code in normalized]


def activity_codes_from_job_codes(job_codes):
    selected = set()
    for job_code in job_codes or ():
        activity = ACTIVITY_BY_JOB_CODE.get(str(job_code or ""))
        if activity:
            selected.add(activity.code)
            continue
        selected.update(LEGACY_JOB_ACTIVITY_MAP.get(str(job_code or ""), ()))
    return tuple(item.code for item in ACTIVITIES if item.code in selected)


def infer_activity_codes_from_profile(profile):
    """
    Traducción de compatibilidad para el directorio.

    No concede ni revoca permisos. Usa puestos ya cargados y grants directos
    para mostrar términos humanos mientras conviven empleados legacy.
    """
    if not profile or not getattr(profile, "user_id", None):
        return tuple()

    selected = set()

    for assignment in profile.user.job_assignments.all():
        if not assignment.is_active:
            continue
        job = assignment.job_template
        activity = ACTIVITY_BY_JOB_CODE.get(job.code)
        if activity:
            selected.add(activity.code)
            continue
        selected.update(LEGACY_JOB_ACTIVITY_MAP.get(job.code, ()))

    for assignment in profile.user.capability_assignments.all():
        if (
            not assignment.is_active
            or assignment.source != EmployeeCapabilityAssignment.SOURCE_DIRECT
        ):
            continue
        activity_code = DIRECT_CAPABILITY_ACTIVITY_MAP.get(assignment.capability.code)
        if activity_code:
            selected.add(activity_code)

    return tuple(item.code for item in ACTIVITIES if item.code in selected)


def activity_labels(activity_codes):
    normalized = normalize_activity_codes(activity_codes, required=False)
    return tuple(ACTIVITY_BY_CODE[code].label for code in normalized)


def has_legacy_active_jobs(profile):
    if not profile or not getattr(profile, "user_id", None):
        return False
    return any(
        assignment.is_active and assignment.job_template.code not in ACTIVITY_JOB_CODES
        for assignment in profile.user.job_assignments.all()
    )
