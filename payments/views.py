from pathlib import Path
from decimal import Decimal, InvalidOperation

import sentry_sdk

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from sales.models import Order
import re

from .models import (
    CustomerCredit,
    CustomerCreditApplication,
    Payment,
    PaymentRefundRequest,
)
from .access import can_approve_payments, is_active_company_owner
from accounts.capability_access import build_capability_context, user_has_capability
from messaging.conversation_scope import (
    get_conversation_for_order_user,
    get_or_create_conversation_for_order_user,
)
from warehouse.services.order_read_scope import (
    get_request_read_warehouse_ids,
    scope_orders_for_read,
    scope_payments_for_read,
)
from .services.customer_credit import (
    apply_customer_credit_to_order,
    convert_refund_to_customer_credit,
)
from .services.refunds import confirm_manual_full_refund, request_full_refund
from .simple_ui import build_payment_simple_step


def _authorize_payment_order_activity_route(
    request,
    route_name,
    order,
    *,
    plan_if_missing=False,
):
    """
    Autoriza una acción financiera contra TODAS las bodegas asociadas
    o potencialmente afectadas por el pedido.

    El resolver PS-3C1 no crea fulfillments, no reserva stock y no cambia
    estados. Si no puede determinar un alcance persistido/planificado, usa
    el fallback conservador a todas las bodegas activas de la empresa.
    """
    from accounts.activity_route_authorization import (
        authorize_activity_route,
    )
    from warehouse.services.order_authorization import (
        resolve_order_authorization_warehouses,
    )

    warehouses = resolve_order_authorization_warehouses(
        order,
        plan_if_missing=plan_if_missing,
        fallback_to_company=True,
    )

    return authorize_activity_route(
        request,
        "payments",
        route_name,
        warehouses=warehouses,
    )


def get_user_company(user):
    profile = getattr(user, "profile", None)
    if (
        profile is None
        or not profile.company_id
        or not profile.is_active
        or profile.employment_status != profile.STATUS_ACTIVE
    ):
        raise PermissionDenied("No se detectó una empresa activa para este trabajador.")
    return profile.company


def order_allows_payment(order):
    """
    Reglas:
    - No pagar pedidos cancelados.
    - No pagar pedidos entregados.
    - Si ya está enviado o empacado, todavía puede pagarse si tiene saldo,
      pero NO debe regresar estados operativos.
    """
    return order.status not in ["cancelled", "delivered"]


def parse_decimal(value, default="0"):
    try:
        return Decimal(str(value or default))
    except (InvalidOperation, TypeError, ValueError):
        raise ValidationError("El monto capturado no es válido.")


def get_payment_type_for_order(order, amount):
    if amount >= order.balance_due:
        if order.amount_paid > 0:
            return "remaining"
        return "full"

    return "deposit"


@login_required(login_url="/admin/login/")
def lista_pagos(request):
    company = get_user_company(request.user)
    read_warehouse_ids = get_request_read_warehouse_ids(
        request,
        company=company,
    )

    payments = Payment.objects.select_related(
        "company",
        "order",
        "order__customer",
        "registered_by",
        "reviewed_by",
    ).order_by("-created_at")

    orders = (
        Order.objects.select_related(
            "company",
            "customer",
        )
        .exclude(status__in=["cancelled", "delivered"])
        .order_by("-created_at")
    )

    payments = scope_payments_for_read(
        payments,
        company=company,
        warehouse_ids=read_warehouse_ids,
    )
    orders = scope_orders_for_read(
        orders,
        company=company,
        warehouse_ids=read_warehouse_ids,
    )

    q = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    method = request.GET.get("method", "").strip()

    if q:
        payments = (
            payments.filter(folio__icontains=q)
            | payments.filter(order__folio__icontains=q)
            | payments.filter(order__customer__full_name__icontains=q)
            | payments.filter(transaction_id__icontains=q)
        )
        orders = orders.filter(
            Q(folio__icontains=q) | Q(customer__full_name__icontains=q)
        )

    if status:
        payments = payments.filter(status=status)

    if method:
        payments = payments.filter(payment_method=method)

    pending_count = payments.filter(status__in=["pending", "under_review"]).count()
    approved_count = payments.filter(status="approved").count()
    rejected_count = payments.filter(status="rejected").count()

    total_approved = payments.filter(status="approved").aggregate(total=Sum("amount"))[
        "total"
    ] or Decimal("0.00")

    total_pending = payments.filter(
        status__in=["pending", "under_review"],
    ).aggregate(
        total=Sum("amount")
    )["total"] or Decimal("0.00")
    context = {
        "payments": payments,
        "orders": orders,
        "q": q,
        "status": status,
        "method": method,
        "pending_count": pending_count,
        "approved_count": approved_count,
        "rejected_count": rejected_count,
        "total_approved": total_approved,
        "total_pending": total_pending,
        "status_choices": Payment.STATUS_CHOICES,
        "method_choices": Payment.PAYMENT_METHOD_CHOICES,
    }

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return render(request, "payments/partials/_pagos_results.html", context)

    return render(request, "payments/lista_pagos.html", context)


@login_required(login_url="/admin/login/")
def detalle_pago(request, payment_id):
    company = get_user_company(request.user)
    read_warehouse_ids = get_request_read_warehouse_ids(
        request,
        company=company,
    )

    payment_query = Payment.objects.select_related(
        "company",
        "order",
        "order__customer",
        "registered_by",
        "reviewed_by",
    )

    payment_query = scope_payments_for_read(
        payment_query,
        company=company,
        warehouse_ids=read_warehouse_ids,
    )

    payment = get_object_or_404(payment_query, id=payment_id)

    capability_context = build_capability_context(request)
    capability_access_all = capability_context["capability_access_all"]
    capability_codes = capability_context["user_capability_codes"]

    def has_capability(code):
        return capability_access_all or code in capability_codes

    owner_access = is_active_company_owner(
        request.user,
        company=company,
    )
    can_approve_payment = can_approve_payments(request.user, company=company) and (
        payment.order.created_by_id != request.user.id or owner_access
    )
    can_cancel_payment = has_capability("payments.cancel")
    can_refund_payment = has_capability("payments.refund")
    can_confirm_refund = has_capability("payments.refund.confirm")
    can_convert_customer_credit = has_capability("payments.customer_credit.manage")
    can_view_payment_proof = has_capability("payments.proof.view")
    can_view_sales = has_capability("sales.view")

    picking_url = ""
    if payment.status == "approved" and payment.order:
        from warehouse.models import PickingOrder

        picking = (
            PickingOrder.objects.filter(
                company=company,
                order=payment.order,
            )
            .exclude(status=PickingOrder.STATUS_CANCELLED)
            .order_by("-id")
            .first()
        )
        if picking:
            picking_url = reverse(
                "warehouse:detalle_picking",
                kwargs={"picking_id": picking.id},
            )

    simple_payment_step = build_payment_simple_step(
        payment,
        can_approve_payment=can_approve_payment,
        picking_url=picking_url,
    )

    return render(
        request,
        "payments/detalle_pago.html",
        {
            "payment": payment,
            "simple_payment_step": simple_payment_step,
            "can_approve_payment": can_approve_payment,
            "can_cancel_payment": can_cancel_payment,
            "can_refund_payment": can_refund_payment,
            "can_confirm_refund": can_confirm_refund,
            "can_convert_customer_credit": can_convert_customer_credit,
            "can_view_payment_proof": can_view_payment_proof,
            "can_view_sales": can_view_sales,
            "refund_requests": payment.refund_requests.select_related(
                "requested_by", "confirmed_by"
            ).order_by("-requested_at"),
            "customer_credits": payment.customer_credits.select_related(
                "customer", "created_by", "source_refund_request"
            ).order_by("-created_at"),
        },
    )


@login_required(login_url="/admin/login/")
def ver_comprobante_pago(request, payment_id):
    """Entrega un comprobante solo dentro del scope autorizado."""
    company = get_user_company(request.user)
    read_warehouse_ids = get_request_read_warehouse_ids(
        request,
        company=company,
    )

    payment_query = Payment.objects.select_related(
        "company",
        "order",
        "order__customer",
    )

    payment_query = scope_payments_for_read(
        payment_query,
        company=company,
        warehouse_ids=read_warehouse_ids,
    )

    payment = get_object_or_404(
        payment_query,
        id=payment_id,
    )

    if not payment.proof_file:
        raise Http404("Este pago no tiene comprobante.")

    try:
        payment.proof_file.open("rb")
    except (FileNotFoundError, OSError):
        raise Http404("El comprobante no está disponible.")

    filename = Path(payment.proof_file.name).name

    response = FileResponse(
        payment.proof_file,
        as_attachment=False,
        filename=filename,
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"

    return response

@login_required(login_url="/admin/login/")
@require_POST
def registrar_pago(request):
    company = get_user_company(request.user)
    read_warehouse_ids = get_request_read_warehouse_ids(
        request,
        company=company,
    )

    order_id = request.POST.get("order")
    payment_method = request.POST.get("payment_method", "manual")
    status = request.POST.get("status", "pending")
    transaction_id = request.POST.get("transaction_id", "").strip()
    notes = request.POST.get("notes", "").strip()
    proof_file = request.FILES.get("proof_file")

    order_query = Order.objects.select_related(
        "company",
        "customer",
    )

    order_query = scope_orders_for_read(
        order_query,
        company=company,
        warehouse_ids=read_warehouse_ids,
    )

    order = get_object_or_404(order_query, id=order_id)

    _authorize_payment_order_activity_route(
        request,
        "registrar_pago",
        order,
        plan_if_missing=True,
    )

    if not order_allows_payment(order):
        messages.error(
            request,
            "No puedes registrar pagos en un pedido cancelado o entregado.",
        )
        return redirect("payments:lista_pagos")

    try:
        amount = parse_decimal(request.POST.get("amount"))
    except ValidationError as e:
        messages.error(request, str(e))
        return redirect("payments:lista_pagos")

    if amount <= 0:
        messages.error(request, "El monto del pago debe ser mayor a cero.")
        return redirect("payments:lista_pagos")

    order.calculate_totals()

    if order.balance_due <= 0:
        messages.error(
            request,
            "Este pedido ya no tiene saldo pendiente.",
        )
        return redirect("payments:lista_pagos")

    if amount > order.balance_due:
        messages.error(
            request,
            f"El pago no puede superar el saldo pendiente. "
            f"Saldo pendiente: ${order.balance_due:.2f}.",
        )
        return redirect("payments:lista_pagos")

    valid_methods = [choice[0] for choice in Payment.PAYMENT_METHOD_CHOICES]
    valid_statuses = [choice[0] for choice in Payment.STATUS_CHOICES]

    if payment_method not in valid_methods:
        messages.error(request, "Método de pago no válido.")
        return redirect("payments:lista_pagos")

    if status not in valid_statuses:
        messages.error(request, "Estado de pago no válido.")
        return redirect("payments:lista_pagos")

    if status in ["approved", "rejected", "refunded", "cancelled"]:
        messages.error(
            request,
            "Registra el pago como pendiente o en revisión. "
            "Después apruébalo o recházalo desde el panel.",
        )
        return redirect("payments:lista_pagos")

    payment_type = get_payment_type_for_order(order, amount)

    with transaction.atomic():
        payment = Payment.objects.create(
            company=order.company,
            order=order,
            registered_by=request.user,
            payment_method=payment_method,
            payment_type=payment_type,
            status=status,
            amount=amount,
            proof_file=proof_file,
            transaction_id=transaction_id,
            notes=notes,
        )

    messages.success(
        request,
        f"Pago {payment.folio} registrado correctamente.",
    )

    return redirect("payments:detalle_pago", payment_id=payment.id)


@login_required(login_url="/admin/login/")
@require_POST
def aprobar_pago(request, payment_id):
    company = get_user_company(request.user)

    payment_query = Payment.objects.select_related(
        "company",
        "order",
        "order__customer",
    )

    payment_query = payment_query.filter(company=company)

    payment = get_object_or_404(payment_query, id=payment_id)

    _authorize_payment_order_activity_route(
        request,
        "aprobar_pago",
        payment.order,
        plan_if_missing=True,
    )

    if payment.order.created_by_id == request.user.id and not is_active_company_owner(
        request.user, company=company
    ):
        raise PermissionDenied(
            "Separación de responsabilidades: no puedes aprobar el pago "
            "de un pedido creado por ti."
        )

    try:
        payment.approve(user=request.user)

    except ValidationError as e:
        messages.error(request, str(e))
        return redirect("payments:detalle_pago", payment_id=payment.id)

    except Exception as e:
        sentry_sdk.capture_exception(e)
        messages.error(request, str(e))
        return redirect("payments:detalle_pago", payment_id=payment.id)

    # BUG-046: la notificación de WhatsApp ya se dispara automáticamente
    # desde payments/signals.py (post_save cuando status cambia a
    # "approved"). Esta llamada manual duplicaba el mensaje al cliente.
    messages.success(
        request,
        f"Pago {payment.folio} aprobado. Pedido actualizado correctamente.",
    )

    return redirect("payments:detalle_pago", payment_id=payment.id)


@login_required(login_url="/admin/login/")
@require_POST
def rechazar_pago(request, payment_id):
    company = get_user_company(request.user)

    payment_query = Payment.objects.select_related(
        "company",
        "order",
        "order__customer",
    )

    payment_query = payment_query.filter(company=company)

    payment = get_object_or_404(payment_query, id=payment_id)

    _authorize_payment_order_activity_route(
        request,
        "rechazar_pago",
        payment.order,
        plan_if_missing=False,
    )

    reason = request.POST.get("reason", "").strip()

    if not reason:
        reason = "Pago rechazado desde panel de pagos."

    try:
        payment.reject(
            user=request.user,
            reason=reason,
        )

    except ValidationError as e:
        messages.error(request, str(e))
        return redirect("payments:detalle_pago", payment_id=payment.id)

    except Exception as e:
        sentry_sdk.capture_exception(e)
        messages.error(request, str(e))
        return redirect("payments:detalle_pago", payment_id=payment.id)

    messages.success(
        request,
        f"Pago {payment.folio} rechazado correctamente.",
    )

    return redirect("payments:detalle_pago", payment_id=payment.id)


@login_required(login_url="/admin/login/")
@require_POST
def cancelar_pago(request, payment_id):
    company = get_user_company(request.user)

    payment_query = Payment.objects.select_related(
        "company",
        "order",
        "order__customer",
    )

    payment_query = payment_query.filter(company=company)

    payment = get_object_or_404(payment_query, id=payment_id)

    _authorize_payment_order_activity_route(
        request,
        "cancelar_pago",
        payment.order,
        plan_if_missing=False,
    )

    reason = request.POST.get("reason", "").strip()

    if not reason:
        reason = "Pago cancelado desde panel de pagos."

    try:
        payment.cancel(
            user=request.user,
            reason=reason,
        )

    except ValidationError as e:
        messages.error(request, str(e))
        return redirect("payments:detalle_pago", payment_id=payment.id)

    except Exception as e:
        sentry_sdk.capture_exception(e)
        messages.error(request, str(e))
        return redirect("payments:detalle_pago", payment_id=payment.id)

    messages.success(
        request,
        f"Pago {payment.folio} cancelado correctamente.",
    )

    return redirect("payments:detalle_pago", payment_id=payment.id)


@login_required(login_url="/admin/login/")
@require_POST
def reembolsar_pago(request, payment_id):
    """CS-1C1: registra solicitud; NO marca dinero como devuelto."""
    company = get_user_company(request.user)
    payment = get_object_or_404(
        Payment.objects.filter(company=company).select_related("order"),
        id=payment_id,
    )

    _authorize_payment_order_activity_route(
        request,
        "reembolsar_pago",
        payment.order,
        plan_if_missing=False,
    )
    reason = request.POST.get("reason", "").strip()
    idempotency_key = request.POST.get("idempotency_key", "").strip()

    try:
        decision = request_full_refund(
            company_id=company.id,
            payment_id=payment.id,
            actor=request.user,
            reason=reason,
            idempotency_key=idempotency_key,
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("payments:detalle_pago", payment_id=payment.id)

    messages.warning(request, decision.message)
    return redirect("payments:detalle_pago", payment_id=payment.id)


@login_required(login_url="/admin/login/")
@require_POST
def confirmar_reembolso_pago(request, refund_id):
    """Confirma únicamente una devolución total que ya ocurrió fuera de PAKIVOX."""
    company = get_user_company(request.user)
    refund = get_object_or_404(
        PaymentRefundRequest.objects.filter(company=company).select_related(
            "order",
            "payment",
        ),
        id=refund_id
    )

    _authorize_payment_order_activity_route(
        request,
        "confirmar_reembolso_pago",
        refund.order,
        plan_if_missing=False,
    )
    reference = request.POST.get("confirmation_reference", "").strip()
    idempotency_key = request.POST.get("idempotency_key", "").strip()

    try:
        decision = confirm_manual_full_refund(
            company_id=company.id,
            refund_id=refund.id,
            actor=request.user,
            confirmation_reference=reference,
            idempotency_key=idempotency_key,
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("payments:detalle_pago", payment_id=refund.payment_id)

    messages.success(request, decision.message)
    return redirect("payments:detalle_pago", payment_id=refund.payment_id)


@login_required(login_url="/admin/login/")
@require_POST
def convertir_reembolso_saldo_favor(request, refund_id):
    """CS-1C3: cambia una devolución pendiente por crédito del cliente."""
    company = get_user_company(request.user)
    refund = get_object_or_404(
        PaymentRefundRequest.objects.filter(company=company).select_related(
            "order",
            "payment",
        ),
        id=refund_id
    )

    _authorize_payment_order_activity_route(
        request,
        "convertir_reembolso_saldo_favor",
        refund.order,
        plan_if_missing=False,
    )
    reason = request.POST.get("reason", "").strip()
    idempotency_key = request.POST.get("idempotency_key", "").strip()

    try:
        decision = convert_refund_to_customer_credit(
            company_id=company.id,
            refund_id=refund.id,
            actor=request.user,
            reason=reason,
            idempotency_key=idempotency_key,
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("payments:detalle_pago", payment_id=refund.payment_id)

    messages.success(request, decision.message)
    return redirect("payments:detalle_pago", payment_id=refund.payment_id)


@login_required(login_url="/admin/login/")
@require_POST
def aplicar_saldo_favor_pedido(request, order_id):
    """CS-1C4: aplica crédito existente a un pedido sin crear Payment ficticio."""
    company = get_user_company(request.user)
    order = get_object_or_404(
        Order.objects.filter(company=company).select_related("customer"), id=order_id
    )

    _authorize_payment_order_activity_route(
        request,
        "aplicar_saldo_favor_pedido",
        order,
        plan_if_missing=True,
    )
    credit_id = request.POST.get("credit_id", "").strip()
    amount = request.POST.get("amount", "").strip()
    reason = request.POST.get("reason", "").strip()
    idempotency_key = request.POST.get("idempotency_key", "").strip()

    try:
        if not credit_id:
            raise ValidationError("Selecciona un saldo a favor.")
        decision = apply_customer_credit_to_order(
            company_id=company.id,
            credit_id=int(credit_id),
            order_id=order.id,
            amount=parse_decimal(amount),
            actor=request.user,
            reason=reason,
            idempotency_key=idempotency_key,
        )
    except (TypeError, ValueError):
        messages.error(request, "El saldo a favor seleccionado no es válido.")
        return redirect("sales:detalle_pedido", order_id=order.id)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return redirect("sales:detalle_pedido", order_id=order.id)

    messages.success(request, decision.message)
    return redirect("sales:detalle_pedido", order_id=order.id)


@login_required(login_url="/admin/login/")
def pagos_pedido(request, order_id):
    company = get_user_company(request.user)
    read_warehouse_ids = get_request_read_warehouse_ids(
        request,
        company=company,
    )

    order_query = Order.objects.select_related(
        "company",
        "customer",
    ).prefetch_related(
        "payments",
    )

    order_query = scope_orders_for_read(
        order_query,
        company=company,
        warehouse_ids=read_warehouse_ids,
    )

    order = get_object_or_404(order_query, id=order_id)

    payments = order.payments.select_related(
        "registered_by",
        "reviewed_by",
    ).order_by("-created_at")

    # ==========================================================
    # CHAT FLOTANTE WHATSAPP EN PANTALLA DE PAGOS
    # Sirve para revisar capturas de transferencia/comprobantes
    # que el cliente mande por WhatsApp.
    # ==========================================================
    conversation_id = request.GET.get("conversation_id", "").strip()

    chat_conversation = None
    chat_messages = []
    can_reply_whatsapp = False

    if conversation_id:
        try:
            chat_conversation = get_conversation_for_order_user(
                user=request.user,
                order=order,
                conversation_id=conversation_id,
            )

            if chat_conversation:
                chat_messages = list(
                    chat_conversation.messages.select_related(
                        "agent",
                        "company",
                        "whatsapp_number",
                    ).order_by("created_at")[:120]
                )

        except Exception as error:
            sentry_sdk.capture_exception(error)
            chat_conversation = None
            chat_messages = []

    if chat_conversation:
        from messaging.models import ChannelAgentAssignment

        can_reply_whatsapp = ChannelAgentAssignment.objects.filter(
            company_id=company.id,
            whatsapp_number=chat_conversation.whatsapp_number,
            agent=request.user,
            is_active=True,
            can_reply=True,
        ).exists()

    return render(
        request,
        "payments/pagos_pedido.html",
        {
            "order": order,
            "payments": payments,
            "method_choices": Payment.PAYMENT_METHOD_CHOICES,
            "status_choices": Payment.STATUS_CHOICES,
            # Chat flotante WhatsApp
            "chat_conversation": chat_conversation,
            "chat_messages": chat_messages,
            "chat_return_url": request.get_full_path(),
            "can_reply_whatsapp": can_reply_whatsapp,
        },
    )


def payments_model_has_field(model, field_name):
    try:
        model._meta.get_field(field_name)
        return True
    except Exception:
        return False


def normalize_payment_whatsapp_number(phone):
    phone = phone or ""
    phone = re.sub(r"\D", "", phone)

    if not phone:
        return ""

    if len(phone) == 10:
        phone = f"52{phone}"

    return phone


@login_required(login_url="/admin/login/")
def abrir_chat_flotante_pago(request, order_id):
    request_company = get_user_company(request.user)
    read_warehouse_ids = get_request_read_warehouse_ids(
        request,
        company=request_company,
    )
    order_query = scope_orders_for_read(
        Order.objects.select_related(
            "company",
            "customer",
        ),
        company=request_company,
        warehouse_ids=read_warehouse_ids,
    )
    order = get_object_or_404(
        order_query,
        id=order_id,
    )

    conversation = get_or_create_conversation_for_order_user(
        user=request.user,
        order=order,
    )

    if conversation is None:
        messages.error(
            request,
            (
                "No hay un canal de WhatsApp autorizado para este pedido "
                "o el cliente no tiene un teléfono válido."
            ),
        )
        return redirect(
            "payments:pagos_pedido",
            order_id=order.id,
        )

    customer_name = (
        getattr(order.customer, "full_name", "")
        or getattr(order.customer, "name", "")
        or f"Cliente #{order.customer_id}"
    )

    messages.success(
        request,
        (
            "Chat flotante abierto para revisar comprobantes de "
            f"{customer_name}."
        ),
    )

    payment_url = reverse(
        "payments:pagos_pedido",
        kwargs={"order_id": order.id},
    )

    return redirect(
        f"{payment_url}?conversation_id={conversation.id}"
    )
