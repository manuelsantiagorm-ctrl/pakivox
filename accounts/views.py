import logging
from django.db import transaction

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import (
    require_GET,
    require_http_methods,
    require_POST,
)


# accounts/views.py
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from legal.documents import current_acceptance_snapshot
from companies.models import Company  # Ajusta la importación según tu modelo
from subscriptions.models import Subscription  # Ajusta según tu modelo
from datetime import timedelta
from django.utils import timezone

from accounts.forms import (
    AcceptEmployeeInvitationForm,
    CompanyRegistrationForm,
    EmployeeInvitationForm,
)
from accounts.models import (
    EmployeeInvitation,
    PendingCompanyRegistration,
    UserProfile,
    UserWarehouseAccess,
)
from accounts.services import (
    ExpiredRegistrationToken,
    InvalidRegistrationToken,
    RegistrationConfigurationError,
    RegistrationServiceError,
    create_pending_registration,
    get_pending_registration_for_code,
    enqueue_registration_verification_email,
    verify_registration_and_create_tenant,
    PREMIUM_SUBSCRIPTION_FLOW,
)
from accounts.employee_invitation_services import (
    EmployeeInvitationError,
    EmployeeInvitationLimitError,
    EmployeeInvitationPermissionError,
    ExpiredEmployeeInvitationToken,
    InvalidEmployeeInvitationToken,
    accept_employee_invitation,
    create_employee_invitation,
    get_company_user_capacity,
    get_employee_invitation_for_token,
    revoke_employee_invitation,
    send_employee_invitation_email,
)
from django.core.exceptions import ValidationError
from accounts.capability_access import capability_required
from accounts.employee_access_services import (
    update_employee_activities,
    update_employee_jobs,
)
from django.contrib.auth.decorators import login_required
from inventory.models import Warehouse

from accounts.simple_employee_access import (
    ACTIVITIES,
    activity_codes_from_job_codes,
    activity_labels,
    infer_activity_codes_from_profile,
    has_legacy_active_jobs,
    resolve_activity_job_templates,
)

from accounts import login_throttle

logger = logging.getLogger(__name__)


def mask_email(email):
    """
    Enmascara el correo solo para presentación.

    Ejemplos:
    manuel@example.com -> m****l@example.com
    ab@example.com     -> a*@example.com
    a@example.com      -> *@example.com

    No modifica el valor persistido ni se usa para lógica de identidad.
    """
    value = str(email or "").strip()

    if "@" not in value:
        return "••••••"

    local, domain = value.rsplit("@", 1)

    if not domain:
        return "••••••"

    if len(local) <= 1:
        masked_local = "*"
    elif len(local) == 2:
        masked_local = local[0] + "*"
    else:
        masked_local = local[0] + ("*" * min(max(len(local) - 2, 1), 8)) + local[-1]

    return f"{masked_local}@{domain}"


def get_client_ip(request):
    """
    IP real del visitante (ver accounts/client_ip.py).

    Solo confía en X-Forwarded-For cuando la conexión viene del proxy local
    (nginx), y de ese encabezado toma únicamente el valor que agregó nginx.
    """
    from accounts.client_ip import get_client_ip as resolve_client_ip

    return resolve_client_ip(request)


def redirect_authenticated_user(user):
    if user.is_superuser:
        return redirect("owner:console")

    try:
        profile = user.profile
    except UserProfile.DoesNotExist:
        # Cuenta sin empresa (por ejemplo, alta con Google): su siguiente paso
        # es crear la empresa. Antes se mandaba a logout, que solo acepta POST
        # y terminaba en un error 405.
        return redirect("accounts:onboarding_empresa")

    if profile.role in {"owner", "admin"}:
        return redirect("dashboard:ceo_dashboard")

    return redirect("accounts:employee_home")


@require_http_methods(["GET", "POST"])
def client_login(request):
    if request.user.is_authenticated:
        return redirect_authenticated_user(request.user)

    next_url = request.GET.get("next") or request.POST.get("next")

    if request.method == "POST":
        username = request.POST.get("username", "").strip().lower()

        password = request.POST.get("password", "")
        client_ip = get_client_ip(request)

        if login_throttle.esta_bloqueado(username, client_ip):
            messages.error(
                request,
                "Por seguridad pausamos los intentos de acceso a esta cuenta. "
                f"Espera {login_throttle.minutos_de_espera()} minutos o "
                "recupera tu contraseña.",
            )
            return render(
                request,
                "accounts/login.html",
                {
                    "next": next_url,
                    "username": username,
                },
                status=429,
            )

        user = authenticate(
            request,
            username=username,
            password=password,
        )

        if user is None:
            login_throttle.registrar_fallo(username, client_ip)
            messages.error(
                request,
                "Correo o contraseña incorrectos.",
            )

            return render(
                request,
                "accounts/login.html",
                {
                    "next": next_url,
                    "username": username,
                },
            )

        login_throttle.limpiar(username, client_ip)
        login(request, user)

        if next_url and url_has_allowed_host_and_scheme(
            url=next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(next_url)

        return redirect_authenticated_user(user)

    return render(
        request,
        "accounts/login.html",
        {
            "next": next_url,
        },
    )


@require_http_methods(["GET", "POST"])
def company_register(request):
    """
    Registro público del propietario de una empresa.

    En esta etapa no crea Company, User ni Subscription.
    Solamente crea un registro pendiente y envía el correo.
    """
    if request.user.is_authenticated:
        return redirect_authenticated_user(request.user)

    if request.method == "GET":
        if (
            request.GET.get("subscribe")
            == PREMIUM_SUBSCRIPTION_FLOW
        ):
            request.session["registration_subscription_flow"] = (
                PREMIUM_SUBSCRIPTION_FLOW
            )
        else:
            request.session.pop(
                "registration_subscription_flow",
                None,
            )

    subscription_flow = request.session.get(
        "registration_subscription_flow",
        "",
    )

    if subscription_flow != PREMIUM_SUBSCRIPTION_FLOW:
        subscription_flow = ""

    if request.method == "POST":
        form = CompanyRegistrationForm(request.POST)

        if form.is_valid():
            pending_registration = None

            try:
                (
                    pending_registration,
                    raw_code,
                ) = create_pending_registration(
                    owner_name=form.cleaned_data["owner_name"],
                    email=form.cleaned_data["email"],
                    company_name=form.cleaned_data["company_name"],
                    raw_password=form.cleaned_data["password1"],
                    registration_ip=get_client_ip(request),
                    registration_user_agent=request.META.get("HTTP_USER_AGENT", ""),
                    accepted_legal_documents=current_acceptance_snapshot(),
                )

                enqueue_registration_verification_email(
                    pending_registration=pending_registration,
                    raw_code=raw_code,
                    subscription_flow=subscription_flow,
                )

            except RegistrationServiceError as error:
                form.add_error(
                    None,
                    str(error),
                )

            except Exception as error:
                logger.exception(
                    "No se pudo preparar el correo de verificación del registro."
                )
                try:
                    import sentry_sdk
                    sentry_sdk.capture_exception(error)
                except Exception:
                    pass

                if pending_registration:
                    PendingCompanyRegistration.objects.filter(
                        pk=pending_registration.pk,
                        status=(PendingCompanyRegistration.STATUS_PENDING),
                    ).update(
                        status=(PendingCompanyRegistration.STATUS_CANCELLED),
                    )

                form.add_error(
                    None,
                    (
                        "No pudimos enviar el correo de "
                        "verificación. Intenta nuevamente "
                        "en unos minutos."
                    ),
                )

            else:
                request.session["pending_registration_email"] = (
                    pending_registration.email
                )

                request.session["pending_registration_id"] = pending_registration.pk

                return redirect("accounts:registration_email_sent")

    else:
        form = CompanyRegistrationForm()

    from accounts.services import TRIAL_PLAN_SLUG
    from subscriptions.models import Plan

    return render(
        request,
        "accounts/register.html",
        {
            "form": form,
            "subscription_flow": subscription_flow,
            # Limites reales del Trial que se asigna al registrarse, para
            # que la pagina nunca prometa algo distinto a lo que recibe.
            "trial_plan": Plan.objects.filter(slug=TRIAL_PLAN_SLUG).first(),
        },
    )


@require_http_methods(["GET", "POST"])
def registration_email_sent(request):
    """
    Pantalla de código y reenvío controlado.
    El ID sale de sesión; no se acepta company_id/tenant_id del navegador.
    """
    if request.user.is_authenticated:
        return redirect_authenticated_user(request.user)

    pending_id = request.session.get("pending_registration_id")
    pending = PendingCompanyRegistration.objects.filter(
        pk=pending_id,
        status=PendingCompanyRegistration.STATUS_PENDING,
    ).first()

    if not pending:
        return redirect("accounts:company_register")

    if request.method == "POST" and request.POST.get("action") == "resend":
        from datetime import timedelta
        from django.db.models import F
        from accounts.services import (
            VERIFICATION_CODE_MINUTES,
            VERIFICATION_MAX_EMAILS,
            VERIFICATION_RESEND_COOLDOWN_SECONDS,
            enqueue_registration_verification_email,
        )

        try:
            with transaction.atomic():
                locked = PendingCompanyRegistration.objects.select_for_update().get(
                    pk=pending.pk,
                    status=PendingCompanyRegistration.STATUS_PENDING,
                )
                now = timezone.now()
                if locked.verification_email_count >= VERIFICATION_MAX_EMAILS:
                    messages.error(request, "Alcanzaste el máximo de reenvíos para este registro.")
                elif (
                    locked.last_verification_email_at
                    and now - locked.last_verification_email_at
                    < timedelta(seconds=VERIFICATION_RESEND_COOLDOWN_SECONDS)
                ):
                    messages.warning(request, "Espera un minuto antes de solicitar otro código.")
                else:
                    raw_code = locked.generate_verification_code(
                        minutes=VERIFICATION_CODE_MINUTES
                    )
                    locked.verification_email_count = F("verification_email_count") + 1
                    locked.last_verification_email_at = now
                    locked.save(update_fields=[
                        "token_hash", "expires_at", "verification_attempt_count",
                        "status", "verification_email_count",
                        "last_verification_email_at", "updated_at",
                    ])
                    enqueue_registration_verification_email(
                        pending_registration=locked,
                        raw_code=raw_code,
                        subscription_flow=request.session.get(
                            "registration_subscription_flow", ""
                        ),
                    )
                    messages.success(request, "Enviamos un código nuevo.")
        except PendingCompanyRegistration.DoesNotExist:
            messages.error(request, "Este registro ya no está disponible.")

        return redirect("accounts:registration_email_sent")

    return render(
        request,
        "accounts/registration_email_sent.html",
        {"masked_email": mask_email(pending.email), "pending_registration": pending},
    )


@require_http_methods(["POST"])
def verify_company_registration(request):
    """
    Consume un código de un solo uso y crea Company/User/Trial atómicamente.
    Protege doble submit con SELECT FOR UPDATE sobre una sola fila pendiente.
    """
    if request.user.is_authenticated:
        return redirect_authenticated_user(request.user)

    pending_id = request.session.get("pending_registration_id")
    raw_code = str(request.POST.get("code", "")).strip()
    subscription_flow = request.session.get("registration_subscription_flow", "")
    if subscription_flow != PREMIUM_SUBSCRIPTION_FLOW:
        subscription_flow = ""

    try:
        result = verify_registration_and_create_tenant(
            pending_registration_id=pending_id,
            raw_code=raw_code,
            subscription_flow=subscription_flow,
        )
    except ExpiredRegistrationToken as error:
        messages.error(request, str(error))
        return redirect("accounts:registration_email_sent")
    except (InvalidRegistrationToken, RegistrationServiceError, RegistrationConfigurationError) as error:
        logger.warning("No se pudo verificar registro pendiente id=%s: %s", pending_id, error)
        messages.error(request, str(error))
        return redirect("accounts:registration_email_sent")
    except Exception as error:
        logger.exception("Error inesperado al crear tenant verificado.")
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(error)
        except Exception:
            pass
        messages.error(
            request,
            "No pudimos activar la cuenta. Intenta nuevamente en unos minutos.",
        )
        return redirect("accounts:registration_email_sent")

    user = result["user"]
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")

    request.session.pop("pending_registration_email", None)
    request.session.pop("pending_registration_id", None)
    request.session.pop("registration_subscription_flow", None)

    if subscription_flow == PREMIUM_SUBSCRIPTION_FLOW:
        messages.success(
            request,
            "Tu correo y empresa quedaron verificados. Completa el pago para activar PAKIVOX Premium.",
        )
        return redirect("subscriptions:checkout")

    messages.success(request, "Correo verificado. Tu prueba gratuita de PAKIVOX ya está activa.")
    return redirect_authenticated_user(user)


@require_POST
def client_logout(request):
    logout(request)
    return redirect("landing")


@login_required(login_url="/accounts/login/")
@capability_required("team.manage")
@require_http_methods(["GET", "POST"])
def employee_invitation_create(request):
    """
    Permite al propietario o administrador invitar empleados.

    La invitación pendiente reserva un espacio dentro del límite
    de usuarios contratado por la empresa.
    """
    profile = getattr(
        request.user,
        "profile",
        None,
    )

    if not profile or not profile.is_active:
        return render(
            request,
            "accounts/employee_invitation_error.html",
            {
                "error_message": (
                    "Tu usuario no tiene un perfil " "de empresa activo."
                ),
            },
            status=403,
        )

    if profile.role not in {
        "owner",
        "admin",
    }:
        return render(
            request,
            "accounts/employee_invitation_error.html",
            {
                "error_message": (
                    "Solo el propietario o un administrador " "puede invitar empleados."
                ),
            },
            status=403,
        )

    company = profile.company
    available_warehouses = list(
        Warehouse.objects.filter(
            company=company,
            is_active=True,
        ).order_by("name")
    )

    capacity = get_company_user_capacity(company)

    pending_invitations = list(
        EmployeeInvitation.objects.filter(
            company=company,
            status=EmployeeInvitation.STATUS_PENDING,
            expires_at__gt=timezone.now(),
        )
        .select_related(
            "invited_by",
            "job_template",
        )
        .prefetch_related(
            "job_assignments__job_template",
            "warehouse_assignments__warehouse",
        )
        .order_by(
            "-created_at",
        )
    )

    employees = list(
        UserProfile.objects.filter(company=company)
        .select_related("user")
        .prefetch_related(
            "user__warehouse_accesses__warehouse",
            "user__capability_assignments__capability",
            "user__job_assignments__job_template",
        )
        .order_by("-is_active", "user__first_name", "user__email")
    )

    authority_labels = {
        UserWarehouseAccess.LEVEL_VIEW: "Consultar",
        UserWarehouseAccess.LEVEL_OPERATE: "Trabajar",
        UserWarehouseAccess.LEVEL_APPROVE: "Supervisar",
        UserWarehouseAccess.LEVEL_MANAGE: "Administrar",
    }

    for employee_profile in employees:
        simple_codes = infer_activity_codes_from_profile(employee_profile)
        employee_profile.simple_activity_codes = simple_codes
        employee_profile.simple_activity_labels = activity_labels(simple_codes)
        employee_profile.has_legacy_jobs = has_legacy_active_jobs(employee_profile)

        active_warehouse_accesses = [
            access
            for access in employee_profile.user.warehouse_accesses.all()
            if (
                access.company_id == company.id
                and access.is_active
                and access.warehouse.is_active
            )
        ]
        employee_profile.simple_warehouse_ids = {
            access.warehouse_id
            for access in active_warehouse_accesses
        }
        employee_profile.simple_warehouse_levels_by_id = {
            access.warehouse_id: access.access_level
            for access in active_warehouse_accesses
        }
        employee_profile.simple_warehouse_labels = tuple(
            access.warehouse.name
            for access in sorted(
                active_warehouse_accesses,
                key=lambda item: item.warehouse.name.casefold(),
            )
        )

        current_levels = {
            access.access_level
            for access in active_warehouse_accesses
        }
        employee_profile.has_mixed_warehouse_levels = len(current_levels) > 1

        if employee_profile.role == "owner":
            employee_profile.simple_activity_labels = ("Dirección general",)
            employee_profile.simple_warehouse_labels = ("Todas las bodegas",)
            employee_profile.simple_authority_label = "Control total"
        elif len(current_levels) == 1:
            employee_profile.simple_warehouse_access_level = next(
                iter(current_levels)
            )
            employee_profile.simple_authority_label = authority_labels.get(
                employee_profile.simple_warehouse_access_level,
                "Acceso configurado",
            )
        elif current_levels:
            employee_profile.simple_warehouse_access_level = "preserve"
            employee_profile.simple_authority_label = "Niveles distintos"
        else:
            employee_profile.simple_warehouse_access_level = (
                UserWarehouseAccess.LEVEL_OPERATE
            )
            employee_profile.simple_authority_label = "Sin bodega asignada"

    for invitation in pending_invitations:
        invitation_job_codes = [
            assignment.job_template.code
            for assignment in invitation.job_assignments.all()
        ]
        if not invitation_job_codes and invitation.job_template_id:
            invitation_job_codes = [invitation.job_template.code]
        simple_codes = activity_codes_from_job_codes(invitation_job_codes)
        invitation.simple_activity_labels = activity_labels(simple_codes)

        invitation_warehouse_assignments = [
            assignment
            for assignment in invitation.warehouse_assignments.all()
            if (
                assignment.warehouse.company_id == company.id
                and assignment.warehouse.is_active
            )
        ]
        invitation.simple_warehouse_labels = tuple(
            assignment.warehouse.name
            for assignment in sorted(
                invitation_warehouse_assignments,
                key=lambda item: item.warehouse.name.casefold(),
            )
        )
        invitation_levels = {
            assignment.access_level
            for assignment in invitation_warehouse_assignments
        }
        if len(invitation_levels) == 1:
            invitation.simple_authority_label = authority_labels.get(
                next(iter(invitation_levels)),
                "Acceso configurado",
            )
        elif invitation_levels:
            invitation.simple_authority_label = "Niveles distintos"
        else:
            invitation.simple_authority_label = "Sin bodega asignada"

    if request.method == "POST":
        form = EmployeeInvitationForm(request.POST, company=company)

        if form.is_valid():
            invitation = None

            try:
                selected_jobs = resolve_activity_job_templates(
                    company,
                    form.cleaned_data["activities"],
                    ensure=True,
                )
                primary_job = selected_jobs[0]

                invitation, raw_token = create_employee_invitation(
                    actor=request.user,
                    full_name=(form.cleaned_data["full_name"]),
                    email=(form.cleaned_data["email"]),
                    role=primary_job.role,
                    job_template=primary_job,
                    job_templates=selected_jobs,
                    primary_job_template=primary_job,
                    warehouse_accesses=[
                        (
                            warehouse,
                            form.cleaned_data["warehouse_levels"][warehouse.pk],
                        )
                        for warehouse in form.cleaned_data["warehouses"]
                    ],
                )

                send_employee_invitation_email(
                    request=request,
                    invitation=invitation,
                    raw_token=raw_token,
                )

            except (
                EmployeeInvitationLimitError,
                EmployeeInvitationPermissionError,
                EmployeeInvitationError,
            ) as error:
                form.add_error(
                    None,
                    str(error),
                )

            except Exception as error:
                logger.exception("No fue posible enviar la " "invitación del empleado.")
                try:
                    import sentry_sdk
                    sentry_sdk.capture_exception(error)
                except Exception:
                    pass

                if invitation:
                    EmployeeInvitation.objects.filter(
                        pk=invitation.pk,
                        company=company,
                        status=(EmployeeInvitation.STATUS_PENDING),
                    ).update(
                        status=(EmployeeInvitation.STATUS_REVOKED),
                        revoked_at=timezone.now(),
                        updated_at=timezone.now(),
                    )

                form.add_error(
                    None,
                    (
                        "No pudimos enviar la invitación. "
                        "Intenta nuevamente en unos minutos."
                    ),
                )

            else:
                messages.success(
                    request,
                    (
                        f"Enviamos la invitación a "
                        f"{invitation.email}. "
                        "El espacio quedó reservado."
                    ),
                )

                return redirect("accounts:employee_invitation_create")

    else:
        form = EmployeeInvitationForm(company=company)

    capacity = get_company_user_capacity(company)
    selected_invite_activities = set(form["activities"].value() or ())

    return render(
        request,
        "accounts/employee_invitation_form.html",
        {
            "company": company,
            "profile": profile,
            "form": form,
            "capacity": capacity,
            "pending_invitations": (pending_invitations),
            "employees": employees,
            "activity_catalog": ACTIVITIES,
            "selected_invite_activities": selected_invite_activities,
            "available_warehouses": available_warehouses,
            "warehouse_level_choices": EmployeeInvitationForm.WAREHOUSE_ACCESS_LEVEL_CHOICES,
        },
    )


@login_required(login_url="/accounts/login/")
@capability_required("team.manage")
@require_POST
def employee_invitation_revoke(
    request,
    invitation_id,
):
    try:
        invitation = revoke_employee_invitation(
            actor=request.user,
            invitation_id=invitation_id,
        )

    except EmployeeInvitationError as error:
        messages.error(
            request,
            str(error),
        )

    else:
        messages.success(
            request,
            (
                f"La invitación de {invitation.email} "
                "fue revocada y el espacio quedó disponible."
            ),
        )

    return redirect("accounts:employee_invitation_create")


@login_required(login_url="/accounts/login/")
@capability_required("team.manage")
@require_POST
def employee_activities_update(request, employee_id):
    update_warehouses = "warehouses" in request.POST

    try:
        warehouse_levels = None

        if update_warehouses:
            raw_warehouse_ids = request.POST.getlist("warehouses")
            try:
                warehouse_id_list = [int(value) for value in raw_warehouse_ids]
            except (TypeError, ValueError):
                raise ValidationError("Seleccionaste una bodega inválida.")

            warehouse_levels = {
                warehouse_id: request.POST.get(
                    f"warehouse_level_{warehouse_id}",
                    "",
                )
                for warehouse_id in warehouse_id_list
            }

        employee = update_employee_activities(
            actor=request.user,
            employee_id=employee_id,
            activity_codes=request.POST.getlist("activities"),
            warehouse_levels=warehouse_levels,
        )
    except (TypeError, ValueError, ValidationError) as error:
        message = (
            getattr(error, "message", None)
            or "; ".join(getattr(error, "messages", []))
            or str(error)
        )
        messages.error(request, message)
    else:
        messages.success(
            request,
            (
                f"Actualizamos el acceso de "
                f"{employee.get_full_name() or employee.email}."
                if update_warehouses
                else (
                    f"Actualizamos las actividades de "
                    f"{employee.get_full_name() or employee.email}."
                )
            ),
        )
    return redirect("accounts:employee_invitation_create")


@login_required(login_url="/accounts/login/")
@capability_required("team.manage")
@require_POST
def employee_jobs_update(request, employee_id):
    raw_job_ids = request.POST.getlist("job_templates")
    try:
        job_ids = [int(value) for value in raw_job_ids]
        primary_job_id = int(request.POST.get("primary_job_template", ""))
        employee = update_employee_jobs(
            actor=request.user,
            employee_id=employee_id,
            job_ids=job_ids,
            primary_job_id=primary_job_id,
        )
    except (TypeError, ValueError, ValidationError) as error:
        message = getattr(error, "message", None) or "; ".join(
            getattr(error, "messages", [])
        ) or str(error)
        messages.error(request, message)
    else:
        messages.success(
            request,
            f"Actualizamos los puestos de {employee.get_full_name() or employee.email}.",
        )
    return redirect("accounts:employee_invitation_create")


@require_http_methods(["GET", "POST"])
def accept_employee_invitation_view(
    request,
    token,
):
    """
    Permite al empleado aceptar la invitación y establecer
    su contraseña.

    La capacidad se valida nuevamente dentro de la transacción.
    """
    token = str(token or "").strip()

    if not token or len(token) > 200:
        return render(
            request,
            "accounts/employee_invitation_accept.html",
            {
                "invitation_status": "invalid",
                "error_message": ("El enlace de invitación no es válido."),
            },
            status=400,
        )

    invitation = get_employee_invitation_for_token(
        raw_token=token,
        lock=False,
    )

    if not invitation:
        return render(
            request,
            "accounts/employee_invitation_accept.html",
            {
                "invitation_status": "invalid",
                "error_message": (
                    "La invitación no existe o ya no " "se encuentra disponible."
                ),
            },
            status=400,
        )

    if request.user.is_authenticated:
        return render(
            request,
            "accounts/employee_invitation_accept.html",
            {
                "invitation_status": "authenticated",
                "error_message": (
                    "Ya existe una sesión iniciada. "
                    "Cierra sesión antes de aceptar "
                    "esta invitación."
                ),
                "invitation": invitation,
            },
            status=409,
        )

    if invitation.is_expired:
        EmployeeInvitation.objects.filter(
            pk=invitation.pk,
            status=EmployeeInvitation.STATUS_PENDING,
        ).update(
            status=EmployeeInvitation.STATUS_EXPIRED,
            updated_at=timezone.now(),
        )

        return render(
            request,
            "accounts/employee_invitation_accept.html",
            {
                "invitation_status": "expired",
                "error_message": (
                    "La invitación ha expirado. Solicita "
                    "una nueva invitación al administrador."
                ),
                "invitation": invitation,
            },
            status=410,
        )

    if invitation.status == EmployeeInvitation.STATUS_ACCEPTED:
        return render(
            request,
            "accounts/employee_invitation_accept.html",
            {
                "invitation_status": "used",
                "error_message": (
                    "Esta invitación ya fue utilizada. " "Puedes iniciar sesión."
                ),
                "invitation": invitation,
            },
        )

    if invitation.status != EmployeeInvitation.STATUS_PENDING:
        return render(
            request,
            "accounts/employee_invitation_accept.html",
            {
                "invitation_status": "invalid",
                "error_message": ("Esta invitación ya no puede utilizarse."),
                "invitation": invitation,
            },
            status=400,
        )

    if request.method == "POST":
        form = AcceptEmployeeInvitationForm(
            request.POST,
            invitation=invitation,
        )

        if form.is_valid():
            try:
                result = accept_employee_invitation(
                    raw_token=token,
                    raw_password=(form.cleaned_data["password1"]),
                )

            except ExpiredEmployeeInvitationToken as error:
                return render(
                    request,
                    "accounts/employee_invitation_accept.html",
                    {
                        "invitation_status": "expired",
                        "error_message": str(error),
                        "invitation": invitation,
                    },
                    status=410,
                )

            except (
                InvalidEmployeeInvitationToken,
                EmployeeInvitationLimitError,
                EmployeeInvitationError,
            ) as error:
                form.add_error(
                    None,
                    str(error),
                )

            else:
                authenticated_user = authenticate(
                    request,
                    username=result["user"].username,
                    password=(form.cleaned_data["password1"]),
                )

                if authenticated_user:
                    login(
                        request,
                        authenticated_user,
                    )

                    messages.success(
                        request,
                        (
                            f"Bienvenido a "
                            f"{result['company'].name}. "
                            "Tu cuenta fue activada."
                        ),
                    )

                    return redirect_authenticated_user(
                        authenticated_user
                    )

                messages.success(
                    request,
                    (
                        "Tu cuenta fue creada correctamente. "
                        "Ahora puedes iniciar sesión."
                    ),
                )

                return redirect("accounts:login")

    else:
        form = AcceptEmployeeInvitationForm(
            invitation=invitation,
        )

    return render(
        request,
        "accounts/employee_invitation_accept.html",
        {
            "invitation_status": "ready",
            "invitation": invitation,
            "form": form,
            "token": token,
        },
    )


import uuid
from datetime import timedelta
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils.text import slugify
from django.utils import timezone

from companies.models import Company
from subscriptions.models import Subscription, Plan


@login_required
def onboarding_empresa_view(request):
    user = request.user

    from accounts.models import UserProfile

    # UserProfile.company es obligatorio: un usuario sin empresa no tiene
    # perfil. Por eso aqui solo se CONSULTA; el perfil se crea junto con la
    # empresa, dentro de la transaccion de mas abajo.
    profile = UserProfile.objects.filter(user=user).first()

    # Si ya tiene empresa, entra primero al onboarding visual.
    if profile and profile.company_id:
        return redirect("companies:onboarding_wms360")

    if request.method == "POST":
        company_name = request.POST.get("company_name")

        if company_name:
            from django.db import transaction

            from accounts.services import TRIAL_PLAN_SLUG

            # Un solo Trial oficial: el mismo que usa el registro normal.
            plan = Plan.objects.filter(
                slug=TRIAL_PLAN_SLUG,
                is_active=True,
            ).first()

            if not plan:
                import sentry_sdk

                sentry_sdk.capture_message(
                    "Onboarding de empresa: el Plan Trial oficial no esta configurado.",
                    level="error",
                )
                messages.error(
                    request,
                    "No pudimos preparar tu periodo de prueba. "
                    "Intenta de nuevo en unos minutos.",
                )
                return render(
                    request,
                    "accounts/onboarding.html",
                )

            base_slug = slugify(company_name) or "empresa"

            slug = base_slug
            count = 1

            while Company.objects.filter(slug=slug).exists():
                slug = f"{base_slug}-{count}"
                count += 1

            today = timezone.localdate()
            trial_end = today + timedelta(days=14)

            # Todo o nada: empresa, perfil de dueno y suscripcion Trial.
            with transaction.atomic():
                company = Company.objects.create(
                    name=company_name,
                    slug=slug,
                    is_active=True,
                )

                UserProfile.objects.create(
                    user=user,
                    company=company,
                    role="owner",
                    is_active=True,
                )

                Subscription.objects.update_or_create(
                    company=company,
                    defaults={
                        "plan": plan,
                        "status": Subscription.STATUS_TRIAL,
                        "start_date": today,
                        "end_date": trial_end,
                        "trial_ends_at": trial_end,
                        "next_billing_date": trial_end,
                        "auto_renew": False,
                    },
                )

            messages.success(
                request,
                (
                    f"¡Bienvenido a WMS360! Tu empresa "
                    f"'{company.name}' se ha creado correctamente. "
                    "Cuentas con 14 días de prueba."
                ),
            )

            request.user.refresh_from_db()

            # Bienvenida visual antes de registrar la primera bodega.
            return redirect("companies:onboarding_wms360")

    return render(
        request,
        "accounts/onboarding.html",
    )



def accept_saas_client_invitation_view(request, token):
    """
    Vista de aceptación de invitación de cliente SaaS (BUG-102).

    El cliente define su propia contraseña aquí. El Owner nunca la
    conoce ni la escribe.
    """
    from accounts.forms import AcceptSaaSClientInvitationForm
    from accounts.saas_invitation_services import (
        ExpiredInvitationToken,
        InvalidInvitationToken,
        SaaSClientInvitationError,
        accept_saas_client_invitation,
        get_invitation_for_token,
    )

    token = str(token or "").strip()

    if not token or len(token) > 200:
        return render(
            request,
            "accounts/saas_client_invitation_accept.html",
            {
                "invitation_status": "invalid",
                "error_message": "El enlace de invitación no es válido.",
            },
            status=400,
        )

    invitation = get_invitation_for_token(token)

    if not invitation or not invitation.token_matches(token):
        return render(
            request,
            "accounts/saas_client_invitation_accept.html",
            {
                "invitation_status": "invalid",
                "error_message": (
                    "La invitación no existe o ya no se encuentra disponible."
                ),
            },
            status=400,
        )

    if request.user.is_authenticated:
        return render(
            request,
            "accounts/saas_client_invitation_accept.html",
            {
                "invitation_status": "authenticated",
                "error_message": (
                    "Ya existe una sesión iniciada. Cierra sesión antes "
                    "de aceptar esta invitación."
                ),
                "invitation": invitation,
            },
            status=409,
        )

    if invitation.is_expired:
        invitation.mark_expired()
        return render(
            request,
            "accounts/saas_client_invitation_accept.html",
            {
                "invitation_status": "expired",
                "error_message": (
                    "La invitación ha expirado. Solicita una nueva "
                    "invitación al equipo de WMS360."
                ),
                "invitation": invitation,
            },
            status=410,
        )

    if invitation.status == invitation.STATUS_ACCEPTED:
        return render(
            request,
            "accounts/saas_client_invitation_accept.html",
            {
                "invitation_status": "used",
                "error_message": "Esta invitación ya fue utilizada. Puedes iniciar sesión.",
                "invitation": invitation,
            },
        )

    if invitation.status != invitation.STATUS_PENDING:
        return render(
            request,
            "accounts/saas_client_invitation_accept.html",
            {
                "invitation_status": "invalid",
                "error_message": "Esta invitación ya no puede utilizarse.",
                "invitation": invitation,
            },
            status=400,
        )

    if request.method == "POST":
        form = AcceptSaaSClientInvitationForm(request.POST, invitation=invitation)

        if form.is_valid():
            try:
                result = accept_saas_client_invitation(
                    raw_token=token,
                    raw_password=form.cleaned_data["password1"],
                )

                user = authenticate(
                    request,
                    username=result["user"].username,
                    password=form.cleaned_data["password1"],
                )

                if user is not None:
                    login(request, user)

                messages.success(
                    request,
                    f"¡Bienvenido a WMS360, {result['company'].name}! "
                    "Tu cuenta ya está activa.",
                )
                return redirect("/")

            except ExpiredInvitationToken:
                return render(
                    request,
                    "accounts/saas_client_invitation_accept.html",
                    {
                        "invitation_status": "expired",
                        "error_message": "La invitación ha expirado.",
                        "invitation": invitation,
                    },
                    status=410,
                )

            except (InvalidInvitationToken, SaaSClientInvitationError) as error:
                return render(
                    request,
                    "accounts/saas_client_invitation_accept.html",
                    {
                        "invitation_status": "invalid",
                        "error_message": str(error),
                        "invitation": invitation,
                    },
                    status=400,
                )
    else:
        form = AcceptSaaSClientInvitationForm(invitation=invitation)

    return render(
        request,
        "accounts/saas_client_invitation_accept.html",
        {
            "invitation_status": "pending",
            "invitation": invitation,
            "form": form,
        },
    )
