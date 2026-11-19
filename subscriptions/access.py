from functools import wraps

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect

from subscriptions.models import Subscription


class WhatsAppPremiumAccessDenied(PermissionDenied):
    """
    Se lanza cuando una operación backend intenta utilizar WhatsApp
    sin una suscripción Premium confirmada por Stripe.
    """


def get_company_subscription(company):
    """
    Obtiene exclusivamente la suscripción de la empresa indicada.

    Nunca acepta company_id proveniente del frontend y nunca utiliza
    una suscripción de otra empresa como fallback.
    """
    if not company or not getattr(company, "pk", None):
        return None

    return (
        Subscription.objects.filter(company_id=company.pk)
        .select_related("company", "plan")
        .first()
    )


def has_whatsapp_premium_access(company):
    """
    Regla central de acceso a WhatsApp Premium.

    Requisitos obligatorios:
    - Empresa existente.
    - Suscripción existente.
    - Estado active.
    - Trial bloqueado.
    - Suscripción vigente.
    - Plan activo.
    - Plan con has_whatsapp=True.
    - Customer y Subscription reales de Stripe.
    - Evidencia de webhook firmado y pagado.
    """
    subscription = get_company_subscription(company)

    if not subscription:
        return False

    return bool(subscription.has_whatsapp_access)


def get_whatsapp_premium_denial_reason(company):
    """
    Devuelve una explicación segura para interfaz y auditoría.
    No expone IDs, tokens ni datos sensibles de Stripe.
    """
    subscription = get_company_subscription(company)

    if not subscription:
        return "La empresa no tiene una suscripción."

    if subscription.is_trial:
        return "WhatsApp Premium no está disponible durante el periodo de prueba."

    if subscription.status != Subscription.STATUS_ACTIVE:
        return "La suscripción no está activa."

    if not subscription.is_valid:
        return "La suscripción está vencida o no es válida."

    plan = subscription.plan

    if not plan or not plan.is_active:
        return "El plan contratado no está activo."

    if not plan.has_whatsapp:
        return "El plan contratado no incluye WhatsApp Premium."

    if not subscription.stripe_customer_id:
        return "La suscripción no tiene un cliente de Stripe confirmado."

    if not subscription.stripe_subscription_id:
        return "La suscripción no tiene una contratación de Stripe confirmada."

    if not subscription.stripe_verified_at:
        return "Stripe todavía no ha confirmado el pago mediante webhook."

    if not subscription.stripe_last_paid_event_id:
        return "No existe evidencia de un evento pagado de Stripe."
    
    
    if not subscription.plan.stripe_price_id:
        return "El plan Premium no tiene configurado su Price ID de Stripe."

    if not subscription.stripe_verified_price_id:
        return "Stripe todavía no ha confirmado el precio contratado."

    if subscription.stripe_verified_price_id != subscription.plan.stripe_price_id:
        return "El precio pagado en Stripe no corresponde al plan actual."

    return "WhatsApp Premium no está disponible para esta empresa."


def ensure_whatsapp_premium_access(company):
    """
    Candado para servicios, APIs, modelos y tareas internas.

    Retorna la suscripción válida o lanza una excepción. De esta manera,
    no dependemos de que la interfaz haya ocultado un botón.
    """
    subscription = get_company_subscription(company)

    if not subscription or not subscription.has_whatsapp_access:
        raise WhatsAppPremiumAccessDenied(get_whatsapp_premium_denial_reason(company))

    return subscription


def whatsapp_premium_required(view_function):
    """
    Decorador para vistas Django pertenecientes a WhatsApp Premium.

    La empresa siempre se obtiene del perfil autenticado; nunca de GET,
    POST, JSON ni parámetros manipulables por el navegador.
    """

    @wraps(view_function)
    def wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("accounts:login")

        profile = getattr(request.user, "profile", None)
        company = getattr(profile, "company", None) if profile else None

        if not company:
            messages.error(
                request,
                "Tu usuario no tiene una empresa válida asignada.",
            )
            return redirect("subscriptions:subscription_expired")

        if not has_whatsapp_premium_access(company):
            messages.error(
                request,
                get_whatsapp_premium_denial_reason(company),
            )
            return redirect("subscriptions:subscription_expired")

        request.whatsapp_subscription = get_company_subscription(company)
        request.whatsapp_company = company

        return view_function(request, *args, **kwargs)

    return wrapped_view
