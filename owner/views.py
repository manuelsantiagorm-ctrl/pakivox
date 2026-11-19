import sentry_sdk
from datetime import timedelta
from decimal import Decimal
import uuid
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import user_passes_test
from django.db import transaction
from django.db.models import F, Sum
from django.shortcuts import redirect, render
from django.utils import timezone
from django.db import IntegrityError

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import UserProfile
from companies.models import Company
from subscriptions.models import Plan, Subscription

from messaging.models import WhatsAppNumber, Message, WhatsAppCreditMovement

from .control_center_services import collect_owner_control_center_snapshot

from .forms import (
    SaaSClientQuickCreateForm,
    OwnerWhatsAppNumberForm,
    WhatsAppCreditInjectionForm,
)


def owner_required(user):
    return user.is_authenticated and user.is_superuser


def model_has_field(model, field_name):
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False


def get_date_value_for_field(model, field_name, days=0):
    field = model._meta.get_field(field_name)
    now = timezone.now()
    target = now + timedelta(days=days)

    if field.get_internal_type() == "DateField":
        return target.date()

    return target


def get_plan_monthly_price(plan):
    if not plan:
        return Decimal("0.00")

    possible_fields = [
        "monthly_price",
        "price",
        "price_mxn",
        "amount",
        "monthly_fee",
    ]

    for field_name in possible_fields:
        value = getattr(plan, field_name, None)

        if value is not None:
            try:
                return Decimal(str(value))
            except Exception:
                pass

    return Decimal("0.00")


def subscription_is_active(subscription):
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

    if status in ["active", "paid", "trialing", "valid"]:
        return True

    if model_has_field(subscription.__class__, "is_active"):
        return bool(getattr(subscription, "is_active", False))

    return False


def get_subscription_due_date(subscription):
    if not subscription:
        return None

    possible_fields = [
        "expires_at",
        "ends_at",
        "end_date",
        "current_period_end",
        "next_billing_date",
    ]

    for field_name in possible_fields:
        if model_has_field(subscription.__class__, field_name):
            value = getattr(subscription, field_name, None)
            if value:
                return value

    return None


def create_subscription_for_company(company, plan, days=30):
    subscription = Subscription(
        company=company,
        plan=plan,
    )

    if model_has_field(Subscription, "status"):
        subscription.status = "active"

    if model_has_field(Subscription, "is_active"):
        subscription.is_active = True

    start_fields = [
        "start_date",
        "starts_at",
        "started_at",
        "current_period_start",
    ]

    end_fields = [
        "end_date",
        "ends_at",
        "expires_at",
        "current_period_end",
        "next_billing_date",
    ]

    for field_name in start_fields:
        if model_has_field(Subscription, field_name):
            setattr(
                subscription,
                field_name,
                get_date_value_for_field(Subscription, field_name, days=0),
            )

    for field_name in end_fields:
        if model_has_field(Subscription, field_name):
            setattr(
                subscription,
                field_name,
                get_date_value_for_field(Subscription, field_name, days=days),
            )

    subscription.save()
    return subscription


def log_owner_action(
    request,
    *,
    action,
    company=None,
    target_repr="",
    before_state=None,
    after_state=None,
    detail=None,
):
    """
    BUG-099: registra una acción sensible del Owner. Nunca debe
    bloquear el flujo principal si falla el registro en sí.
    """
    from accounts.models import OwnerActionAudit

    try:
        OwnerActionAudit.objects.create(
            actor=request.user,
            action=action,
            company=company,
            target_repr=target_repr[:255],
            before_state=before_state or {},
            after_state=after_state or {},
            request_path=request.path[:500],
            request_method=request.method,
            ip_address=request.META.get("REMOTE_ADDR"),
            detail=detail or {},
        )
    except Exception as error:
        sentry_sdk.capture_exception(error)


@user_passes_test(owner_required)
def owner_saas_console(request):
    client_form = SaaSClientQuickCreateForm(prefix="client")
    channel_form = OwnerWhatsAppNumberForm(prefix="channel")
    credit_form = WhatsAppCreditInjectionForm(prefix="credit")
    credit_form.initial["idempotency_key"] = str(uuid.uuid4())

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create_client":
            client_form = SaaSClientQuickCreateForm(
                request.POST,
                prefix="client",
            )

            if client_form.is_valid():
                from accounts.saas_invitation_services import (
                    SaaSClientInvitationError,
                    create_saas_client_invitation,
                    send_saas_client_invitation_email,
                )

                try:
                    invitation, raw_token = create_saas_client_invitation(
                        invited_by=request.user,
                        company_name=client_form.cleaned_data["company_name"],
                        admin_email=client_form.cleaned_data["admin_email"],
                        admin_name=client_form.cleaned_data["admin_name"],
                        plan=client_form.cleaned_data["plan"],
                        subscription_days=client_form.cleaned_data["subscription_days"],
                        whatsapp_data={
                            "phone_number_id": client_form.cleaned_data[
                                "phone_number_id"
                            ],
                            "display_number": client_form.cleaned_data[
                                "display_number"
                            ],
                            "channel_name": client_form.cleaned_data["channel_name"],
                            "twilio_account_sid": client_form.cleaned_data[
                                "twilio_account_sid"
                            ],
                            "twilio_auth_token": client_form.cleaned_data[
                                "twilio_auth_token"
                            ],
                            "twilio_whatsapp_from": client_form.cleaned_data[
                                "twilio_whatsapp_from"
                            ],
                            "twilio_enabled": client_form.cleaned_data[
                                "twilio_enabled"
                            ],
                            "initial_credits": client_form.cleaned_data[
                                "initial_credits"
                            ],
                            "precio_por_mensaje": client_form.cleaned_data[
                                "precio_por_mensaje"
                            ],
                        },
                    )
                    send_saas_client_invitation_email(
                        request=request,
                        invitation=invitation,
                        raw_token=raw_token,
                    )

                    log_owner_action(
                        request,
                        action="create_client_invitation",
                        target_repr=invitation.company_name,
                        after_state={
                            "company_name": invitation.company_name,
                            "admin_email": invitation.admin_email,
                            "plan": invitation.plan.slug,
                            "subscription_days": invitation.subscription_days,
                        },
                    )

                    messages.success(
                        request,
                        "Invitación enviada. El cliente definirá su propia "
                        "contraseña al aceptar el enlace por correo.",
                    )
                    return redirect("owner:console")

                except SaaSClientInvitationError as e:
                    messages.error(request, str(e))
                    return redirect("owner:console")

                except IntegrityError:
                    messages.error(
                        request,
                        "Ya existe una invitación o cuenta con ese correo, "
                        "o la solicitud se procesó dos veces.",
                    )
                    return redirect("owner:console")

            messages.error(request, "Revisa los datos del nuevo cliente.")

        elif action == "create_channel":
            channel_form = OwnerWhatsAppNumberForm(
                request.POST,
                prefix="channel",
            )

            if channel_form.is_valid():
                channel = channel_form.save()

                log_owner_action(
                    request,
                    action="create_channel",
                    company=channel.company,
                    target_repr=channel.channel_name,
                    after_state={
                        "channel_name": channel.channel_name,
                        "display_number": channel.display_number,
                        "provider": channel.provider,
                    },
                )

                messages.success(request, "Canal WhatsApp creado correctamente.")
                return redirect("owner:console")

            messages.error(request, "Revisa los datos del canal WhatsApp.")

        elif action == "inject_credits":
            credit_form = WhatsAppCreditInjectionForm(
                request.POST,
                prefix="credit",
            )

            if credit_form.is_valid():
                whatsapp_number = credit_form.cleaned_data["whatsapp_number"]
                credits = credit_form.cleaned_data["credits"]
                amount_mxn = credit_form.cleaned_data.get("amount_mxn") or Decimal(
                    "0.00"
                )
                note = credit_form.cleaned_data.get("note") or ""
                idempotency_key = credit_form.cleaned_data.get("idempotency_key") or ""

                with transaction.atomic():
                    locked_number = WhatsAppNumber.objects.select_for_update().get(
                        pk=whatsapp_number.pk
                    )

                    already_processed = False
                    if idempotency_key:
                        already_processed = WhatsAppCreditMovement.objects.filter(
                            company=locked_number.company,
                            idempotency_key=idempotency_key,
                        ).exists()

                    if already_processed:
                        messages.info(
                            request,
                            "Esta recarga ya se había procesado antes; "
                            "no se volvió a aplicar para evitar un doble cargo.",
                        )
                    else:
                        WhatsAppNumber.objects.filter(pk=locked_number.pk).update(
                            creditos_disponibles=F("creditos_disponibles") + credits
                        )

                        locked_number.refresh_from_db(fields=["creditos_disponibles"])

                        WhatsAppCreditMovement.objects.create(
                            company=locked_number.company,
                            whatsapp_number=locked_number,
                            movement_type=WhatsAppCreditMovement.MOVEMENT_RECHARGE,
                            credits=credits,
                            amount_mxn=amount_mxn,
                            balance_after=locked_number.creditos_disponibles,
                            note=note,
                            created_by=request.user,
                            idempotency_key=idempotency_key or None,
                        )
                        messages.success(
                            request,
                            f"Se cargaron {credits} créditos a "
                            f"{locked_number.channel_name}.",
                        )

                        log_owner_action(
                            request,
                            action="inject_credits",
                            company=locked_number.company,
                            target_repr=locked_number.channel_name,
                            after_state={
                                "credits_added": credits,
                                "amount_mxn": str(amount_mxn),
                                "new_balance": locked_number.creditos_disponibles,
                            },
                        )

                return redirect("owner:console")

            messages.error(request, "Revisa los datos de la recarga.")

    subscriptions = list(Subscription.objects.select_related("company", "plan").all())

    active_subscriptions = [
        subscription
        for subscription in subscriptions
        if subscription_is_active(subscription)
    ]
    from owner.control_center_services import collect_financial_snapshot

    mrr = collect_financial_snapshot()["mrr_contractual"]

    month_start = timezone.now().replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    message_date_field = None

    if model_has_field(Message, "created_at"):
        message_date_field = "created_at"
    elif model_has_field(Message, "provider_timestamp"):
        message_date_field = "provider_timestamp"

    outbound_messages = Message.objects.filter(
        direction=Message.DIRECTION_OUTBOUND,
    ).exclude(status=getattr(Message, "STATUS_FAILED", "failed"))

    if message_date_field:
        outbound_messages = outbound_messages.filter(
            **{f"{message_date_field}__gte": month_start}
        )

    whatsapp_consumption = outbound_messages.count()

    total_credits = (
        WhatsAppNumber.objects.aggregate(total=Sum("creditos_disponibles"))["total"]
        or 0
    )

    # Portafolio Owner sin consultas por empresa dentro del loop.
    # Conserva exactamente el contrato de client_rows usado por el template.
    companies = list(Company.objects.all().order_by("name"))
    subscriptions_by_company = {
        subscription.company_id: subscription for subscription in subscriptions
    }
    whatsapp_by_company = {
        row["company_id"]: {
            "channels_count": row["channels_count"],
            "credits": row["credits"] or 0,
        }
        for row in WhatsAppNumber.objects.values("company_id").annotate(
            channels_count=Count("id"),
            credits=Sum("creditos_disponibles"),
        )
    }

    client_rows = []

    for company in companies:
        subscription = subscriptions_by_company.get(company.id)
        is_active = subscription_is_active(subscription)
        due_date = get_subscription_due_date(subscription)
        whatsapp_totals = whatsapp_by_company.get(
            company.id,
            {"channels_count": 0, "credits": 0},
        )

        client_rows.append(
            {
                "company": company,
                "subscription": subscription,
                "is_active": is_active,
                "due_date": due_date,
                "channels_count": whatsapp_totals["channels_count"],
                "credits": whatsapp_totals["credits"],
            }
        )

    active_clients_count = sum(1 for row in client_rows if row["is_active"])
    suspended_clients_count = len(client_rows) - active_clients_count

    recent_movements = WhatsAppCreditMovement.objects.select_related(
        "company",
        "whatsapp_number",
        "created_by",
    ).order_by("-created_at")[:10]

    context = {
        "client_form": client_form,
        "channel_form": channel_form,
        "credit_form": credit_form,
        "mrr": mrr,
        "whatsapp_consumption": whatsapp_consumption,
        "total_credits": total_credits,
        "active_clients_count": active_clients_count,
        "suspended_clients_count": suspended_clients_count,
        "client_rows": client_rows,
        "recent_movements": recent_movements,
        "founder": collect_owner_control_center_snapshot(),
    }

    return render(request, "owner/owner_saas_console.html", context)


@user_passes_test(owner_required)
def whatsapp_requests_list(request):
    status_filter = request.GET.get("status", "").strip()
    company_filter = request.GET.get("company", "").strip()
    search = request.GET.get("q", "").strip()

    valid_statuses = {
        WhatsAppNumber.STATUS_PENDING_ACTIVATION,
        WhatsAppNumber.STATUS_VERIFYING,
        WhatsAppNumber.STATUS_ACTIVE,
        WhatsAppNumber.STATUS_SUSPENDED,
    }

    channels = WhatsAppNumber.objects.select_related(
        "company",
        "warehouse",
    ).order_by(
        "-activation_requested_at",
        "-pk",
    )

    if status_filter in valid_statuses:
        channels = channels.filter(
            activation_status=status_filter,
        )
    else:
        status_filter = ""

    if company_filter.isdigit():
        channels = channels.filter(
            company_id=int(company_filter),
        )
    else:
        company_filter = ""

    if search:
        channels = channels.filter(
            Q(company__name__icontains=search)
            | Q(channel_name__icontains=search)
            | Q(display_number__icontains=search)
        )

    companies = Company.objects.order_by("name")

    status_counts = {
        "all": WhatsAppNumber.objects.count(),
        "pending": WhatsAppNumber.objects.filter(
            activation_status=WhatsAppNumber.STATUS_PENDING_ACTIVATION,
        ).count(),
        "verifying": WhatsAppNumber.objects.filter(
            activation_status=WhatsAppNumber.STATUS_VERIFYING,
        ).count(),
        "active": WhatsAppNumber.objects.filter(
            activation_status=WhatsAppNumber.STATUS_ACTIVE,
        ).count(),
        "suspended": WhatsAppNumber.objects.filter(
            activation_status=WhatsAppNumber.STATUS_SUSPENDED,
        ).count(),
    }

    context = {
        "channels": channels,
        "companies": companies,
        "status_filter": status_filter,
        "company_filter": company_filter,
        "search": search,
        "status_counts": status_counts,
    }

    return render(
        request,
        "owner/whatsapp_requests_list.html",
        context,
    )


@user_passes_test(owner_required)
def whatsapp_request_detail(request, pk):
    channel = get_object_or_404(
        WhatsAppNumber.objects.select_related(
            "company",
            "warehouse",
        ),
        pk=pk,
    )

    previous_status = channel.activation_status

    if request.method == "POST":
        form = OwnerWhatsAppNumberForm(
            request.POST,
            instance=channel,
        )

        if form.is_valid():
            try:
                with transaction.atomic():
                    channel = form.save()
            except ValidationError as error:
                if hasattr(error, "message_dict"):
                    for field_name, error_list in error.message_dict.items():
                        target_field = field_name if field_name in form.fields else None

                        for error_message in error_list:
                            form.add_error(
                                target_field,
                                error_message,
                            )
                else:
                    for error_message in error.messages:
                        form.add_error(
                            None,
                            error_message,
                        )
            else:
                if previous_status != channel.activation_status:
                    messages.success(
                        request,
                        (
                            "Canal actualizado correctamente. "
                            f"Nuevo estado: "
                            f"{channel.get_activation_status_display()}."
                        ),
                    )
                else:
                    messages.success(
                        request,
                        "Configuración del canal guardada correctamente.",
                    )

                return redirect(
                    "owner:whatsapp_request_detail",
                    pk=channel.pk,
                )
        else:
            messages.error(
                request,
                "No se pudo guardar. Revisa los campos marcados.",
            )
    else:
        form = OwnerWhatsAppNumberForm(
            instance=channel,
        )

    context = {
        "channel": channel,
        "form": form,
    }

    return render(
        request,
        "owner/whatsapp_request_detail.html",
        context,
    )
