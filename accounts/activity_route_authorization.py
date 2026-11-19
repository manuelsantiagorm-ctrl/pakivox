from dataclasses import dataclass
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils import timezone

from .capability_access import (
    AccessDecision,
    audit_access_denied,
    authorize_request,
    resolve_capability,
)
from .models import (
    EmployeeCapabilityOverride,
    EmployeeJobAssignment,
    UserWarehouseAccess,
)
from .simple_activity_route_contract import (
    CLASS_ACTIVITY,
    CLASS_EXTERNAL,
    CLASS_PRIVILEGED,
    SCOPE_COMPANY,
    SCOPE_MULTIWAREHOUSE,
    SCOPE_TRANSFER,
    SCOPE_WAREHOUSE,
    route_action_contract,
)
from .simple_activity_contract import (
    OUT_OF_ACTIVITY_SCOPE_CAPABILITIES,
    capabilities_for_activity_level,
)
from .simple_employee_access import (
    ACTIVITY_BY_CODE,
    ACTIVITY_JOB_CODES,
)
from .warehouse_access import (
    ACCESS_APPROVE,
    ACCESS_MANAGE,
    ACCESS_OPERATE,
    ACCESS_VIEW,
    ACCESS_LEVELS_ALLOWED,
    get_accessible_warehouses,
    get_user_company,
    require_active_warehouse,
    user_can_access_warehouse,
)

LEVEL_ORDER = {
    ACCESS_VIEW: 0,
    ACCESS_OPERATE: 1,
    ACCESS_APPROVE: 2,
    ACCESS_MANAGE: 3,
}

UNSAFE_METHODS = frozenset(
    {
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    }
)


@dataclass(frozen=True)
class ActivityRouteAuthorization:
    allowed: bool
    route_key: str
    capability_code: str | None
    required_level: str | None
    scope: str
    origin: str
    reason: str
    company_id: int | None = None


def _deny(
    *,
    request,
    contract,
    reason,
    origin,
    company_id,
):
    decision = AccessDecision(
        False,
        contract.capability_code or contract.key,
        reason,
        origin,
        company_id,
    )
    audit_access_denied(request, decision)
    raise PermissionDenied(reason)


def _active_override(
    *,
    user,
    company_id,
    capability_code,
):
    if not capability_code:
        return None

    now = timezone.now()

    return (
        EmployeeCapabilityOverride.objects.filter(
            company_id=company_id,
            user_id=user.id,
            capability__code=capability_code,
            capability__is_active=True,
            is_active=True,
            starts_at__lte=now,
        )
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .select_related("capability")
        .first()
    )


def _route_capability_is_valid_for_activity(contract):
    if (
        contract.classification != CLASS_ACTIVITY
        or not contract.activity_code
        or not contract.minimum_level
        or not contract.capability_code
    ):
        return True

    baseline = set(
        capabilities_for_activity_level(
            contract.activity_code,
            contract.minimum_level,
        )
    )

    if contract.capability_code in baseline:
        return True

    if not contract.route_level_derivation:
        return False

    if contract.capability_code in OUT_OF_ACTIVITY_SCOPE_CAPABILITIES:
        return False

    maximum = set(
        capabilities_for_activity_level(
            contract.activity_code,
            ACCESS_MANAGE,
        )
    )

    return contract.capability_code in maximum


def _has_simple_activity(
    *,
    user,
    company_id,
    activity_code,
):
    activity = ACTIVITY_BY_CODE.get(activity_code)

    if activity is None:
        return False

    return EmployeeJobAssignment.objects.filter(
        company_id=company_id,
        user_id=user.id,
        is_active=True,
        job_template__is_active=True,
        job_template__code=activity.job_code,
    ).exists()


def _has_legacy_job_capability(
    *,
    user,
    company_id,
    capability_code,
):
    if not capability_code:
        return False

    return (
        EmployeeJobAssignment.objects.filter(
            company_id=company_id,
            user_id=user.id,
            is_active=True,
            job_template__is_active=True,
            job_template__capabilities__code=capability_code,
            job_template__capabilities__is_active=True,
        )
        .exclude(
            job_template__code__in=ACTIVITY_JOB_CODES,
        )
        .exists()
    )


def _company_authority_floor(
    *,
    user,
    company_id,
):
    """
    Para actividades simples con alcance de empresa, usa el nivel más bajo
    entre las bodegas activas asignadas.

    La UI simple normal usa el mismo nivel en todas las bodegas. El mínimo es
    deliberadamente conservador para empleados legacy con niveles mezclados:
    una bodega con menos autoridad no puede elevar una acción global.
    """
    levels = list(
        UserWarehouseAccess.objects.filter(
            company_id=company_id,
            user_id=user.id,
            is_active=True,
            warehouse__company_id=company_id,
        ).values_list(
            "access_level",
            flat=True,
        )
    )

    valid = [level for level in levels if level in LEVEL_ORDER]

    if not valid:
        return None

    return min(
        valid,
        key=LEVEL_ORDER.__getitem__,
    )


def _level_allows(
    granted_level,
    required_level,
):
    if granted_level not in LEVEL_ORDER or required_level not in LEVEL_ORDER:
        return False

    return LEVEL_ORDER[granted_level] >= LEVEL_ORDER[required_level]


def _normalize_warehouses(warehouses):
    normalized = []

    for warehouse in warehouses or ():
        if warehouse is None:
            continue

        if all(
            getattr(existing, "pk", None) != getattr(warehouse, "pk", None)
            for existing in normalized
        ):
            normalized.append(warehouse)

    return tuple(normalized)


def _authorize_target_scope(
    *,
    request,
    contract,
    warehouses=None,
    require_specific_warehouse=False,
):
    required_level = contract.minimum_level
    company = get_user_company(request.user)

    if company is None:
        _deny(
            request=request,
            contract=contract,
            reason="No se detectó una empresa activa para este trabajador.",
            origin="company",
            company_id=None,
        )

    if contract.scope == SCOPE_COMPANY:
        return tuple()

    if contract.scope == SCOPE_WAREHOUSE:
        normalized = _normalize_warehouses(warehouses)

        if len(normalized) > 1:
            _deny(
                request=request,
                contract=contract,
                reason="La acción esperaba una sola bodega.",
                origin="warehouse",
                company_id=company.id,
            )

        if normalized:
            warehouse = normalized[0]

            if not user_can_access_warehouse(
                request.user,
                warehouse,
                required_level=required_level,
            ):
                _deny(
                    request=request,
                    contract=contract,
                    reason="No tienes el nivel requerido en esta bodega.",
                    origin="warehouse",
                    company_id=company.id,
                )

            return normalized

        if require_specific_warehouse:
            try:
                warehouse = require_active_warehouse(
                    request,
                    required_level=required_level,
                )
            except PermissionDenied:
                _deny(
                    request=request,
                    contract=contract,
                    reason=(
                        "Selecciona una bodega específica donde tengas "
                        "el nivel requerido."
                    ),
                    origin="warehouse",
                    company_id=company.id,
                )

            return (warehouse,)

        if not get_accessible_warehouses(
            request.user,
            required_level=required_level,
        ).exists():
            _deny(
                request=request,
                contract=contract,
                reason="No tienes una bodega accesible con el nivel requerido.",
                origin="warehouse",
                company_id=company.id,
            )

        return tuple()

    if contract.scope in {
        SCOPE_MULTIWAREHOUSE,
        SCOPE_TRANSFER,
    }:
        normalized = _normalize_warehouses(warehouses)

        if (
            str(getattr(request, "method", "GET") or "GET").upper() in UNSAFE_METHODS
            and not normalized
        ):
            _deny(
                request=request,
                contract=contract,
                reason=(
                    "Esta acción modifica datos de varias bodegas y debe "
                    "declarar explícitamente todas las bodegas afectadas."
                ),
                origin="warehouse_scope_missing",
                company_id=company.id,
            )

        if normalized:
            for warehouse in normalized:
                if not user_can_access_warehouse(
                    request.user,
                    warehouse,
                    required_level=required_level,
                ):
                    _deny(
                        request=request,
                        contract=contract,
                        reason=(
                            "No tienes el nivel requerido en todas las "
                            "bodegas afectadas."
                        ),
                        origin="warehouse",
                        company_id=company.id,
                    )

            return normalized

        if not get_accessible_warehouses(
            request.user,
            required_level=required_level,
        ).exists():
            _deny(
                request=request,
                contract=contract,
                reason="No tienes una bodega accesible con el nivel requerido.",
                origin="warehouse",
                company_id=company.id,
            )

        return tuple()

    _deny(
        request=request,
        contract=contract,
        reason=f"Alcance PS-2B no soportado: {contract.scope}.",
        origin="contract",
        company_id=company.id,
    )


def authorize_activity_route(
    request,
    source,
    route_name,
    *,
    warehouse=None,
    warehouses=None,
    require_specific_warehouse=False,
):
    """
    Motor PS-3A.

    Evalúa en este orden:
    - contrato de ruta PS-2B;
    - tenant/perfil activo;
    - deny explícito;
    - capability explícita/legacy o actividad simple;
    - nivel humano;
    - bodega(s) afectadas.

    Importante:
    Los JobTemplate activity_* de PS-1 sólo materializan el baseline. Este
    motor puede autorizar capabilities superiores declaradas en PS-2A cuando
    la persona tiene la actividad y el nivel de bodega suficiente. No altera
    JobTemplate, no crea grants globales y no modifica la base de datos salvo
    la auditoría normal de denegaciones.
    """
    contract = route_action_contract(
        source,
        route_name,
    )

    if contract.classification == CLASS_EXTERNAL:
        raise ValueError(f"La ruta externa {contract.key} no usa autorización humana.")

    company = get_user_company(request.user)

    if company is None:
        _deny(
            request=request,
            contract=contract,
            reason="No se detectó una empresa activa para este trabajador.",
            origin="company",
            company_id=None,
        )

    if (
        contract.classification == CLASS_ACTIVITY
        and not _route_capability_is_valid_for_activity(contract)
    ):
        _deny(
            request=request,
            contract=contract,
            reason=(
                "El contrato de esta ruta intenta derivar una capability "
                "fuera del nivel de actividad permitido."
            ),
            origin="contract",
            company_id=company.id,
        )

    if contract.classification == CLASS_PRIVILEGED:
        authorize_request(
            request,
            contract.capability_code,
        )

        return ActivityRouteAuthorization(
            True,
            contract.key,
            contract.capability_code,
            None,
            contract.scope,
            "privileged_capability",
            "Capability privilegiada validada.",
            company.id,
        )

    if contract.classification != CLASS_ACTIVITY:
        raise ValueError(f"Clasificación PS-2B no soportada: {contract.classification}")

    override = _active_override(
        user=request.user,
        company_id=company.id,
        capability_code=contract.capability_code,
    )

    if (
        override is not None
        and override.effect == EmployeeCapabilityOverride.EFFECT_DENY
    ):
        _deny(
            request=request,
            contract=contract,
            reason=override.reason,
            origin="explicit_deny",
            company_id=company.id,
        )

    decision = resolve_capability(
        request.user,
        contract.capability_code,
    )

    explicit_capability = decision.allowed and (
        decision.origin
        in {
            "owner",
            "superuser",
            "direct_assignment",
            "exception_allow",
        }
        or _has_legacy_job_capability(
            user=request.user,
            company_id=company.id,
            capability_code=contract.capability_code,
        )
    )

    simple_activity = _has_simple_activity(
        user=request.user,
        company_id=company.id,
        activity_code=contract.activity_code,
    )

    if not explicit_capability and not simple_activity:
        _deny(
            request=request,
            contract=contract,
            reason=(
                "La actividad requerida no está asignada y no existe "
                "un grant explícito compatible."
            ),
            origin="activity_missing",
            company_id=company.id,
        )

    if simple_activity and not explicit_capability and contract.scope == SCOPE_COMPANY:
        granted_level = _company_authority_floor(
            user=request.user,
            company_id=company.id,
        )

        if not _level_allows(
            granted_level,
            contract.minimum_level,
        ):
            _deny(
                request=request,
                contract=contract,
                reason=(
                    "Tu nivel de autoridad no alcanza para esta acción "
                    "de alcance general."
                ),
                origin="company_authority",
                company_id=company.id,
            )

    normalized = warehouses

    if warehouse is not None:
        if warehouses:
            raise ValueError("Usa warehouse o warehouses, no ambos.")
        normalized = (warehouse,)

    authorized_warehouses = _authorize_target_scope(
        request=request,
        contract=contract,
        warehouses=normalized,
        require_specific_warehouse=require_specific_warehouse,
    )

    origin = (
        decision.origin if explicit_capability else f"activity:{contract.activity_code}"
    )

    return ActivityRouteAuthorization(
        True,
        contract.key,
        contract.capability_code,
        contract.minimum_level,
        contract.scope,
        origin,
        "Acceso PS-3A autorizado.",
        company.id,
    )


def activity_route_required(
    source,
    route_name,
    *,
    warehouse_kwarg=None,
    require_specific_warehouse=False,
):
    """
    Decorador para rutas donde la bodega llega directamente como objeto
    resuelto por el caller o mediante una selección activa.

    Las rutas object-level más complejas (pedidos multibodega, pagos,
    transferencias) deben llamar authorize_activity_route() después de
    resolver el objeto y pasar todas las bodegas afectadas.
    """

    def decorator(view_function):
        @wraps(view_function)
        def wrapped_view(request, *args, **kwargs):
            warehouse = kwargs.get(warehouse_kwarg) if warehouse_kwarg else None

            authorize_activity_route(
                request,
                source,
                route_name,
                warehouse=warehouse,
                require_specific_warehouse=require_specific_warehouse,
            )

            return view_function(
                request,
                *args,
                **kwargs,
            )

        return wrapped_view

    return decorator
