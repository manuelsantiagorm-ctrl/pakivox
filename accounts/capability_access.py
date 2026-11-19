from dataclasses import dataclass
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.utils import timezone

from .models import (
    EmployeeAccessAudit,
    EmployeeCapabilityAssignment,
    EmployeeCapabilityOverride,
    EmployeeJobAssignment,
    UserProfile,
)
from .warehouse_access import (
    ACCESS_VIEW,
    get_warehouse_for_user,
    require_active_warehouse,
    user_can_access_warehouse,
)


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    code: str
    reason: str
    origin: str = ""
    company_id: int | None = None


def get_active_user_profile(user):
    if not user or not user.is_authenticated:
        return None
    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        return None
    if (
        not profile.is_active
        or not profile.company_id
        or profile.employment_status != UserProfile.STATUS_ACTIVE
    ):
        return None
    return profile


def resolve_capability(user, capability_code):
    """Resuelve el permiso y conserva su origen para auditoría y UI."""
    if not user or not user.is_authenticated:
        return AccessDecision(False, capability_code, "Sesión no autenticada.")

    try:
        raw_profile = user.profile
    except UserProfile.DoesNotExist:
        return AccessDecision(False, capability_code, "Usuario sin empresa asignada.")

    if not raw_profile.company_id:
        return AccessDecision(False, capability_code, "Usuario sin empresa asignada.")
    if not raw_profile.is_active:
        return AccessDecision(
            False, capability_code, "Perfil desactivado.", company_id=raw_profile.company_id
        )
    if raw_profile.employment_status != UserProfile.STATUS_ACTIVE:
        return AccessDecision(
            False,
            capability_code,
            f"Estado laboral sin acceso: {raw_profile.employment_status}.",
            company_id=raw_profile.company_id,
        )

    company_id = raw_profile.company_id
    if raw_profile.role == "owner":
        return AccessDecision(
            True, capability_code, "Propietario de la empresa.", "owner", company_id
        )
    if user.is_superuser:
        return AccessDecision(
            True,
            capability_code,
            "Superusuario con perfil de empresa activo.",
            "superuser",
            company_id,
        )

    now = timezone.now()
    override = (
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
    if override and override.effect == EmployeeCapabilityOverride.EFFECT_DENY:
        return AccessDecision(
            False,
            capability_code,
            override.reason,
            "explicit_deny",
            company_id,
        )
    if override and override.effect == EmployeeCapabilityOverride.EFFECT_ALLOW:
        return AccessDecision(
            True,
            capability_code,
            override.reason,
            "exception_allow",
            company_id,
        )

    job_names = list(
        EmployeeJobAssignment.objects.filter(
            company_id=company_id,
            user_id=user.id,
            is_active=True,
            job_template__is_active=True,
            job_template__capabilities__code=capability_code,
            job_template__capabilities__is_active=True,
        )
        .values_list("job_template__name", flat=True)
        .distinct()
    )
    if job_names:
        return AccessDecision(
            True,
            capability_code,
            "Concedido por puesto activo.",
            "job:" + ", ".join(job_names),
            company_id,
        )

    if EmployeeCapabilityAssignment.objects.filter(
        company_id=company_id,
        user_id=user.id,
        source=EmployeeCapabilityAssignment.SOURCE_DIRECT,
        is_active=True,
        capability__code=capability_code,
        capability__is_active=True,
    ).exists():
        return AccessDecision(
            True,
            capability_code,
            "Asignación directa activa.",
            "direct_assignment",
            company_id,
        )

    return AccessDecision(
        False,
        capability_code,
        "La capacidad no está concedida.",
        "none",
        company_id,
    )


def user_has_capability(user, capability_code):
    return resolve_capability(user, capability_code).allowed


def get_user_capability_codes(user):
    profile = get_active_user_profile(user)
    if profile is None or profile.role == "owner" or user.is_superuser:
        return frozenset()

    codes = set(
        EmployeeCapabilityAssignment.objects.filter(
            company_id=profile.company_id,
            user_id=user.id,
            source=EmployeeCapabilityAssignment.SOURCE_DIRECT,
            is_active=True,
            capability__is_active=True,
        ).values_list("capability__code", flat=True)
    )
    codes.update(
        EmployeeJobAssignment.objects.filter(
            company_id=profile.company_id,
            user_id=user.id,
            is_active=True,
            job_template__is_active=True,
            job_template__capabilities__is_active=True,
        ).values_list("job_template__capabilities__code", flat=True)
    )
    now = timezone.now()
    overrides = EmployeeCapabilityOverride.objects.filter(
        company_id=profile.company_id,
        user_id=user.id,
        is_active=True,
        starts_at__lte=now,
        capability__is_active=True,
    ).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    codes.difference_update(
        overrides.filter(effect=EmployeeCapabilityOverride.EFFECT_DENY).values_list(
            "capability__code", flat=True
        )
    )
    codes.update(
        overrides.filter(effect=EmployeeCapabilityOverride.EFFECT_ALLOW).values_list(
            "capability__code", flat=True
        )
    )
    return frozenset(codes)


def _request_ip(request):
    # Antes tomaba el primer valor de X-Forwarded-For, que el cliente puede
    # inventar. Ahora usa la misma regla que el freno de inicio de sesión.
    from accounts.client_ip import get_client_ip

    return get_client_ip(request)


def audit_access_denied(request, decision):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not decision.company_id:
        return
    EmployeeAccessAudit.objects.create(
        company_id=decision.company_id,
        employee=user,
        actor=user,
        action="access_denied",
        permission_code=decision.code,
        result="denied",
        reason=decision.reason,
        request_path=request.path[:500],
        request_method=request.method[:10],
        ip_address=_request_ip(request),
        detail={"origin": decision.origin},
    )


def authorize_request(
    request,
    capability_code,
    *,
    required_level=ACCESS_VIEW,
    warehouse=None,
    warehouse_id=None,
    require_specific_warehouse=False,
):
    decision = resolve_capability(request.user, capability_code)
    if not decision.allowed:
        audit_access_denied(request, decision)
        raise PermissionDenied("No tienes autorización para realizar esta acción.")

    selected = warehouse
    if selected is None and warehouse_id is not None:
        try:
            selected = get_warehouse_for_user(
                request.user, warehouse_id, required_level=required_level
            )
        except PermissionDenied:
            denied = AccessDecision(
                False,
                capability_code,
                "Bodega ajena o nivel insuficiente.",
                "warehouse",
                decision.company_id,
            )
            audit_access_denied(request, denied)
            raise
    elif selected is None and require_specific_warehouse:
        try:
            selected = require_active_warehouse(request, required_level=required_level)
        except PermissionDenied:
            denied = AccessDecision(
                False,
                capability_code,
                "Se requiere una bodega concreta y autorizada.",
                "warehouse",
                decision.company_id,
            )
            audit_access_denied(request, denied)
            raise
    elif selected is not None and not user_can_access_warehouse(
        request.user, selected, required_level=required_level
    ):
        denied = AccessDecision(
            False,
            capability_code,
            "Bodega ajena o nivel insuficiente.",
            "warehouse",
            decision.company_id,
        )
        audit_access_denied(request, denied)
        raise PermissionDenied("No tienes acceso suficiente a esta bodega.")

    return selected


def capability_required(
    capability_code,
    *,
    required_level=ACCESS_VIEW,
    warehouse_kwarg=None,
    require_specific_warehouse=False,
):
    def decorator(view_function):
        @wraps(view_function)
        def wrapped_view(request, *args, **kwargs):
            warehouse_id = kwargs.get(warehouse_kwarg) if warehouse_kwarg else None
            authorize_request(
                request,
                capability_code,
                required_level=required_level,
                warehouse_id=warehouse_id,
                require_specific_warehouse=require_specific_warehouse,
            )
            return view_function(request, *args, **kwargs)

        return wrapped_view

    return decorator


def build_capability_context(request):
    user = getattr(request, "user", None)
    profile = get_active_user_profile(user)
    access_all = bool(profile and (profile.role == "owner" or user.is_superuser))
    return {
        "capability_access_all": access_all,
        "user_capability_codes": get_user_capability_codes(user),
    }
