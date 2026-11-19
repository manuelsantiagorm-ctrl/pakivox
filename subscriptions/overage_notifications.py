"""
Alertas al dueno cuando la empresa llega a su tope de mensajes de WhatsApp.

Se dispara una sola vez por periodo: reserve_whatsapp_message() marca
whatsapp_overage_notified_at de forma atomica y devuelve just_notified=True
unicamente en la primera ocasion.

Todo lo de aqui es de mejor esfuerzo: si una alerta falla, se reporta a
Sentry pero NUNCA se propaga el error. No poder avisar no debe romper el
flujo de mensajeria desde el que se llama.
"""

import sentry_sdk

from django.conf import settings
from django.core.mail import send_mail
from django.urls import reverse


def get_company_authorizer_profiles(company):
    """
    Perfiles activos que pueden autorizar el excedente: dueno y admin.
    Es el mismo criterio que usa la vista authorize_whatsapp_overage.
    """
    from accounts.models import UserProfile

    return UserProfile.objects.filter(
        company=company,
        role__in=["owner", "admin"],
        is_active=True,
    ).select_related("user")


def build_authorize_overage_url():
    base = getattr(
        settings,
        "PAKIVOX_BASE_URL",
        "https://www.pakivox.com",
    ).rstrip("/")

    path = reverse("subscriptions:authorize_whatsapp_overage")

    return f"{base}{path}"


def send_overage_email(company):
    """
    Avisa por correo a duenos y administradores, con la liga directa a la
    pantalla donde pueden autorizar.

    Manda un correo por destinatario, no uno con todos en copia: los
    correos de unos no tienen por que quedar expuestos a los otros.

    Devuelve cuantos correos se entregaron al backend de email.
    """
    recipients = sorted(
        {
            profile.user.email
            for profile in get_company_authorizer_profiles(company)
            if profile.user_id and profile.user.email
        }
    )

    if not recipients:
        return 0

    authorize_url = build_authorize_overage_url()

    subject = "PAKIVOX: tus mensajes de WhatsApp están detenidos"

    message = (
        f"Hola,\n\n"
        f"{company.name} llegó al límite de mensajes de WhatsApp "
        f"incluidos en su plan.\n\n"
        f"Para no cobrarte nada que no hayas aprobado, PAKIVOX detuvo "
        f"los mensajes en lugar de seguir enviándolos. No se perdió "
        f"ninguno: están guardados y esperando tu autorización.\n\n"
        f"Puedes autorizar el envío aquí:\n\n"
        f"{authorize_url}\n\n"
        f"Los mensajes que superen tu plan se cobran aparte. Si "
        f"prefieres no autorizar nada, los mensajes detenidos saldrán "
        f"solos cuando tu cupo se renueve el próximo periodo, y no se "
        f"te cobrará excedente.\n\n"
        f"PAKIVOX\n"
        f"Vende, opera y escala."
    )

    from_email = getattr(
        settings,
        "DEFAULT_FROM_EMAIL",
        "PAKIVOX <no-reply@pakivox.local>",
    )

    delivered = 0

    for recipient in recipients:
        try:
            delivered += send_mail(
                subject=subject,
                message=message,
                from_email=from_email,
                recipient_list=[recipient],
                fail_silently=False,
            )
        except Exception as error:
            sentry_sdk.capture_exception(error)

    return delivered


def notify_whatsapp_overage(company):
    """
    Punto de entrada unico para avisar que se alcanzo el tope.

    De mejor esfuerzo por diseno: cualquier fallo se reporta a Sentry y se
    traga. Aqui se iran sumando los demas canales (letrero en el inicio,
    WhatsApp al dueno) conforme se construyan.
    """
    if not company:
        return

    try:
        send_overage_email(company)
    except Exception as error:
        sentry_sdk.capture_exception(error)
