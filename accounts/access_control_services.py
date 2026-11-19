from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F

from .models import (
    EmployeeAccessAudit,
    EmployeeCapabilityOverride,
    UserProfile,
)


def _owner_context(actor, employee_id):
    actor_profile = getattr(actor, "profile", None)
    if (
        not actor_profile
        or not actor_profile.is_active
        or actor_profile.employment_status != UserProfile.STATUS_ACTIVE
        or actor_profile.role != "owner"
    ):
        raise PermissionDenied("Solo el owner puede realizar este cambio sensible.")
    target = (
        UserProfile.objects.select_for_update()
        .select_related("user")
        .filter(company_id=actor_profile.company_id, user_id=employee_id)
        .first()
    )
    if target is None:
        raise PermissionDenied("El trabajador no pertenece a tu empresa.")
    if target.role == "owner":
        raise ValidationError("No se puede suspender ni limitar al owner principal.")
    return actor_profile.company, target


def bump_access_version(profile):
    UserProfile.objects.filter(pk=profile.pk).update(
        access_version=F("access_version") + 1
    )
    profile.refresh_from_db(fields=["access_version"])


@transaction.atomic
def set_employee_status(*, actor, employee_id, status, reason):
    company, profile = _owner_context(actor, employee_id)
    valid = {value for value, _ in UserProfile.EMPLOYMENT_STATUS_CHOICES}
    if status not in valid:
        raise ValidationError("Estado laboral no válido.")
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError("Debes registrar el motivo.")
    previous = profile.employment_status
    profile.employment_status = status
    profile.is_active = status == UserProfile.STATUS_ACTIVE
    profile.access_version += 1
    profile.save(
        update_fields=["employment_status", "is_active", "access_version", "updated_at"]
    )
    EmployeeAccessAudit.objects.create(
        company=company,
        employee=profile.user,
        actor=actor,
        action="status_changed",
        result="success",
        reason=reason,
        detail={"previous": previous, "new": status},
    )
    return profile


@transaction.atomic
def set_capability_override(
    *,
    actor,
    employee_id,
    capability,
    effect,
    reason,
    starts_at,
    expires_at=None,
):
    company, profile = _owner_context(actor, employee_id)
    if effect not in {
        EmployeeCapabilityOverride.EFFECT_ALLOW,
        EmployeeCapabilityOverride.EFFECT_DENY,
    }:
        raise ValidationError("Efecto de permiso no válido.")
    EmployeeCapabilityOverride.objects.filter(
        company=company,
        user=profile.user,
        capability=capability,
        is_active=True,
    ).update(is_active=False)
    override = EmployeeCapabilityOverride.objects.create(
        company=company,
        user=profile.user,
        capability=capability,
        effect=effect,
        reason=reason,
        starts_at=starts_at,
        expires_at=expires_at,
        assigned_by=actor,
    )
    bump_access_version(profile)
    EmployeeAccessAudit.objects.create(
        company=company,
        employee=profile.user,
        actor=actor,
        action="permission_override",
        permission_code=capability.code,
        result="success",
        reason=override.reason,
        detail={
            "effect": effect,
            "starts_at": starts_at.isoformat(),
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
    )
    return override
