import sentry_sdk
import stripe

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse

from subscriptions.models import Plan, Subscription
from subscriptions.webhooks import WMS360_PRODUCT_CODE

@login_required
def subscription_expired(request):
    """
    Pantalla de acceso restringido.

    Llegan aquí dos casos distintos y antes se mostraban igual ("Suscripción
    vencida"):

    1. La suscripción de la empresa ya no está vigente -> se ofrece renovar.
    2. La suscripción SÍ está vigente pero la empresa abrió un módulo que su
       plan no incluye (por ejemplo WhatsApp sin pago confirmado en Stripe).
       Aquí no se ofrece "Reactivar" (crearía un segundo cobro); se explica
       qué falta y se regresa al panel.

    No se muestran precios fijos: el precio vigente lo confirma Stripe.
    """
    from subscriptions.access import get_company_subscription

    company = get_user_company(request.user)
    subscription = get_company_subscription(company) if company else None
    vigente = bool(subscription and subscription.is_valid)

    profile = getattr(request.user, "profile", None)
    puede_contratar = bool(
        company
        and profile
        and getattr(profile, "role", "") in {"owner", "admin"}
        and not subscription_is_verified_premium(subscription)
    )
    planes = get_purchasable_plans() if puede_contratar else []
    en_prueba = bool(
        subscription and subscription.status == Subscription.STATUS_TRIAL
    )

    return render(
        request,
        "subscriptions/subscription_expired.html",
        {
            "suscripcion": subscription,
            "suscripcion_vigente": vigente,
            "plan_nombre": getattr(getattr(subscription, "plan", None), "name", ""),
            "planes_contratables": planes,
            "puede_contratar": puede_contratar,
            "en_prueba": en_prueba,
        },
    )


def stripe_get(obj, key, default=None):
    """
    Lee datos tanto de objetos StripeObject como de diccionarios.
    """
    try:
        return obj[key]
    except (KeyError, TypeError):
        pass

    try:
        return getattr(obj, key)
    except (AttributeError, TypeError):
        return default


def get_user_company(user):
    """
    Obtiene la empresa exclusivamente desde el perfil autenticado.

    Nunca acepta IDs de empresa recibidos mediante GET, POST o JSON.
    """
    if not user or not user.is_authenticated:
        return None

    profile = getattr(user, "profile", None)

    if not profile:
        return None

    if not getattr(profile, "company_id", None):
        return None

    return profile.company


def get_company_subscription(company):
    """
    Obtiene exclusivamente la suscripción de la empresa indicada.
    """
    if not company or not getattr(company, "pk", None):
        return None

    return (
        Subscription.objects
        .filter(company_id=company.pk)
        .select_related("company", "plan")
        .first()
    )


def get_whatsapp_premium_plan():
    """
    Obtiene el único plan Premium asociado al Price ID configurado.

    Si no existe exactamente un plan activo con WhatsApp para ese
    Price ID, la configuración se considera inválida.
    """
    stripe_price_id = str(
        getattr(
            settings,
            "STRIPE_WMS360_PRICE_ID",
            "",
        )
        or ""
    ).strip()

    if not stripe_price_id:
        return None

    plans = Plan.objects.filter(
        stripe_price_id=stripe_price_id,
        has_whatsapp=True,
        is_active=True,
    )

    if plans.count() != 1:
        return None

    return plans.first()


# Planes que un dueño puede contratar por su cuenta desde PAKIVOX. El precio
# sale siempre del Plan en la base (stripe_price_id), nunca del navegador.
SELF_SERVICE_PLAN_SLUGS = ("esencial", "profesional", "maximo")


def get_purchasable_plans():
    """Planes de pago activos y conectados a un precio real de Stripe."""
    plans = Plan.objects.filter(
        slug__in=SELF_SERVICE_PLAN_SLUGS,
        is_active=True,
        stripe_price_id__startswith="price_",
    ).order_by("price", "id")
    return list(plans)


def get_purchasable_plan(slug):
    slug = str(slug or "").strip().lower()
    if slug not in SELF_SERVICE_PLAN_SLUGS:
        return None
    for plan in get_purchasable_plans():
        if plan.slug == slug:
            return plan
    return None


def subscription_is_verified_premium(subscription):
    """
    Indica si la suscripción local está activa, vigente,
    vinculada a Stripe y correctamente verificada.

    Una suscripción con status="active" pero con su vigencia vencida
    NO debe considerarse Premium activa.

    Esta comprobación nunca consulta datos de otra empresa.
    """
    if not subscription:
        return False

    plan = getattr(subscription, "plan", None)

    if not plan:
        return False

    if not plan.is_active:
        return False

    if not plan.has_whatsapp:
        return False

    if not subscription.is_valid:
        return False

    customer_id = str(subscription.stripe_customer_id or "").strip()

    stripe_subscription_id = str(subscription.stripe_subscription_id or "").strip()

    paid_event_id = str(subscription.stripe_last_paid_event_id or "").strip()

    verified_price_id = str(subscription.stripe_verified_price_id or "").strip()

    plan_price_id = str(plan.stripe_price_id or "").strip()

    return (
        subscription.status == Subscription.STATUS_ACTIVE
        and customer_id.startswith("cus_")
        and stripe_subscription_id.startswith("sub_")
        and subscription.stripe_verified_at is not None
        and bool(paid_event_id)
        and bool(verified_price_id)
        and bool(plan_price_id)
        and verified_price_id == plan_price_id
    )


@login_required
def create_stripe_checkout_session(request):
    """
    Crea una sesión de Stripe Checkout para WhatsApp Premium.

    La empresa procede exclusivamente del perfil del usuario autenticado.
    El plan procede exclusivamente del Price ID configurado en backend.
    Ninguno de esos valores se acepta desde el navegador.
    """
    if request.method != "POST":
        return redirect(
            "subscriptions:subscription_expired"
        )

    company = get_user_company(request.user)

    if not company:
        messages.error(
            request,
            "No encontramos una empresa válida asociada a tu usuario.",
        )
        return redirect(
            "subscriptions:subscription_expired"
        )
    # BUG-015: solo owner/admin pueden iniciar un cobro real con Stripe.
    profile = getattr(request.user, "profile", None)
    if not profile or profile.role not in {"owner", "admin"}:
        messages.error(
            request,
            "Solo el propietario o administrador de la empresa "
            "puede iniciar la contratación de un plan de pago.",
        )
        return redirect("subscriptions:subscription_expired")

    subscription = get_company_subscription(company)

    if not subscription:
        messages.error(
            request,
            "La empresa no tiene una suscripción registrada.",
        )
        return redirect(
            "subscriptions:subscription_expired"
        )

    # Impide crear otra suscripción cuando la empresa ya tiene
    # WhatsApp Premium activo y verificado.
    if subscription_is_verified_premium(subscription):
        messages.info(
            request,
            "Tu empresa ya tiene WhatsApp Premium activo y verificado.",
        )
        return redirect("/")

    stripe_secret_key = str(
        getattr(
            settings,
            "STRIPE_SECRET_KEY",
            "",
        )
        or ""
    ).strip()

    if not stripe_secret_key:
        messages.error(
            request,
            "Stripe no está configurado correctamente.",
        )
        return redirect(
            "subscriptions:subscription_expired"
        )

    requested_slug = str(request.POST.get("plan", "") or "").strip()

    if requested_slug:
        # Esencial / Profesional / Máximo: el slug solo elige entre planes
        # ya configurados en la base; el precio nunca viene del navegador.
        premium_plan = get_purchasable_plan(requested_slug)

        if not premium_plan:
            messages.error(
                request,
                "Ese plan todavía no se puede contratar en línea. "
                "Escríbenos y te ayudamos a activarlo.",
            )
            return redirect("subscriptions:subscription_expired")
    else:
        premium_plan = get_whatsapp_premium_plan()

    if not premium_plan:
        messages.error(
            request,
            (
                "La configuración Premium es inválida: "
                "el Price ID debe corresponder exactamente "
                "a un plan activo con WhatsApp."
            ),
        )
        return redirect(
            "subscriptions:subscription_expired"
        )

    stripe.api_key = stripe_secret_key

    success_url = (
        request.build_absolute_uri(
            reverse("subscriptions:checkout_success")
        )
        + "?session_id={CHECKOUT_SESSION_ID}"
    )

    cancel_url = request.build_absolute_uri(
        reverse("subscriptions:subscription_expired")
    )

    metadata = {
        "company_id": str(company.pk),
        "user_id": str(request.user.pk),
        "plan_id": str(premium_plan.pk),
        "plan_slug": premium_plan.slug,
        "product": WMS360_PRODUCT_CODE,
    }

    customer_options = {}

    stripe_customer_id = str(
        subscription.stripe_customer_id or ""
    ).strip()

    if stripe_customer_id.startswith("cus_"):
        customer_options["customer"] = stripe_customer_id
    else:
        customer_email = str(
            request.user.email
            or request.user.username
            or ""
        ).strip()

        if customer_email:
            customer_options["customer_email"] = customer_email

    try:
        checkout_session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[
                {
                    "price": premium_plan.stripe_price_id,
                    "quantity": 1,
                }
            ],
            client_reference_id=str(company.pk),
            metadata=metadata,
            subscription_data={
                "metadata": metadata,
            },
            success_url=success_url,
            cancel_url=cancel_url,
            **customer_options,
        )

        checkout_url = str(
            stripe_get(
                checkout_session,
                "url",
                "",
            )
            or ""
        ).strip()

        if not checkout_url:
            messages.error(
                request,
                "Stripe no devolvió una URL de pago válida.",
            )
            return redirect(
                "subscriptions:subscription_expired"
            )

        return redirect(checkout_url)

    except Exception as error:
        sentry_sdk.capture_exception(error)
        # No mostramos datos internos de Stripe ni información sensible.
        messages.error(
            request,
            (
                "No se pudo iniciar el pago en este momento. "
                "Intenta nuevamente."
            ),
        )
        return redirect(
            "subscriptions:subscription_expired"
        )


@login_required
def checkout_success(request):
    """
    Verifica el regreso desde Stripe Checkout.

    Esta vista nunca activa, renueva, cambia de plan ni modifica la
    suscripción. La activación únicamente puede realizarla el webhook
    firmado de Stripe.

    La pantalla solo presenta una activación completada cuando coinciden:

    - La empresa del usuario autenticado.
    - La empresa incluida en Checkout.
    - El producto y plan Premium configurados en backend.
    - El Customer de Stripe.
    - La Subscription de Stripe.
    - La suscripción local activa y verificada mediante webhook.
    """
    session_id = str(
        request.GET.get("session_id", "") or ""
    ).strip()

    if not session_id.startswith("cs_"):
        messages.error(
            request,
            "Stripe regresó sin una sesión de pago válida.",
        )
        return redirect(
            "subscriptions:subscription_expired"
        )

    company = get_user_company(request.user)

    if not company:
        messages.error(
            request,
            "No encontramos una empresa válida asociada a tu usuario.",
        )
        return redirect(
            "subscriptions:subscription_expired"
        )

    subscription = get_company_subscription(company)

    if not subscription:
        messages.error(
            request,
            "Tu empresa no tiene una suscripción local registrada.",
        )
        return redirect(
            "subscriptions:subscription_expired"
        )

    premium_plan = get_whatsapp_premium_plan()

    if not premium_plan:
        messages.error(
            request,
            "La configuración del plan WhatsApp Premium no es válida.",
        )
        return redirect(
            "subscriptions:subscription_expired"
        )

    stripe_secret_key = str(
        getattr(
            settings,
            "STRIPE_SECRET_KEY",
            "",
        )
        or ""
    ).strip()

    if not stripe_secret_key:
        messages.error(
            request,
            "Stripe no está configurado correctamente.",
        )
        return redirect(
            "subscriptions:subscription_expired"
        )

    stripe.api_key = stripe_secret_key

    try:
        stripe_session = stripe.checkout.Session.retrieve(
            session_id
        )

        checkout_status = str(
            stripe_get(
                stripe_session,
                "status",
                "",
            )
            or ""
        ).strip().lower()

        payment_status = str(
            stripe_get(
                stripe_session,
                "payment_status",
                "",
            )
            or ""
        ).strip().lower()

        mode = str(
            stripe_get(
                stripe_session,
                "mode",
                "",
            )
            or ""
        ).strip().lower()

        customer_id = str(
            stripe_get(
                stripe_session,
                "customer",
                "",
            )
            or ""
        ).strip()

        checkout_subscription_id = str(
            stripe_get(
                stripe_session,
                "subscription",
                "",
            )
            or ""
        ).strip()

        client_reference_id = str(
            stripe_get(
                stripe_session,
                "client_reference_id",
                "",
            )
            or ""
        ).strip()

        metadata = (
            stripe_get(
                stripe_session,
                "metadata",
                {},
            )
            or {}
        )

        metadata_company_id = str(
            stripe_get(
                metadata,
                "company_id",
                "",
            )
            or ""
        ).strip()

        metadata_user_id = str(
            stripe_get(
                metadata,
                "user_id",
                "",
            )
            or ""
        ).strip()

        metadata_plan_id = str(
            stripe_get(
                metadata,
                "plan_id",
                "",
            )
            or ""
        ).strip()

        metadata_plan_slug = str(
            stripe_get(
                metadata,
                "plan_slug",
                "",
            )
            or ""
        ).strip()

        product = str(
            stripe_get(
                metadata,
                "product",
                "",
            )
            or ""
        ).strip()

        expected_company_id = str(company.pk)
        expected_user_id = str(request.user.pk)
        expected_plan_id = str(premium_plan.pk)

        company_matches = (
            client_reference_id == expected_company_id
            and metadata_company_id == expected_company_id
        )

        user_matches = (
            metadata_user_id == expected_user_id
        )

        plan_matches = (
            metadata_plan_id == expected_plan_id
            and metadata_plan_slug == premium_plan.slug
        )

        product_matches = (
            product == WMS360_PRODUCT_CODE
        )

        if (
            not company_matches
            or not user_matches
            or not plan_matches
            or not product_matches
            or mode != "subscription"
        ):
            messages.error(
                request,
                (
                    "La sesión de pago no corresponde al usuario, "
                    "empresa o plan Premium autenticado."
                ),
            )
            return redirect(
                "subscriptions:subscription_expired"
            )

        checkout_paid = (
            checkout_status == "complete"
            and payment_status == "paid"
            and customer_id.startswith("cus_")
            and checkout_subscription_id.startswith("sub_")
        )

        local_customer_id = str(
            subscription.stripe_customer_id or ""
        ).strip()

        local_subscription_id = str(
            subscription.stripe_subscription_id or ""
        ).strip()

        local_identity_matches = (
            local_customer_id == customer_id
            and local_subscription_id
            == checkout_subscription_id
        )

        webhook_verified = (
            subscription_is_verified_premium(subscription)
            and subscription.plan_id == premium_plan.pk
        )
        activation_complete = (
            checkout_paid
            and local_identity_matches
            and webhook_verified
        )

        existing_verified_identity = (
            subscription_is_verified_premium(subscription)
            and bool(local_subscription_id)
        )

        identity_conflict = (
            checkout_paid
            and existing_verified_identity
            and not local_identity_matches
        )

        if identity_conflict:
            messages.error(
                request,
                (
                    "El pago regresado no coincide con la suscripción "
                    "verificada de tu empresa. No se modificó tu acceso."
                ),
            )
            return redirect(
                "subscriptions:subscription_expired"
            )

        if activation_complete:
            messages.success(
                request,
                "WhatsApp Premium fue activado y verificado correctamente.",
            )
        elif checkout_paid:
            messages.info(
                request,
                (
                    "Stripe recibió el pago. Estamos esperando la "
                    "confirmación firmada para liberar Premium. "
                    "No vuelvas a realizar el pago."
                ),
            )
        else:
            messages.warning(
                request,
                (
                    "Stripe todavía no confirma completamente el pago. "
                    "WhatsApp Premium continuará bloqueado."
                ),
            )

        return render(
            request,
            "subscriptions/checkout_success.html",
            {
                "session_id": session_id,
                "activation_complete": activation_complete,
                "activation_pending": (
                    checkout_paid
                    and not activation_complete
                ),
                "company": company,
            },
        )

    except stripe.error.StripeError:
        messages.error(
            request,
            "No pudimos consultar la sesión de pago en Stripe.",
        )
        return redirect(
            "subscriptions:subscription_expired"
        )

    except Exception as error:
        sentry_sdk.capture_exception(error)
        messages.error(
            request,
            (
                "No pudimos verificar el pago. "
                "La suscripción no fue activada desde esta pantalla."
            ),
        )
        return redirect(
            "subscriptions:subscription_expired"
        )


@login_required
def authorize_whatsapp_overage(request):
    """
    Permite al dueno autorizar el excedente de mensajes de WhatsApp del
    periodo actual y reenviar la cola que quedo detenida.

    GET  -> muestra la situacion y pide confirmacion explicita.
    POST -> autoriza el excedente y reintenta la cola.

    El reintento es "a lo mas una vez": cada mensaje se saca de la cola
    dentro de su propia transaccion ANTES de llamar al proveedor. Si el
    envio falla, el Message queda marcado como fallido y visible en el
    hilo, pero nunca se le manda dos veces el mismo mensaje al cliente.
    """
    from django.db import transaction
    from django.utils import timezone

    from messaging.models import WhatsAppPendingMessage
    from subscriptions.whatsapp_queue import drain_whatsapp_pending_queue
    from subscriptions.whatsapp_quota import (
        get_whatsapp_quota_summary,
        reset_whatsapp_period_if_needed,
    )

    company = get_user_company(request.user)

    if not company:
        messages.error(
            request,
            "No encontramos una empresa valida asociada a tu usuario.",
        )
        return redirect("subscriptions:subscription_expired")

    profile = getattr(request.user, "profile", None)

    if not profile or profile.role not in {"owner", "admin"}:
        messages.error(
            request,
            "Solo el propietario o administrador de la empresa "
            "puede autorizar el excedente de mensajes de WhatsApp.",
        )
        return redirect("subscriptions:subscription_expired")

    subscription = get_company_subscription(company)

    if not subscription:
        messages.error(
            request,
            "La empresa no tiene una suscripcion registrada.",
        )
        return redirect("subscriptions:subscription_expired")

    pending_queryset = WhatsAppPendingMessage.objects.filter(
        company=company,
    ).order_by("created_at")

    # ==========================================================
    # GET: SOLO INFORMAR Y PEDIR CONFIRMACION
    # ==========================================================

    if request.method != "POST":
        return render(
            request,
            "subscriptions/whatsapp_overage_authorize.html",
            {
                "company": company,
                "subscription": subscription,
                "quota": get_whatsapp_quota_summary(company),
                "pending_count": pending_queryset.count(),
            },
        )

    # ==========================================================
    # POST: AUTORIZAR EL EXCEDENTE
    # ==========================================================

    with transaction.atomic():
        locked_subscription = (
            Subscription.objects.select_for_update()
            .select_related("plan")
            .filter(pk=subscription.pk)
            .first()
        )

        # El orden importa. reset_whatsapp_period_if_needed() limpia
        # whatsapp_overage_authorized_at cuando detecta un cambio de
        # periodo. Si primero se autoriza y despues corre el reset (que
        # es justo lo que hace reserve_whatsapp_message durante el
        # reintento), el reset borra la autorizacion recien concedida y
        # el dueno termina pagando por una autorizacion que no quedo.
        # Por eso aqui se asienta el periodo ANTES de autorizar.
        if locked_subscription:
            reset_whatsapp_period_if_needed(locked_subscription)

        Subscription.objects.filter(pk=subscription.pk).update(
            whatsapp_overage_authorized_at=timezone.now(),
        )

        # ==========================================================
    # REENVIO DE LA COLA DETENIDA
    # ==========================================================

    sent_count, failed_count = drain_whatsapp_pending_queue(company)

    if sent_count:
        messages.success(
            request,
            f"Excedente autorizado. Se reenviaron {sent_count} "
            f"mensaje(s) que estaban detenidos.",
        )
    else:
        messages.success(
            request,
            "Excedente autorizado. No habia mensajes detenidos en la cola.",
        )

    if failed_count:
        messages.warning(
            request,
            f"{failed_count} mensaje(s) no se pudieron enviar y quedaron "
            f"marcados como fallidos en su conversacion.",
        )

    return redirect("subscriptions:authorize_whatsapp_overage")
