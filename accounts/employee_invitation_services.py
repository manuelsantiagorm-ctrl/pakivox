import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from accounts.models import (
    EmployeeAreaAssignment,
    EmployeeCapabilityAssignment,
    EmployeeInvitation,
    EmployeeJobAssignment,
    InvitationAreaAssignment,
    InvitationCapabilityAssignment,
    InvitationJobAssignment,
    InvitationWarehouseAccess,
    JobTemplate,
    UserProfile,
    UserWarehouseAccess,
    WorkArea,
)
from accounts.services import split_owner_name
from accounts.simple_employee_access import (
    activity_codes_from_job_codes,
    activity_labels,
)
from companies.models import Company
from subscriptions.models import Subscription

User = get_user_model()

INVITATION_TOKEN_HOURS = 48
INVITATION_ROLES = {
    "admin",
    "manager",
    "sales",
    "warehouse",
    "accounting",
}


class EmployeeInvitationError(Exception):
    """
    Error controlado durante una invitación de empleado.
    """


class EmployeeInvitationPermissionError(EmployeeInvitationError):
    """
    El usuario no puede administrar empleados.
    """


class EmployeeInvitationLimitError(EmployeeInvitationError):
    """
    La empresa agotó los usuarios permitidos por su plan.
    """


class InvalidEmployeeInvitationToken(EmployeeInvitationError):
    """
    El token no existe, fue revocado o ya fue utilizado.
    """


class ExpiredEmployeeInvitationToken(EmployeeInvitationError):
    """
    La invitación ya expiró.
    """


class EmployeeInvitationConfigurationError(EmployeeInvitationError):
    """
    La empresa no tiene una suscripción configurada.
    """


def normalize_email(value):
    return str(value or "").strip().lower()


def normalize_full_name(value):
    return " ".join(str(value or "").strip().split())


def hash_invitation_token(raw_token):
    return hashlib.sha256(str(raw_token or "").encode("utf-8")).hexdigest()


def get_actor_profile(actor):
    profile = getattr(
        actor,
        "profile",
        None,
    )

    if not profile:
        raise EmployeeInvitationPermissionError(
            "Tu usuario no tiene un perfil de empresa."
        )

    if not profile.is_active:
        raise EmployeeInvitationPermissionError("Tu perfil de empresa está inactivo.")

    if not profile.company.is_active:
        raise EmployeeInvitationPermissionError("La empresa se encuentra inactiva.")

    if profile.role not in {
        "owner",
        "admin",
    }:
        raise EmployeeInvitationPermissionError(
            "Solo el propietario o un administrador " "puede invitar empleados."
        )

    return profile


def expire_old_invitations(company):
    now = timezone.now()

    return EmployeeInvitation.objects.filter(
        company=company,
        status=EmployeeInvitation.STATUS_PENDING,
        expires_at__lte=now,
    ).update(
        status=EmployeeInvitation.STATUS_EXPIRED,
        updated_at=now,
    )


def get_company_user_capacity(company):
    """
    Devuelve el consumo actual del plan.

    Las invitaciones pendientes vigentes reservan un espacio.
    Los usuarios inactivos no consumen una licencia activa.
    """
    expire_old_invitations(company)

    try:
        subscription = Subscription.objects.select_related("plan").get(company=company)
    except Subscription.DoesNotExist:
        return {
            "subscription": None,
            "limit": None,
            "active_users": 0,
            "pending_invitations": 0,
            "reserved_total": 0,
            "remaining": None,
            "limit_reached": False,
        }

    active_users = UserProfile.objects.filter(
        company=company,
        is_active=True,
        user__is_active=True,
    ).count()

    pending_invitations = EmployeeInvitation.objects.filter(
        company=company,
        status=EmployeeInvitation.STATUS_PENDING,
        expires_at__gt=timezone.now(),
    ).count()

    user_limit = subscription.plan.max_users

    reserved_total = active_users + pending_invitations

    remaining = max(
        user_limit - reserved_total,
        0,
    )

    return {
        "subscription": subscription,
        "limit": user_limit,
        "active_users": active_users,
        "pending_invitations": pending_invitations,
        "reserved_total": reserved_total,
        "remaining": remaining,
        "limit_reached": reserved_total >= user_limit,
    }


@transaction.atomic
def create_employee_invitation(
    *,
    actor,
    full_name,
    email,
    role,
    job_template=None,
    job_templates=(),
    primary_job_template=None,
    areas=(),
    capabilities=(),
    warehouse_accesses=(),
):
    actor_profile = get_actor_profile(actor)
    company_id = actor_profile.company_id

    company = Company.objects.select_for_update().get(
        pk=company_id,
        is_active=True,
    )

    normalized_email = normalize_email(email)
    normalized_name = normalize_full_name(full_name)
    normalized_role = str(role or "").strip().lower()

    selected_jobs = list(job_templates)
    if job_template is not None and not selected_jobs:
        selected_jobs = [job_template]
    if primary_job_template is None:
        primary_job_template = job_template or (selected_jobs[0] if selected_jobs else None)

    if any(item.company_id != company.id for item in selected_jobs):
        raise EmployeeInvitationError("Uno de los puestos pertenece a otra empresa.")
    if primary_job_template and primary_job_template not in selected_jobs:
        raise EmployeeInvitationError("El puesto principal debe estar seleccionado.")
    if primary_job_template is not None and primary_job_template.company_id != company.id:
        raise EmployeeInvitationError("El puesto seleccionado pertenece a otra empresa.")

    if not normalized_name:
        raise EmployeeInvitationError("Escribe el nombre completo del empleado.")

    if not normalized_email:
        raise EmployeeInvitationError("Escribe un correo válido.")

    if normalized_role not in INVITATION_ROLES:
        raise EmployeeInvitationError("El rol seleccionado no es válido.")

    if User.objects.filter(
        username__iexact=normalized_email,
    ).exists():
        raise EmployeeInvitationError(
            "Ya existe un usuario registrado con este correo."
        )

    if User.objects.filter(
        email__iexact=normalized_email,
    ).exists():
        raise EmployeeInvitationError(
            "Ya existe un usuario registrado con este correo."
        )

    now = timezone.now()

    EmployeeInvitation.objects.filter(
        company=company,
        status=EmployeeInvitation.STATUS_PENDING,
        expires_at__lte=now,
    ).update(
        status=EmployeeInvitation.STATUS_EXPIRED,
        updated_at=now,
    )

    duplicate_invitation = EmployeeInvitation.objects.filter(
        company=company,
        email__iexact=normalized_email,
        status=EmployeeInvitation.STATUS_PENDING,
        expires_at__gt=now,
    ).exists()

    if duplicate_invitation:
        raise EmployeeInvitationError("Este correo ya tiene una invitación pendiente.")

    try:
        subscription = (
            Subscription.objects.select_for_update()
            .select_related("plan")
            .get(company=company)
        )
    except Subscription.DoesNotExist as error:
        raise EmployeeInvitationConfigurationError(
            "La empresa no tiene una suscripción configurada."
        ) from error

    active_users = UserProfile.objects.filter(
        company=company,
        is_active=True,
        user__is_active=True,
    ).count()

    pending_invitations = EmployeeInvitation.objects.filter(
        company=company,
        status=EmployeeInvitation.STATUS_PENDING,
        expires_at__gt=now,
    ).count()

    user_limit = subscription.plan.max_users

    if active_users + pending_invitations >= user_limit:
        raise EmployeeInvitationLimitError(
            f"Tu plan permite un máximo de {user_limit} "
            "usuarios activos. Actualiza tu plan para "
            "invitar a más empleados."
        )

    raw_token = secrets.token_urlsafe(32)

    invitation = EmployeeInvitation.objects.create(
        company=company,
        email=normalized_email,
        full_name=normalized_name,
        role=normalized_role,
        job_template=primary_job_template,
        token_hash=hash_invitation_token(raw_token),
        status=EmployeeInvitation.STATUS_PENDING,
        invited_by=actor,
        expires_at=(
            now
            + timedelta(
                hours=INVITATION_TOKEN_HOURS,
            )
        ),
    )

    capability_list = list(capabilities)
    if selected_jobs:
        capability_ids = set()
        capability_list = []
        for selected_job in selected_jobs:
            for capability in selected_job.capabilities.filter(is_active=True):
                if capability.pk not in capability_ids:
                    capability_ids.add(capability.pk)
                    capability_list.append(capability)
    area_list = list(areas)
    if not area_list and capability_list:
        area_codes = {item.area_code for item in capability_list}
        area_list = list(
            WorkArea.objects.filter(
                company=company, code__in=area_codes, is_active=True
            )
        )

    InvitationAreaAssignment.objects.bulk_create(
        [
            InvitationAreaAssignment(invitation=invitation, area=area)
            for area in area_list
            if area.company_id == company.id
        ]
    )
    InvitationCapabilityAssignment.objects.bulk_create(
        [
            InvitationCapabilityAssignment(
                invitation=invitation, capability=capability
            )
            for capability in capability_list
        ]
    )
    InvitationJobAssignment.objects.bulk_create(
        [
            InvitationJobAssignment(
                invitation=invitation,
                job_template=selected_job,
                is_primary=(selected_job.pk == primary_job_template.pk),
            )
            for selected_job in selected_jobs
        ]
    )
    InvitationWarehouseAccess.objects.bulk_create(
        [
            InvitationWarehouseAccess(
                invitation=invitation,
                warehouse=warehouse,
                access_level=access_level,
            )
            for warehouse, access_level in warehouse_accesses
            if warehouse.company_id == company.id
        ]
    )

    return invitation, raw_token


def build_employee_invitation_url(
    request,
    raw_token,
):
    invitation_path = reverse(
        "accounts:accept_employee_invitation",
        kwargs={
            "token": raw_token,
        },
    )

    return request.build_absolute_uri(invitation_path)


def send_employee_invitation_email(
    *,
    request,
    invitation,
    raw_token,
):
    invitation_url = build_employee_invitation_url(
        request=request,
        raw_token=raw_token,
    )

    invitation_job_codes = list(
        invitation.job_assignments.select_related("job_template").values_list(
            "job_template__code",
            flat=True,
        )
    )
    if not invitation_job_codes and invitation.job_template_id:
        invitation_job_codes = [invitation.job_template.code]

    simple_labels = activity_labels(
        activity_codes_from_job_codes(invitation_job_codes)
    )
    activity_text = ", ".join(simple_labels) or "Acceso configurado por tu empresa"

    subject = f"{invitation.company.name} te invitó a PAKIVOX"

    message = (
        f"Hola {invitation.full_name},\n\n"
        f"Has sido invitado a colaborar en "
        f"{invitation.company.name} dentro de PAKIVOX.\n\n"
        f"Actividades: {activity_text}\n\n"
        f"Usa el siguiente enlace para aceptar la invitación "
        f"y crear tu contraseña:\n\n"
        f"{invitation_url}\n\n"
        f"El enlace vence en "
        f"{INVITATION_TOKEN_HOURS} horas.\n\n"
        f"Si no reconoces esta invitación, puedes ignorar "
        f"este mensaje.\n\n"
        f"PAKIVOX\n"
        f"Vende, opera y escala."
    )

    from_email = getattr(
        settings,
        "DEFAULT_FROM_EMAIL",
        "PAKIVOX <no-reply@pakivox.local>",
    )

    return send_mail(
        subject=subject,
        message=message,
        from_email=from_email,
        recipient_list=[
            invitation.email,
        ],
        fail_silently=False,
    )


def get_employee_invitation_for_token(
    *,
    raw_token,
    lock=False,
):
    token_hash = hash_invitation_token(raw_token)

    invitations = EmployeeInvitation.objects.select_related(
        "company",
        "invited_by",
        "accepted_user",
    )

    if lock:
        # PostgreSQL: select_related() includes nullable joins.
        # Lock only the base EmployeeInvitation row; otherwise
        # PostgreSQL rejects FOR UPDATE on the nullable side of
        # the LEFT OUTER JOINs (invited_by / accepted_user).
        invitations = invitations.select_for_update(of=("self",))

    return invitations.filter(
        token_hash=token_hash,
    ).first()


@transaction.atomic
def accept_employee_invitation(
    *,
    raw_token,
    raw_password,
):
    invitation = get_employee_invitation_for_token(
        raw_token=raw_token,
        lock=True,
    )

    if not invitation:
        raise InvalidEmployeeInvitationToken("El enlace de invitación no es válido.")

    if invitation.status == EmployeeInvitation.STATUS_ACCEPTED:
        raise InvalidEmployeeInvitationToken("Esta invitación ya fue utilizada.")

    if invitation.is_expired:
        invitation.status = EmployeeInvitation.STATUS_EXPIRED

        invitation.save(
            update_fields=[
                "status",
                "updated_at",
            ]
        )

        raise ExpiredEmployeeInvitationToken("La invitación ha expirado.")

    if invitation.status != EmployeeInvitation.STATUS_PENDING:
        raise InvalidEmployeeInvitationToken("Esta invitación ya no puede utilizarse.")

    company = Company.objects.select_for_update().get(
        pk=invitation.company_id,
        is_active=True,
    )

    try:
        subscription = (
            Subscription.objects.select_for_update()
            .select_related("plan")
            .get(company=company)
        )
    except Subscription.DoesNotExist as error:
        raise EmployeeInvitationConfigurationError(
            "La empresa no tiene una suscripción configurada."
        ) from error

    email = normalize_email(invitation.email)

    if User.objects.filter(
        username__iexact=email,
    ).exists():
        raise EmployeeInvitationError("Ya existe un usuario con este correo.")

    if User.objects.filter(
        email__iexact=email,
    ).exists():
        raise EmployeeInvitationError("Ya existe un usuario con este correo.")

    active_users = UserProfile.objects.filter(
        company=company,
        is_active=True,
        user__is_active=True,
    ).count()

    user_limit = subscription.plan.max_users

    if active_users >= user_limit:
        raise EmployeeInvitationLimitError(
            f"La empresa ya alcanzó el límite de " f"{user_limit} usuarios de su plan."
        )

    first_name, last_name = split_owner_name(invitation.full_name)

    user = User(
        username=email,
        email=email,
        first_name=first_name,
        last_name=last_name,
        is_active=True,
    )

    user.set_password(raw_password)

    user.full_clean()
    user.save()

    UserProfile.objects.create(
        user=user,
        company=company,
        role=invitation.role,
        is_active=True,
    )

    invitation_jobs = list(
        invitation.job_assignments.select_related("job_template")
    )
    if not invitation_jobs and invitation.job_template_id:
        invitation_jobs = [
            InvitationJobAssignment(
                invitation=invitation,
                job_template=invitation.job_template,
                is_primary=True,
            )
        ]
    EmployeeJobAssignment.objects.bulk_create(
        [
            EmployeeJobAssignment(
                company=company,
                user=user,
                job_template=item.job_template,
                assigned_by=invitation.invited_by,
                is_primary=item.is_primary,
            )
            for item in invitation_jobs
        ]
    )

    EmployeeAreaAssignment.objects.bulk_create(
        [
            EmployeeAreaAssignment(
                company=company,
                user=user,
                area=item.area,
                assigned_by=invitation.invited_by,
            )
            for item in invitation.area_assignments.select_related("area")
        ]
    )
    capability_source = (
        EmployeeCapabilityAssignment.SOURCE_JOB
        if invitation_jobs
        else EmployeeCapabilityAssignment.SOURCE_DIRECT
    )
    EmployeeCapabilityAssignment.objects.bulk_create(
        [
            EmployeeCapabilityAssignment(
                company=company,
                user=user,
                capability=item.capability,
                assigned_by=invitation.invited_by,
                source=capability_source,
            )
            for item in invitation.capability_assignments.select_related("capability")
        ]
    )
    UserWarehouseAccess.objects.bulk_create(
        [
            UserWarehouseAccess(
                company=company,
                user=user,
                warehouse=item.warehouse,
                access_level=item.access_level,
                assigned_by=invitation.invited_by,
            )
            for item in invitation.warehouse_assignments.select_related("warehouse")
        ]
    )

    now = timezone.now()

    invitation.status = EmployeeInvitation.STATUS_ACCEPTED

    invitation.accepted_user = user
    invitation.accepted_at = now

    invitation.save(
        update_fields=[
            "status",
            "accepted_user",
            "accepted_at",
            "updated_at",
        ]
    )

    return {
        "company": company,
        "user": user,
        "invitation": invitation,
        "subscription": subscription,
    }


@transaction.atomic
def revoke_employee_invitation(
    *,
    actor,
    invitation_id,
):
    actor_profile = get_actor_profile(actor)

    invitation = (
        EmployeeInvitation.objects.select_for_update()
        .filter(
            pk=invitation_id,
            company_id=actor_profile.company_id,
        )
        .first()
    )

    if not invitation:
        raise EmployeeInvitationError("La invitación no existe.")

    if invitation.status != EmployeeInvitation.STATUS_PENDING:
        raise EmployeeInvitationError("La invitación ya no está pendiente.")

    now = timezone.now()

    invitation.status = EmployeeInvitation.STATUS_REVOKED

    invitation.revoked_at = now

    invitation.save(
        update_fields=[
            "status",
            "revoked_at",
            "updated_at",
        ]
    )

    return invitation
