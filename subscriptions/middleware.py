import sentry_sdk
from django.shortcuts import redirect
from django.utils import timezone
from django.contrib import messages
from django.urls import reverse

from inventory.models import Warehouse  # Importación de tu modelo Warehouse


class WMS360SubscriptionAccessMiddleware:
    """
    Candado mensual SaaS y Guardián de Configuración Inicial para WMS360.

    Reglas de negocio:
    1. Superusuarios siempre entran.
    2. Rutas libres (Login, Logout, Pagos, Assets) no se bloquean.
    3. Si la empresa no existe o no tiene suscripción activa, bloquea el acceso.
    4. Si la empresa tiene suscripción pero NO TIENE BODEGAS, redirige al Onboarding de Bodega.
    """

    PROTECTED_PREFIXES = [
        "/dashboard/",
        "/crm/",
        "/inventory/",
        "/sales/",
        "/payments/",
        "/purchases/",
        "/warehouse/",
        "/messaging/",
    ]

    FREE_PREFIXES = [
        "/admin/",
        "/owner/",
        "/subscriptions/",
        "/accounts/",
        "/login/",
        "/logout/",
        "/static/",
        "/media/",
        "/empresa/",  # Permitir flujo de empresas/bodegas
        "/empresas/",
    ]

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path

        if self.is_free_path(path):
            return self.get_response(request)

        if not request.user.is_authenticated:
            return self.get_response(request)

        if request.user.is_superuser:
            return self.get_response(request)

        if not self.is_protected_path(path):
            return self.get_response(request)

        company = self.get_user_company(request.user)

        # 1. SI NO TIENE EMPRESA (Usuario nuevo), mandarlo al Onboarding/Registro
        if not company:
            return (
                redirect("accounts:onboarding_empresa")
                if self.has_route("accounts:onboarding_empresa")
                else redirect("/accounts/registro/")
            )

        subscription = self.get_company_subscription(company)

        # 2. SI TIENE EMPRESA PERO SU SUSCRIPCIÓN ESTÁ VENCIDA
        if not self.subscription_is_valid(subscription):
            return redirect("subscriptions:subscription_expired")

        # 3. 🛡️ GUARDIÁN DE BODEGA: SI TIENE EMPRESA Y SUSCRIPCIÓN PERO NO TIENE BODEGAS
        has_warehouses = Warehouse.objects.filter(company=company).exists()
        if not has_warehouses:
            # Si intenta navegar a una ruta protegida sin bodegas, enviarlo a crear la primera
            if path != reverse("companies:warehouse_create"):
                messages.info(
                    request,
                    "Para comenzar a operar en el sistema, debes configurar tu primera bodega.",
                )
                return redirect("companies:warehouse_create")

        return self.get_response(request)

    def is_free_path(self, path):
        return any(path.startswith(prefix) for prefix in self.FREE_PREFIXES)

    def is_protected_path(self, path):
        if path == "/":
            return True

        return any(path.startswith(prefix) for prefix in self.PROTECTED_PREFIXES)

    def get_user_company(self, user):
        profile = getattr(user, "profile", None) or getattr(user, "userprofile", None)

        if profile and getattr(profile, "company_id", None):
            return profile.company

        company = getattr(user, "company", None)

        if company:
            return company

        return None

    def get_company_subscription(self, company):
        try:
            subscription = getattr(company, "subscription", None)

            if subscription:
                return subscription
        except Exception:
            pass

        try:
            from subscriptions.models import Subscription

            return (
                Subscription.objects.filter(company=company)
                .select_related("plan")
                .order_by("-id")
                .first()
            )
        except Exception as error:
            sentry_sdk.capture_exception(error)
            return None

    def subscription_is_valid(self, subscription):
        if not subscription:
            return False

        is_valid = getattr(subscription, "is_valid", None)

        if isinstance(is_valid, bool):
            return is_valid

        if callable(is_valid):
            try:
                return bool(is_valid())
            except Exception as error:
                sentry_sdk.capture_exception(error)

        status = str(getattr(subscription, "status", "") or "").lower()

        # Aceptar 'trialing' como válido para permitir entrar
        if status in ["active", "paid", "valid", "trialing"]:
            return True

        if status in ["suspended", "expired", "cancelled", "inactive"]:
            return False

        active_value = getattr(subscription, "is_active", None)

        if isinstance(active_value, bool) and not active_value:
            return False

        today = timezone.localdate()
        now = timezone.now()

        possible_due_fields = [
            "trial_ends_at",
            "fecha_proximo_pago",
            "next_payment_date",
            "next_billing_date",
            "expires_at",
            "ends_at",
            "end_date",
            "current_period_end",
        ]

        for field_name in possible_due_fields:
            if hasattr(subscription, field_name):
                due_value = getattr(subscription, field_name)

                if not due_value:
                    continue

                if hasattr(due_value, "hour"):
                    return due_value >= now

                return due_value >= today

        if isinstance(active_value, bool):
            return active_value

        return False

    def has_route(self, route_name):
        try:
            reverse(route_name)
            return True
        except Exception:
            return False
