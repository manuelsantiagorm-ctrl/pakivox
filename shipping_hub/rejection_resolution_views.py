from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from accounts.capability_access import capability_required
from accounts.warehouse_access import ACCESS_APPROVE, ACCESS_OPERATE

from shipping_hub.services.batch_rejection_resolution import (
    resolve_rejection_by_cancellation,
    resolve_rejection_by_field_expense,
    resolve_rejection_by_new_label,
    resolve_rejection_by_repack,
)
from shipping_hub.models import LogisticsIncident
from shipping_hub.views import _get_request_company_or_403


def _empresa_con_incidencia(request, incident_id):
    """Empresa del usuario, solo si la incidencia es suya.

    Una incidencia ajena o inexistente responde 404 (antes reventaba en 500).
    """
    company = _get_request_company_or_403(request)
    get_object_or_404(LogisticsIncident, pk=incident_id, company=company)
    return company


def _back_to_incident(incident_id):
    return redirect("shipping_hub:incident_detail", incident_id=incident_id)


@capability_required(
    "shipping.incidents.resolve",
    required_level=ACCESS_OPERATE,
    require_specific_warehouse=False,
)
@require_POST
def incident_resolve_repack(request, incident_id):
    company = _empresa_con_incidencia(request, incident_id)
    notes = (request.POST.get("notes") or "").strip()
    try:
        resolve_rejection_by_repack(
            company_id=company.pk,
            incident_id=incident_id,
            notes=notes,
            actor=request.user,
        )
    except ValidationError as exc:
        messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
    else:
        messages.success(
            request, "Incidencia resuelta: reempacado y aceptado por la paquetería."
        )
    return _back_to_incident(incident_id)


@capability_required(
    "shipping.incidents.resolve",
    required_level=ACCESS_APPROVE,
    require_specific_warehouse=False,
)
@require_POST
def incident_resolve_new_label(request, incident_id):
    company = _empresa_con_incidencia(request, incident_id)
    new_carrier_name = (request.POST.get("new_carrier_name") or "").strip()
    new_tracking_number = (request.POST.get("new_tracking_number") or "").strip()
    notes = (request.POST.get("notes") or "").strip()
    try:
        resolve_rejection_by_new_label(
            company_id=company.pk,
            incident_id=incident_id,
            new_carrier_name=new_carrier_name,
            new_tracking_number=new_tracking_number,
            notes=notes,
            actor=request.user,
        )
    except ValidationError as exc:
        messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
    else:
        messages.success(
            request, "Guía regenerada correctamente. La incidencia quedó resuelta."
        )
    return _back_to_incident(incident_id)


@capability_required(
    "shipping.execute",
    required_level=ACCESS_APPROVE,
    require_specific_warehouse=False,
)
@require_POST
def incident_resolve_field_expense(request, incident_id):
    company = _empresa_con_incidencia(request, incident_id)
    new_carrier_name = (request.POST.get("expense_carrier_name") or "").strip()
    new_tracking_number = (request.POST.get("expense_tracking_number") or "").strip()
    currency = (request.POST.get("expense_currency") or "MXN").strip()
    receipt_file = request.FILES.get("receipt_file")

    try:
        amount = Decimal(request.POST.get("expense_amount") or "0")
    except InvalidOperation:
        messages.error(request, "El monto del gasto no es un número válido.")
        return _back_to_incident(incident_id)

    try:
        resolve_rejection_by_field_expense(
            company_id=company.pk,
            incident_id=incident_id,
            new_carrier_name=new_carrier_name,
            new_tracking_number=new_tracking_number,
            amount=amount,
            currency=currency,
            receipt_file=receipt_file,
            actor=request.user,
        )
    except ValidationError as exc:
        messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
    else:
        messages.success(
            request,
            "Gasto y guía nueva registrados. Queda pendiente de conciliar por finanzas.",
        )
    return _back_to_incident(incident_id)


@capability_required(
    "shipping.execute",
    required_level=ACCESS_APPROVE,
    require_specific_warehouse=False,
)
@require_POST
def incident_resolve_cancellation(request, incident_id):
    company = _empresa_con_incidencia(request, incident_id)
    notes = (request.POST.get("notes") or "").strip()
    try:
        resolve_rejection_by_cancellation(
            company_id=company.pk,
            incident_id=incident_id,
            notes=notes,
            actor=request.user,
        )
    except ValidationError as exc:
        messages.error(request, " ".join(getattr(exc, "messages", [str(exc)])))
    else:
        messages.warning(
            request, "Pedido cancelado y stock liberado. La incidencia quedó resuelta."
        )
    return _back_to_incident(incident_id)
