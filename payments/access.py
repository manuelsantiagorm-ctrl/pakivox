from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from accounts.capability_access import capability_required, user_has_capability


def get_active_company_profile(user):
    profile = getattr(user, "profile", None)
    if (
        profile is None
        or not getattr(profile, "company_id", None)
        or not getattr(profile, "is_active", False)
        or getattr(profile, "employment_status", None) != profile.STATUS_ACTIVE
    ):
        return None
    return profile


def is_active_company_owner(user, company=None):
    profile = get_active_company_profile(user)
    if profile is None or profile.role != "owner":
        return False
    if company is not None and profile.company_id != company.id:
        return False
    return True


def can_approve_payments(user, company=None):
    if is_active_company_owner(user, company=company):
        return True
    return bool(user_has_capability(user, "payments.approve"))


def payment_capability_required(capability_code):
    """Owner bypass seguro; empleados conservan el motor de capacidades."""
    capability_decorator = capability_required(capability_code)

    def decorator(view_function):
        protected_employee_view = capability_decorator(view_function)

        @wraps(view_function)
        def wrapped(request, *args, **kwargs):
            profile = get_active_company_profile(request.user)
            if profile is None:
                raise PermissionDenied(
                    "No se detectó una empresa activa para este trabajador."
                )
            if profile.role == "owner":
                return view_function(request, *args, **kwargs)
            return protected_employee_view(request, *args, **kwargs)

        return login_required(wrapped, login_url="/accounts/login/")

    return decorator
