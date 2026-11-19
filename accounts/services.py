from datetime import timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import (
    PendingCompanyRegistration,
    UserProfile,
)
from companies.models import Company
from subscriptions.models import Plan, Subscription

User = get_user_model()

TRIAL_PLAN_SLUG = "trial-14-dias"
TRIAL_DURATION_DAYS = 14
VERIFICATION_CODE_MINUTES = 10
VERIFICATION_MAX_ATTEMPTS = 5
VERIFICATION_RESEND_COOLDOWN_SECONDS = 60
VERIFICATION_MAX_EMAILS = 5
PREMIUM_SUBSCRIPTION_FLOW = "premium"


class RegistrationServiceError(Exception):
    """
    Error controlado durante el registro de una empresa.
    """


class InvalidRegistrationToken(RegistrationServiceError):
    """
    El token no existe, ya fue utilizado o no es válido.
    """


class ExpiredRegistrationToken(RegistrationServiceError):
    """
    El token de verificación expiró.
    """


class RegistrationConfigurationError(RegistrationServiceError):
    """
    Falta alguna configuración interna necesaria.
    """


def normalize_email(value):
    return PendingCompanyRegistration.normalize_email(value)


def split_owner_name(owner_name):
    """
    Divide el nombre para los campos first_name y last_name
    del usuario estándar de Django.
    """
    normalized_name = " ".join(str(owner_name or "").strip().split())

    if not normalized_name:
        return "", ""

    parts = normalized_name.split(" ", 1)

    first_name = parts[0][:150]
    last_name = parts[1][:150] if len(parts) > 1 else ""

    return first_name, last_name


def build_unique_company_slug(company_name):
    """
    Obtiene el primer slug disponible.

    La restricción unique=True de Company.slug continúa siendo
    la protección definitiva en base de datos.
    """
    base_slug = slugify(company_name)[:45].strip("-")

    if not base_slug:
        base_slug = "empresa"

    candidate = base_slug
    suffix = 2

    while Company.objects.filter(slug=candidate).exists():
        suffix_text = f"-{suffix}"
        candidate = f"{base_slug[:50 - len(suffix_text)]}" f"{suffix_text}"
        suffix += 1

    return candidate


def create_company_with_unique_slug(
    *,
    company_name,
    company_email,
    max_attempts=100,
):
    """
    Crea la empresa controlando posibles registros simultáneos.

    La transacción interna genera un savepoint. Si otro registro
    ocupa el mismo slug entre la consulta y el INSERT, se intenta
    con el siguiente sufijo sin romper la transacción principal.
    """
    base_slug = slugify(company_name)[:45].strip("-")

    if not base_slug:
        base_slug = "empresa"

    for suffix in range(1, max_attempts + 1):
        if suffix == 1:
            candidate = base_slug
        else:
            suffix_text = f"-{suffix}"
            candidate = f"{base_slug[:50 - len(suffix_text)]}" f"{suffix_text}"

        if Company.objects.filter(slug=candidate).exists():
            continue

        try:
            with transaction.atomic():
                return Company.objects.create(
                    name=company_name,
                    slug=candidate,
                    email=company_email,
                    is_active=True,
                )
        except IntegrityError:
            continue

    raise RegistrationServiceError(
        "No fue posible generar una URL única para la empresa."
    )


@transaction.atomic
def create_pending_registration(
    *,
    owner_name,
    email,
    company_name,
    raw_password,
    registration_ip=None,
    registration_user_agent="",
    accepted_legal_documents=None,
):
    """
    Crea un registro temporal y devuelve:

    - pending_registration
    - raw_code

    El código sin hash se devuelve una sola vez para encolarlo.
    """
    normalized_email = normalize_email(email)

    if User.objects.filter(username__iexact=normalized_email).exists():
        raise RegistrationServiceError("Ya existe una cuenta con este correo.")

    now = timezone.now()

    PendingCompanyRegistration.objects.filter(
        email__iexact=normalized_email,
        status=PendingCompanyRegistration.STATUS_PENDING,
        expires_at__lte=now,
    ).update(
        status=PendingCompanyRegistration.STATUS_EXPIRED,
        updated_at=now,
    )

    active_pending_exists = PendingCompanyRegistration.objects.filter(
        email__iexact=normalized_email,
        status=PendingCompanyRegistration.STATUS_PENDING,
        expires_at__gt=now,
    ).exists()

    if active_pending_exists:
        raise RegistrationServiceError(
            "Este correo ya tiene una verificación pendiente."
        )

    pending_registration = PendingCompanyRegistration(
        owner_name=owner_name,
        email=normalized_email,
        company_name=company_name,
        accepted_terms_at=now,
        registration_ip=registration_ip,
        registration_user_agent=str(registration_user_agent or "")[:500],
        accepted_legal_documents=list(accepted_legal_documents or []),
        verification_email_count=1,
        last_verification_email_at=now,
    )

    pending_registration.set_password(raw_password)

    raw_code = pending_registration.generate_verification_code(
        minutes=VERIFICATION_CODE_MINUTES,
    )

    pending_registration.save()

    from legal.models import LegalAcceptance

    LegalAcceptance.objects.bulk_create(
        [
            LegalAcceptance(
                pending_registration=pending_registration,
                document_slug=item["slug"],
                document_version=item["version"],
                document_sha256=item["sha256"],
                accepted_at=now,
                ip_address=registration_ip,
                user_agent=str(registration_user_agent or "")[:500],
            )
            for item in pending_registration.accepted_legal_documents
        ]
    )

    return pending_registration, raw_code


def enqueue_registration_verification_email(
    *,
    pending_registration,
    raw_code,
    subscription_flow="",
):
    """
    Encola después del COMMIT. El request no hace SMTP.
    El código viaja únicamente al broker y nunca se registra en logs/DB.
    """
    from accounts.tasks import send_registration_verification_email_task

    transaction.on_commit(
        lambda: send_registration_verification_email_task.delay(
            pending_registration.pk,
            raw_code,
            subscription_flow,
        )
    )


def get_pending_registration_for_code(*, pending_registration_id, lock=False):
    queryset = PendingCompanyRegistration.objects
    if lock:
        queryset = queryset.select_for_update()
    return queryset.filter(pk=pending_registration_id).first()


def verify_registration_and_create_tenant(
    *,
    pending_registration_id,
    raw_code,
    subscription_flow="",
):
    """
    Verifica el código y crea atómicamente el tenant.

    Regla clave:
    - Los intentos fallidos / expiración deben COMMIT.
    - Company/User/Warehouse/Subscription deben ser todo-o-nada.
    - La excepción controlada se lanza DESPUÉS de salir de transaction.atomic(),
      para no revertir el contador de seguridad.
    """
    controlled_error = None
    result = None

    with transaction.atomic():
        pending_registration = get_pending_registration_for_code(
            pending_registration_id=pending_registration_id,
            lock=True,
        )

        if not pending_registration:
            controlled_error = InvalidRegistrationToken(
                "El código de verificación no es válido."
            )

        elif pending_registration.status == PendingCompanyRegistration.STATUS_VERIFIED:
            controlled_error = InvalidRegistrationToken(
                "Este código ya fue utilizado."
            )

        elif pending_registration.is_expired:
            pending_registration.mark_expired()
            controlled_error = ExpiredRegistrationToken(
                "El código de verificación ha expirado."
            )

        elif pending_registration.status != PendingCompanyRegistration.STATUS_PENDING:
            controlled_error = InvalidRegistrationToken(
                "Este registro ya no puede verificarse."
            )

        else:
            code = str(raw_code or "").strip()

            if not (len(code) == 6 and code.isdigit()):
                # Un formato inválido también consume intento: evita bypass
                # del límite alternando payloads no numéricos.
                pending_registration.verification_attempt_count += 1
                update_fields = ["verification_attempt_count", "updated_at"]

                if (
                    pending_registration.verification_attempt_count
                    >= VERIFICATION_MAX_ATTEMPTS
                ):
                    pending_registration.status = (
                        PendingCompanyRegistration.STATUS_CANCELLED
                    )
                    update_fields.append("status")

                pending_registration.save(update_fields=update_fields)

                controlled_error = InvalidRegistrationToken(
                    "El código de verificación no es válido."
                )

            elif (
                pending_registration.verification_attempt_count
                >= VERIFICATION_MAX_ATTEMPTS
            ):
                pending_registration.status = (
                    PendingCompanyRegistration.STATUS_CANCELLED
                )
                pending_registration.save(
                    update_fields=["status", "updated_at"]
                )

                controlled_error = InvalidRegistrationToken(
                    "Se alcanzó el máximo de intentos. Inicia un registro nuevo."
                )

            elif not pending_registration.code_matches(code):
                pending_registration.verification_attempt_count += 1
                update_fields = ["verification_attempt_count", "updated_at"]

                if (
                    pending_registration.verification_attempt_count
                    >= VERIFICATION_MAX_ATTEMPTS
                ):
                    pending_registration.status = (
                        PendingCompanyRegistration.STATUS_CANCELLED
                    )
                    update_fields.append("status")

                pending_registration.save(update_fields=update_fields)

                controlled_error = InvalidRegistrationToken(
                    "El código de verificación no es válido."
                )

            else:
                email = normalize_email(pending_registration.email)

                if User.objects.filter(username__iexact=email).exists():
                    raise RegistrationServiceError(
                        "Ya existe una cuenta activa con este correo."
                    )

                is_premium_flow = subscription_flow == PREMIUM_SUBSCRIPTION_FLOW

                if is_premium_flow:
                    stripe_price_id = str(
                        getattr(
                            settings,
                            "STRIPE_WMS360_PRICE_ID",
                            "",
                        )
                        or ""
                    ).strip()

                    if not stripe_price_id:
                        raise RegistrationConfigurationError(
                            "El plan Premium de PAKIVOX no está configurado."
                        )

                    available_plans = Plan.objects.filter(
                        stripe_price_id=stripe_price_id,
                        has_whatsapp=True,
                        is_active=True,
                    )

                    if available_plans.count() != 1:
                        raise RegistrationConfigurationError(
                            "La configuración del plan Premium no es válida."
                        )

                    selected_plan = available_plans.first()
                else:
                    selected_plan = Plan.objects.filter(
                        slug=TRIAL_PLAN_SLUG,
                        is_active=True,
                    ).first()

                    if not selected_plan:
                        raise RegistrationConfigurationError(
                            "El Plan Trial de PAKIVOX no está configurado."
                        )

                company = create_company_with_unique_slug(
                    company_name=pending_registration.company_name,
                    company_email=email,
                )

                first_name, last_name = split_owner_name(
                    pending_registration.owner_name
                )

                user = User(
                    username=email,
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    is_active=True,
                )

                user.password = pending_registration.password_hash
                user.save()

                UserProfile.objects.create(
                    user=user,
                    company=company,
                    role="owner",
                    is_active=True,
                )

                from inventory.models import Warehouse

                Warehouse.objects.create(
                    company=company,
                    name="Bodega Principal",
                    code="PRINCIPAL",
                )

                from legal.models import LegalAcceptance

                LegalAcceptance.objects.filter(
                    pending_registration=pending_registration,
                    user__isnull=True,
                    company__isnull=True,
                ).update(user=user, company=company)

                today = timezone.localdate()

                if is_premium_flow:
                    subscription = Subscription.objects.create(
                        company=company,
                        plan=selected_plan,
                        status=Subscription.STATUS_SUSPENDED,
                        start_date=today,
                        trial_ends_at=None,
                        end_date=None,
                        next_billing_date=None,
                        auto_renew=True,
                        ai_queries_used=0,
                        ai_period_started_at=None,
                        ai_period_ends_at=None,
                    )
                else:
                    trial_ends_at = today + timedelta(
                        days=TRIAL_DURATION_DAYS
                    )

                    subscription = Subscription.objects.create(
                        company=company,
                        plan=selected_plan,
                        status=Subscription.STATUS_TRIAL,
                        start_date=today,
                        trial_ends_at=trial_ends_at,
                        end_date=trial_ends_at,
                        next_billing_date=None,
                        auto_renew=False,
                        ai_queries_used=0,
                        ai_period_started_at=today,
                        ai_period_ends_at=trial_ends_at,
                    )

                pending_registration.mark_verified()

                result = {
                    "company": company,
                    "user": user,
                    "subscription": subscription,
                    "pending_registration": pending_registration,
                }

    # Muy importante: lanzar la excepción fuera del atomic para conservar
    # intentos fallidos y estado EXPIRED/CANCELLED.
    if controlled_error is not None:
        raise controlled_error

    return result

