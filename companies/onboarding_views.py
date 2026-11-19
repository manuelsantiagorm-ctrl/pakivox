from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from inventory.models import Warehouse
from subscriptions.models import Subscription

from .setup_views import puede_configurar_empresa


def _get_user_company(user):
    """
    Obtiene exclusivamente la empresa asociada al usuario autenticado.

    No acepta company_id desde GET, POST ni parámetros externos.
    Mantiene el aislamiento multiempresa de WMS360.
    """

    if not user or not getattr(user, "is_authenticated", False):
        return None

    # Estructura principal utilizada actualmente por WMS360.
    profile = (
        getattr(user, "profile", None)
        or getattr(user, "userprofile", None)
    )

    if profile and getattr(profile, "company_id", None):
        return profile.company

    # Compatibilidad con usuarios donde company esté directamente
    # relacionada al usuario.
    company = getattr(user, "company", None)

    if company and getattr(company, "pk", None):
        return company

    return None

@login_required(login_url="accounts:login")
def onboarding_wms360(request):
    """
    Bienvenida y configuración inicial de WMS360.

    Esta vista:
    - No crea empresas.
    - No crea bodegas.
    - No modifica suscripciones.
    - No modifica permisos.
    - Nunca recibe una empresa desde el frontend.
    """

    company = _get_user_company(request.user)

    if company is None:
        return redirect("accounts:onboarding_empresa")

    if not puede_configurar_empresa(request.user):
        return redirect("accounts:employee_home")

    # Siempre filtrado por la empresa autenticada.
    warehouse_count = (
        Warehouse.objects
        .filter(company=company)
        .count()
    )

    subscription = (
        Subscription.objects
        .filter(company=company)
        .select_related("plan")
        .first()
    )

    today = timezone.localdate()

    subscription_status = "none"
    subscription_label = "Cuenta activa"

    trial_days_remaining = None
    trial_day_number = None

    is_trial = False
    is_premium = False

    if subscription:

        # =====================================================
        # TRIAL
        # =====================================================
        if subscription.status == Subscription.STATUS_TRIAL:

            is_trial = True
            subscription_status = "trial"
            subscription_label = "Prueba gratuita activa"

            if subscription.trial_ends_at:

                days_remaining = (
                    subscription.trial_ends_at - today
                    ).days
                trial_days_remaining = max(
                    0,
                    min(days_remaining, 14),
                    )

                trial_days_remaining = max(
                    days_remaining,
                    0,
                )

                total_trial_days = 14

                trial_day_number = (
                    total_trial_days
                    - trial_days_remaining
                    + 1
                )

                trial_day_number = max(
                    1,
                    min(
                        trial_day_number,
                        total_trial_days,
                    ),
                )

        # =====================================================
        # SUSCRIPCIÓN ACTIVA
        # =====================================================
        elif (
            subscription.status
            == Subscription.STATUS_ACTIVE
            and subscription.is_valid
        ):

            subscription_status = "active"
            subscription_label = "Plan activo"

            if (
                subscription.plan_id
                and subscription.plan
                and subscription.plan.has_whatsapp
            ):
                is_premium = True
                subscription_label = "Premium activo"

        # =====================================================
        # VENCIDA / INVÁLIDA
        # =====================================================
        elif not subscription.is_valid:

            subscription_status = "expired"
            subscription_label = "Suscripción vencida"

    context = {
        "onboarding_company": company,
        "warehouse_count": warehouse_count,

        "subscription": subscription,
        "subscription_status": subscription_status,
        "subscription_label": subscription_label,

        "trial_days_remaining": trial_days_remaining,
        "trial_day_number": trial_day_number,

        "is_trial": is_trial,
        "is_premium": is_premium,
    }

    return render(
        request,
        "companies/onboarding_wms360.html",
        context,
    )