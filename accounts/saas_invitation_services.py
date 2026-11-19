"""
Servicio para invitación segura de clientes SaaS nuevos (BUG-102).

El Owner captura los datos de la empresa/plan/canal y envía una
invitación de un solo uso. El cliente define su propia contraseña
al aceptar; el Owner nunca la conoce ni la escribe.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from accounts.models import SaaSClientInvitation, UserProfile
from companies.models import Company
from inventory.models import Warehouse
from messaging.models import WhatsAppNumber

User = get_user_model()

INVITATION_TOKEN_HOURS = 72


class SaaSClientInvitationError(Exception):
    """Error controlado del flujo de invitación de clientes SaaS."""


class InvalidInvitationToken(SaaSClientInvitationError):
    pass


class ExpiredInvitationToken(SaaSClientInvitationError):
    pass


def create_saas_client_invitation(
    *,
    invited_by,
    company_name,
    admin_email,
    admin_name,
    plan,
    subscription_days,
    whatsapp_data=None,
):
    """
    Crea una invitación pendiente. Devuelve (invitation, raw_token).

    whatsapp_data es un dict opcional con las llaves de
    SaaSClientQuickCreateForm para el canal Twilio inicial.
    """
    whatsapp_data = whatsapp_data or {}

    normalized_email = str(admin_email or "").strip().lower()

    if User.objects.filter(username__iexact=normalized_email).exists():
        raise SaaSClientInvitationError(
            "Ya existe una cuenta activa con este correo."
        )

    active_pending_exists = SaaSClientInvitation.objects.filter(
        admin_email__iexact=normalized_email,
        status=SaaSClientInvitation.STATUS_PENDING,
        expires_at__gt=timezone.now(),
    ).exists()

    if active_pending_exists:
        raise SaaSClientInvitationError(
            "Este correo ya tiene una invitación pendiente."
        )

    invitation = SaaSClientInvitation(
        company_name=company_name,
        admin_email=normalized_email,
        admin_name=admin_name,
        plan=plan,
        subscription_days=subscription_days,
        whatsapp_phone_number_id=whatsapp_data.get("phone_number_id", ""),
        whatsapp_display_number=whatsapp_data.get("display_number", ""),
        whatsapp_channel_name=whatsapp_data.get("channel_name", ""),
        whatsapp_twilio_account_sid=whatsapp_data.get("twilio_account_sid", ""),
        whatsapp_twilio_auth_token=whatsapp_data.get("twilio_auth_token", ""),
        whatsapp_twilio_whatsapp_from=whatsapp_data.get("twilio_whatsapp_from", ""),
        whatsapp_twilio_enabled=bool(whatsapp_data.get("twilio_enabled", False)),
        whatsapp_initial_credits=whatsapp_data.get("initial_credits", 0),
        whatsapp_precio_por_mensaje=whatsapp_data.get("precio_por_mensaje", 0),
        invited_by=invited_by,
    )

    raw_token = invitation.generate_invitation_token(hours=INVITATION_TOKEN_HOURS)
    invitation.save()

    return invitation, raw_token


def build_invitation_url(request, raw_token):
    path = reverse(
        "accounts:accept_saas_client_invitation",
        kwargs={"token": raw_token},
    )
    return request.build_absolute_uri(path)


def send_saas_client_invitation_email(*, request, invitation, raw_token):
    invitation_url = build_invitation_url(request, raw_token)

    subject = f"Invitación para activar {invitation.company_name} en WMS360"

    message = (
        f"Hola {invitation.admin_name},\n\n"
        f"Fuiste invitado a activar la cuenta de "
        f"{invitation.company_name} en WMS360.\n\n"
        f"Define tu contraseña y activa tu cuenta usando este enlace:\n\n"
        f"{invitation_url}\n\n"
        f"El enlace vence en {INVITATION_TOKEN_HOURS} horas.\n\n"
        f"Si no esperabas este correo, puedes ignorarlo.\n\n"
        f"WMS360"
    )

    from_email = getattr(
        settings, "DEFAULT_FROM_EMAIL", "WMS360 <no-reply@wms360.local>"
    )

    return send_mail(
        subject=subject,
        message=message,
        from_email=from_email,
        recipient_list=[invitation.admin_email],
        fail_silently=False,
    )


def get_invitation_for_token(raw_token):
    token_hash = SaaSClientInvitation.hash_token(raw_token)
    return SaaSClientInvitation.objects.filter(token_hash=token_hash).first()


@transaction.atomic
def accept_saas_client_invitation(*, raw_token, raw_password):
    """
    Verifica el token y crea atómicamente:
    Company, User, UserProfile, Subscription, Warehouse principal,
    y opcionalmente el canal WhatsApp si se capturaron datos.

    El cliente define su propia contraseña aquí; el Owner nunca la vio.
    """
    from owner.views import create_subscription_for_company

    invitation = get_invitation_for_token(raw_token)

    if not invitation:
        raise InvalidInvitationToken("El enlace de invitación no es válido.")

    if not invitation.token_matches(raw_token):
        raise InvalidInvitationToken("El enlace de invitación no es válido.")

    if invitation.status == SaaSClientInvitation.STATUS_ACCEPTED:
        raise InvalidInvitationToken("Esta invitación ya fue utilizada.")

    if invitation.is_expired:
        invitation.mark_expired()
        raise ExpiredInvitationToken("El enlace de invitación ha expirado.")

    if invitation.status != SaaSClientInvitation.STATUS_PENDING:
        raise InvalidInvitationToken("Esta invitación ya no puede aceptarse.")

    if User.objects.filter(username__iexact=invitation.admin_email).exists():
        raise SaaSClientInvitationError(
            "Ya existe una cuenta activa con este correo."
        )

    try:
        company = Company.objects.create(name=invitation.company_name)
    except IntegrityError:
        raise SaaSClientInvitationError(
            "Ya existe una empresa con un nombre equivalente."
        )

    first_name = invitation.admin_name.split(" ", 1)[0] if invitation.admin_name else ""
    last_name = (
        invitation.admin_name.split(" ", 1)[1]
        if invitation.admin_name and " " in invitation.admin_name
        else ""
    )

    user = User.objects.create_user(
        username=invitation.admin_email,
        email=invitation.admin_email,
        password=raw_password,
        first_name=first_name,
        last_name=last_name,
        is_active=True,
    )

    UserProfile.objects.create(
        user=user,
        company=company,
        role="owner",
        is_active=True,
    )

    # BUG-103: bodega principal automática.
    Warehouse.objects.create(
        company=company,
        name="Bodega Principal",
        code="PRINCIPAL",
    )

    create_subscription_for_company(
        company=company,
        plan=invitation.plan,
        days=invitation.subscription_days,
    )

    if invitation.whatsapp_phone_number_id or invitation.whatsapp_display_number:
        WhatsAppNumber.objects.create(
            company=company,
            provider=WhatsAppNumber.PROVIDER_TWILIO,
            phone_number_id=invitation.whatsapp_phone_number_id,
            display_number=invitation.whatsapp_display_number,
            channel_name=invitation.whatsapp_channel_name,
            twilio_account_sid=invitation.whatsapp_twilio_account_sid,
            twilio_auth_token=invitation.whatsapp_twilio_auth_token,
            twilio_whatsapp_from=invitation.whatsapp_twilio_whatsapp_from,
            twilio_enabled=invitation.whatsapp_twilio_enabled,
            twilio_sandbox_mode=True,
            creditos_disponibles=invitation.whatsapp_initial_credits,
            precio_por_mensaje=invitation.whatsapp_precio_por_mensaje,
            is_active=True,
        )

    invitation.mark_accepted(user=user, company=company)

    return {
        "company": company,
        "user": user,
    }