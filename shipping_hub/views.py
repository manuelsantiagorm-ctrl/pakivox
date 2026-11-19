from functools import wraps
import base64
import json
from io import BytesIO
from pathlib import Path, PurePosixPath
from django.views.decorators.clickjacking import xframe_options_exempt

import qrcode

from django.apps import apps
import sentry_sdk
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import (
    ImproperlyConfigured,
    PermissionDenied,
    ValidationError,
)
from shipping_hub.strategies.base import (
    ShippingProviderError,
)
from shipping_hub.strategies.resolver import (
    get_shipping_strategy,
)
from accounts.warehouse_access import (
    ACCESS_APPROVE,
    ACCESS_OPERATE,
    ACCESS_VIEW,
    require_active_warehouse,
    user_can_access_warehouse,
)

from decimal import Decimal
import csv
import json
from datetime import timedelta
from django.conf import settings

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.http import require_GET
import uuid

from django.core.files.storage import default_storage
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.urls import reverse
from django.utils.text import get_valid_filename

from .forms import (
    CubicationPackageFormSet,
    ExternalShippingLabelForm,
    PackageExternalLabelForm,
    CustomCarrierForm,
    ManualTrackingEventForm,
    LocalDeliveryDriverForm,
    LocalDeliveryForm,
    LocalDeliveryVehicleForm,
    ShippingIntegrationForm,
    ShippingOriginAddressForm,
)
from .services import (
    EnviaAPIError,
    prepare_cubication,
)
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .services import (
    EnviaAPIError,
    prepare_cubication,
)
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render

from .forms import CubicationPackageFormSet
from shipping_hub.services.quote_selection import freeze_existing_quote
from shipping_hub.services.lifecycle_service import (
    SOURCE_USER_ACTION,
    transition_package,
    transition_shipment,
)

from .models import (
    CarrierShipment,
    CustomCarrier,
    LocalDeliveryAssignment,
    LocalDeliveryDriver,
    LocalDeliveryRoute,
    LocalDeliveryVehicle,
    ShipmentPackage,
    ShipmentTrackingEvent,
    ShippingAuditEvent,
    ShippingPrintItem,
    ShippingPrintJob,
    ShippingQuote,
    TrackingWebhookReceipt,
)
from .services import prepare_cubication
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect, render
from decimal import Decimal
from .forms import CubicationPackageFormSet
from .models import (
    CarrierShipment,
    ShipmentPackage,
    ShippingQuote,
)
from .services import prepare_cubication
from .forms import (
    ShippingIntegrationForm,
    ShippingOriginAddressForm,
)
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from .models import (
    CompanyShippingIntegration,
    ShippingOriginAddress,
)
from .models import CompanyShippingIntegration
from shipping_hub.services.integration_credentials import (
    IntegrationCredentialError,
    get_integration_api_token,
)
from shipping_hub.services.purchase_confirmation import (
    build_purchase_confirmation,
    validate_purchase_confirmation,
)
from shipping_hub.models import ShippingPurchase
from shipping_hub.services.purchase_ui import build_purchase_ui
from shipping_hub.services.manual_recovery import (
    resolve_skydropx_purchase_by_exact_id,
)
from shipping_hub.services.multi_provider import quote_all_providers
from .services import EnviaAPIError, EnviaClient
from .services.tracking import receive_webhook, verify_signature
from .services.provider_event_engine import (
    parse_json_body,
    store_provider_event,
)
from .forms_delivery import DeliveryEvidenceForm
from .models import DeliveryEvidence, DeliveryEvidenceAsset
from .services.delivery_evidence import register_delivery_evidence
from .forms_phase9 import IncidentActionForm, IncidentForm, ReconciliationForm, RetryForm, ReturnForm
from .models import DeliveryRetry, LogisticsIncident, ShipmentReturn, ShippingReconciliation
from .services.incidents import create_incident, create_retry, create_return, reconcile_shipment, transition_incident
from .services.dashboard import build_dashboard, dashboard_shipments
from django.http import HttpResponse
from django.utils.dateparse import parse_date


def _dashboard_period(request):
    today = timezone.localdate()
    start = parse_date(request.GET.get("start", "")) or (today - timedelta(days=29))
    end = parse_date(request.GET.get("end", "")) or today
    if start > end:
        start, end = end, start
    if (end - start).days > 366:
        start = end - timedelta(days=366)
    return start, end


def logistics_dashboard(request):
    company = _get_request_company_or_403(request)
    warehouse = require_active_warehouse(request, required_level=ACCESS_VIEW)
    if warehouse.company_id != company.id or not user_can_access_warehouse(request.user, warehouse, required_level=ACCESS_VIEW):
        raise PermissionDenied("No tienes acceso a esta bodega.")
    start, end = _dashboard_period(request)
    data = build_dashboard(dashboard_shipments(company, warehouse, start, end))
    data.update({
        "warehouse": warehouse, "start": start, "end": end,
        "chart_labels_json": json.dumps(data["chart_labels"]),
        "chart_values_json": json.dumps(data["chart_values"]),
    })
    return render(request, "shipping_hub/logistics_dashboard.html", data)


def logistics_dashboard_export(request):
    company = _get_request_company_or_403(request)
    warehouse = require_active_warehouse(request, required_level=ACCESS_VIEW)
    if warehouse.company_id != company.id or not user_can_access_warehouse(request.user, warehouse, required_level=ACCESS_VIEW):
        raise PermissionDenied("No tienes acceso a esta bodega.")
    start, end = _dashboard_period(request)
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="shipping_{start}_{end}.csv"'
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(["ID", "Rastreo", "Proveedor", "Transportista", "Estado", "Costo", "Moneda", "Creado", "Actualizado"])
    for shipment in dashboard_shipments(company, warehouse, start, end):
        writer.writerow([shipment.pk, shipment.master_tracking_number, shipment.get_provider_display(), shipment.carrier_name, shipment.get_status_display(), shipment.total_cost, shipment.currency, timezone.localtime(shipment.created_at).isoformat(), timezone.localtime(shipment.updated_at).isoformat()])
    return response


def _tracking_shipments(company, warehouse):
    return (
        CarrierShipment.objects.for_company(company)
        .select_related("shipping_order", "shipping_order__order", "selected_quote")
        .prefetch_related("packages", "tracking_events", "delivery_evidences__assets", "delivery_evidences__package_items__package")
        .filter(shipping_order__packing_order__picking_order__warehouse=warehouse)
    )


def tracking_center(request):
    company = _get_request_company_or_403(request)
    warehouse = require_active_warehouse(request, required_level=ACCESS_VIEW)
    if warehouse.company_id != company.id or not user_can_access_warehouse(
        request.user, warehouse, required_level=ACCESS_VIEW
    ):
        raise PermissionDenied("No tienes acceso a esta bodega.")
    shipments = _tracking_shipments(company, warehouse)
    query = str(request.GET.get("q") or "").strip()
    status = str(request.GET.get("status") or "").strip()
    provider = str(request.GET.get("provider") or "").strip()
    if query:
        shipments = shipments.filter(
            Q(master_tracking_number__icontains=query)
            | Q(shipping_order__order__folio__icontains=query)
            | Q(carrier_name__icontains=query)
        )
    if status in dict(CarrierShipment.STATUS_CHOICES):
        shipments = shipments.filter(status=status)
    if provider:
        shipments = shipments.filter(provider=provider)
    return render(request, "shipping_hub/tracking_center.html", {
        "shipments": shipments.order_by("-updated_at", "-id")[:200],
        "status_choices": CarrierShipment.STATUS_CHOICES,
        "provider_choices": CompanyShippingIntegration.PROVIDER_CHOICES,
        "query": query, "selected_status": status,
        "selected_provider": provider, "warehouse": warehouse,
    })


def tracking_detail(request, shipment_id):
    company = _get_request_company_or_403(request)
    warehouse = require_active_warehouse(request, required_level=ACCESS_VIEW)
    shipment = get_object_or_404(_tracking_shipments(company, warehouse), pk=shipment_id)
    return render(request, "shipping_hub/tracking_detail.html", {
        "shipment": shipment, "events": shipment.tracking_events.all()[:200],
        "delivery_evidences": shipment.delivery_evidences.all()[:50],
    })


def delivery_evidence_create(request, shipment_id):
    company = _get_request_company_or_403(request)
    warehouse = require_active_warehouse(request, required_level=ACCESS_OPERATE)
    shipment = get_object_or_404(_tracking_shipments(company, warehouse), pk=shipment_id)
    form = DeliveryEvidenceForm(request.POST or None, request.FILES or None, shipment=shipment)
    if request.method == "POST" and form.is_valid():
        try:
            evidence, created = register_delivery_evidence(shipment=shipment, warehouse=warehouse, user=request.user, cleaned_data=form.cleaned_data)
        except (ValidationError, ValueError) as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "La evidencia quedó registrada." if created else "La evidencia ya existía; no se duplicó.")
            return redirect("shipping_hub:delivery_evidence_detail", evidence_id=evidence.pk)
    return render(request, "shipping_hub/delivery_evidence_form.html", {"form": form, "shipment": shipment, "warehouse": warehouse})


def delivery_evidence_detail(request, evidence_id):
    company = _get_request_company_or_403(request); warehouse = require_active_warehouse(request, required_level=ACCESS_VIEW)
    evidence = get_object_or_404(DeliveryEvidence.objects.for_company(company).select_related("carrier_shipment", "warehouse", "created_by").prefetch_related("assets", "package_items__package").filter(warehouse=warehouse), pk=evidence_id)
    return render(request, "shipping_hub/delivery_evidence_detail.html", {"evidence": evidence})


def delivery_evidence_asset(request, evidence_id, asset_id):
    company = _get_request_company_or_403(request); warehouse = require_active_warehouse(request, required_level=ACCESS_VIEW)
    asset = get_object_or_404(DeliveryEvidenceAsset.objects.for_company(company).select_related("evidence").filter(evidence_id=evidence_id, evidence__warehouse=warehouse), pk=asset_id)
    try: handle = asset.file.open("rb")
    except (FileNotFoundError, OSError): raise Http404("El archivo de evidencia no está disponible.")
    response = FileResponse(handle, content_type=asset.content_type or "application/octet-stream")
    response["Content-Disposition"] = f'inline; filename="{get_valid_filename(asset.original_name)}"'; response["Cache-Control"] = "private, no-store"
    return response


def _phase9_shipments(company, warehouse):
    return _tracking_shipments(company, warehouse).prefetch_related("logistics_incidents", "returns", "delivery_retries", "reconciliations")


def incident_center(request):
    company = _get_request_company_or_403(request)
    warehouse = require_active_warehouse(request, required_level=ACCESS_VIEW)
    incidents = LogisticsIncident.objects.for_company(company).filter(warehouse=warehouse).select_related("carrier_shipment", "package", "opened_by", "assigned_to")
    status = str(request.GET.get("status") or "").strip(); severity = str(request.GET.get("severity") or "").strip(); query = str(request.GET.get("q") or "").strip()
    if status in dict(LogisticsIncident.STATUS_CHOICES): incidents = incidents.filter(status=status)
    if severity in dict(LogisticsIncident.SEVERITY_CHOICES): incidents = incidents.filter(severity=severity)
    if query: incidents = incidents.filter(Q(title__icontains=query) | Q(description__icontains=query) | Q(carrier_shipment__master_tracking_number__icontains=query) | Q(carrier_shipment__shipping_order__order__folio__icontains=query))
    reconciliations = ShippingReconciliation.objects.for_company(company).filter(warehouse=warehouse).select_related("carrier_shipment", "checked_by")[:30]
    return render(request, "shipping_hub/incident_center.html", {"incidents": incidents[:200], "reconciliations": reconciliations, "status_choices": LogisticsIncident.STATUS_CHOICES, "severity_choices": LogisticsIncident.SEVERITY_CHOICES, "selected_status": status, "selected_severity": severity, "query": query, "warehouse": warehouse})


def incident_create(request, shipment_id):
    company = _get_request_company_or_403(request); warehouse = require_active_warehouse(request, required_level=ACCESS_OPERATE)
    shipment = get_object_or_404(_phase9_shipments(company, warehouse), pk=shipment_id)
    form = IncidentForm(request.POST or None, shipment=shipment, company=company)
    if request.method == "POST" and form.is_valid():
        try: incident = create_incident(shipment=shipment, warehouse=warehouse, user=request.user, cleaned_data=form.cleaned_data)
        except ValidationError as exc: form.add_error(None, exc)
        else:
            messages.success(request, "Incidencia logística registrada.")
            return redirect("shipping_hub:incident_detail", incident_id=incident.pk)
    return render(request, "shipping_hub/incident_form.html", {"form": form, "shipment": shipment, "warehouse": warehouse})


def _incident_or_404(request, incident_id, required_level=ACCESS_VIEW):
    company = _get_request_company_or_403(request); warehouse = require_active_warehouse(request, required_level=required_level)
    incident = get_object_or_404(LogisticsIncident.objects.for_company(company).filter(warehouse=warehouse).select_related("carrier_shipment", "package", "opened_by", "assigned_to").prefetch_related("events", "returns", "delivery_retries"), pk=incident_id)
    return company, warehouse, incident


def incident_detail(request, incident_id):
    company, warehouse, incident = _incident_or_404(request, incident_id)
    return render(request, "shipping_hub/incident_detail.html", {"incident": incident, "action_form": IncidentActionForm(), "return_form": ReturnForm(shipment=incident.carrier_shipment, company=company), "retry_form": RetryForm(), "warehouse": warehouse})


@require_POST
def incident_transition(request, incident_id):
    company, warehouse, incident = _incident_or_404(request, incident_id, ACCESS_APPROVE)
    form = IncidentActionForm(request.POST)
    if form.is_valid():
        try: transition_incident(incident=incident, user=request.user, **form.cleaned_data)
        except ValidationError as exc: messages.error(request, " ".join(exc.messages))
        else: messages.success(request, "Estado de la incidencia actualizado.")
    else: messages.error(request, "Revisa el estado y las notas.")
    return redirect("shipping_hub:incident_detail", incident_id=incident.pk)


@require_POST
def return_create(request, incident_id):
    company, warehouse, incident = _incident_or_404(request, incident_id, ACCESS_APPROVE)
    form = ReturnForm(request.POST, shipment=incident.carrier_shipment, company=company)
    if form.is_valid():
        try: create_return(incident=incident, user=request.user, cleaned_data=form.cleaned_data)
        except ValidationError as exc: messages.error(request, " ".join(exc.messages))
        else: messages.success(request, "Devolución registrada.")
    else: messages.error(request, "Revisa los datos de la devolución.")
    return redirect("shipping_hub:incident_detail", incident_id=incident.pk)


@require_POST
def retry_create(request, incident_id):
    company, warehouse, incident = _incident_or_404(request, incident_id, ACCESS_OPERATE)
    form = RetryForm(request.POST)
    if form.is_valid():
        try: create_retry(incident=incident, user=request.user, cleaned_data=form.cleaned_data)
        except ValidationError as exc: messages.error(request, " ".join(exc.messages))
        else: messages.success(request, "Reintento de entrega programado.")
    else: messages.error(request, "Revisa la fecha e instrucciones del reintento.")
    return redirect("shipping_hub:incident_detail", incident_id=incident.pk)


def reconciliation_create(request, shipment_id):
    company = _get_request_company_or_403(request); warehouse = require_active_warehouse(request, required_level=ACCESS_APPROVE)
    shipment = get_object_or_404(_phase9_shipments(company, warehouse), pk=shipment_id)
    form = ReconciliationForm(request.POST or None, initial={"provider_status": shipment.status, "provider_cost": shipment.total_cost, "currency": shipment.currency})
    if request.method == "POST" and form.is_valid():
        record = reconcile_shipment(shipment=shipment, warehouse=warehouse, user=request.user, **form.cleaned_data)
        messages.success(request, "Conciliación completada sin diferencias." if record.status == ShippingReconciliation.STATUS_MATCHED else "Conciliación registrada con diferencias.")
        return redirect("shipping_hub:tracking_detail", shipment_id=shipment.pk)
    return render(request, "shipping_hub/reconciliation_form.html", {"form": form, "shipment": shipment, "warehouse": warehouse})


def tracking_events_json(request, shipment_id):
    company = _get_request_company_or_403(request)
    warehouse = require_active_warehouse(request, required_level=ACCESS_VIEW)
    shipment = get_object_or_404(_tracking_shipments(company, warehouse), pk=shipment_id)
    events = [{
        "id": event.pk, "code": event.event_code, "name": event.event_name,
        "description": event.description, "location": event.location,
        "occurred_at": event.occurred_at.isoformat(),
    } for event in shipment.tracking_events.all()[:200]]
    return JsonResponse({"shipment_status": shipment.status, "events": events})


@csrf_exempt
@require_POST
def tracking_webhook(request, provider, integration_id):
    integration = get_object_or_404(
        CompanyShippingIntegration, pk=integration_id,
        provider=provider, is_active=True,
    )
    signature = request.headers.get("X-WMS360-Signature") or request.headers.get("X-Webhook-Signature")
    try:
        if not verify_signature(integration=integration, body=request.body, supplied_signature=signature):
            return JsonResponse({"ok": False}, status=401)
        payload = json.loads(request.body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"ok": False}, status=400)
    receipt, created = receive_webhook(integration=integration, body=request.body, payload=payload)
    return JsonResponse(
        {"ok": True, "accepted": created, "status": receipt.status},
        status=202 if created else 200,
    )


@csrf_exempt
@require_POST
def provider_event_webhook(request, provider, integration_id):
    """
    Endpoint durable del Event Engine.

    Solo valida, deduplica y persiste. Nunca actualiza envíos dentro del request
    y nunca llama endpoints de compra o conciliación.
    """
    integration = get_object_or_404(
        CompanyShippingIntegration,
        pk=integration_id,
        provider=provider,
        is_active=True,
    )

    try:
        payload = parse_json_body(request.body)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse(
            {"ok": False, "error": "invalid_json"},
            status=400,
        )

    event, created, signature_status = store_provider_event(
        integration=integration,
        body=request.body,
        payload=payload,
        headers=request.headers,
    )

    if event is None:
        return JsonResponse(
            {
                "ok": False,
                "error": "invalid_signature",
                "signature_status": signature_status,
            },
            status=401,
        )

    return JsonResponse(
        {
            "ok": True,
            "accepted": created,
            "duplicate": not created,
            "event_id": event.pk,
            "status": event.status,
        },
        status=202 if created else 200,
    )


PRINTABLE_PACKAGE_STATUSES = {
    ShipmentPackage.STATUS_LABEL_READY,
    ShipmentPackage.STATUS_PRINTED,
    ShipmentPackage.STATUS_SHIPPED,
    ShipmentPackage.STATUS_IN_TRANSIT,
    ShipmentPackage.STATUS_OUT_FOR_DELIVERY,
}


# ============================================================
# OBTENER EMPRESA DEL USUARIO
# ============================================================
def get_user_company_safe(user, request=None):
    """
    Obtiene la empresa actual sin usar fallbacks inseguros.

    Si no encuentra empresa, devuelve None.
    """

    if request is not None:
        company = getattr(request, "company", None)

        if company is not None:
            return company

    if not user or not user.is_authenticated:
        return None

    # Caso: user.profile.company
    try:
        profile = user.profile

        if profile and getattr(profile, "company_id", None):
            return profile.company
    except Exception:
        pass

    # Caso: user.company
    try:
        company = getattr(user, "company", None)

        if company and getattr(company, "pk", None):
            return company
    except Exception:
        pass

    # BÃºsqueda de un modelo dentro de accounts
    # que tenga relaciÃ³n con user y company.
    try:
        accounts_config = apps.get_app_config("accounts")

        for model in accounts_config.get_models():
            field_names = {field.name for field in model._meta.fields}

            if {"user", "company"}.issubset(field_names):
                profile = (
                    model.objects.filter(user=user).select_related("company").first()
                )

                if profile and getattr(profile, "company_id", None):
                    return profile.company

    except Exception:
        pass

    sentry_sdk.capture_message(
        f"get_user_company_safe: no company found for user_id={getattr(user, 'pk', None)}",
        level="warning",
    )
    return None


# ============================================================
# PERMISO PARA ADMINISTRAR SHIPPING HUB
# ============================================================


def shipping_hub_manager_required(view_func):
    """
    Permite acceder a usuarios autenticados que tengan
    una empresa asignada.

    No depende de Django Admin.
    """

    @login_required
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        company = get_user_company_safe(
            request.user,
            request=request,
        )

        if company is None:
            raise PermissionDenied("No se detectó una empresa para este usuario.")

        return view_func(
            request,
            *args,
            **kwargs,
        )

    return wrapped


def _get_request_company_or_403(request):
    """
    Obtiene la empresa actual.

    Si no existe empresa, bloquea la operación.
    """

    company = get_user_company_safe(
        request.user,
        request=request,
    )

    if company is None:
        raise PermissionDenied("No se detectó una empresa para este usuario.")

    return company


def _authorize_shipping_warehouse(request, cubication, *, required_level):
    """Autoriza contra la bodega real del envío; nunca contra un ID del cliente."""
    warehouse = cubication.warehouse
    selected_warehouse = require_active_warehouse(
        request, required_level=required_level
    )
    if selected_warehouse.pk != warehouse.pk:
        raise PermissionDenied(
            "Selecciona la bodega específica a la que pertenece este envío."
        )
    if warehouse.company_id != cubication.shipping_order.company_id:
        raise PermissionDenied("La bodega y el envío pertenecen a empresas diferentes.")
    if not user_can_access_warehouse(
        request.user, warehouse, required_level=required_level
    ):
        raise PermissionDenied(
            "No tienes el nivel requerido para operar la bodega de este envío."
        )
    return warehouse


def _get_company_integration(request, integration_id):
    """
    Obtiene una integración exclusivamente de la empresa actual.
    """

    company = _get_request_company_or_403(request)

    integration = get_object_or_404(
        CompanyShippingIntegration.objects.select_related("company"),
        pk=integration_id,
        company=company,
    )

    return company, integration


# ============================================================
# LISTADO HTML DE INTEGRACIONES
# ============================================================


@shipping_hub_manager_required
def integrations_list(request):
    company = _get_request_company_or_403(request)

    integrations = CompanyShippingIntegration.objects.for_company(company).order_by(
        "provider",
        "environment",
    )

    context = {
        "company": company,
        "integrations": integrations,
        "integration_count": integrations.count(),
        "active_count": integrations.filter(is_active=True).count(),
        "connected_count": integrations.filter(
            last_connection_test_success=True
        ).count(),
    }

    return render(
        request,
        "shipping_hub/integrations_list.html",
        context,
    )


# ============================================================
# CREAR INTEGRACIÓN
# ============================================================

# ============================================================
# CREAR INTEGRACIÓN
# ============================================================


@shipping_hub_manager_required
def integration_create(request):
    company = _get_request_company_or_403(request)

    initial_data = {
        "provider": (CompanyShippingIntegration.PROVIDER_ENVIA),
        "account_mode": (CompanyShippingIntegration.ACCOUNT_MODE_COMPANY),
        "environment": (CompanyShippingIntegration.ENVIRONMENT_SANDBOX),
        "preferred_label_format": (CompanyShippingIntegration.FORMAT_PDF),
        "fallback_label_format": (CompanyShippingIntegration.FORMAT_ZPL),
        "is_active": False,
        "is_primary": False,
    }

    if request.method == "POST":
        form = ShippingIntegrationForm(
            request.POST,
            company=company,
        )

        if form.is_valid():
            try:
                with transaction.atomic():
                    integration = form.save()

                messages.success(
                    request,
                    (
                        "Integración guardada correctamente. "
                        "Ahora realiza la prueba de conexión "
                        "antes de activarla."
                    ),
                )

                return redirect("shipping_hub:integrations_list")

            except ValidationError as exc:
                form.add_error(
                    None,
                    exc,
                )

            except IntegrityError:
                form.add_error(
                    None,
                    (
                        "Ya existe una integración de este "
                        "proveedor para el entorno seleccionado."
                    ),
                )

    else:
        form = ShippingIntegrationForm(
            initial=initial_data,
            company=company,
        )

    context = {
        "company": company,
        "form": form,
        "editing": False,
        "integration": None,
        "has_stored_credentials": False,
    }

    return render(
        request,
        "shipping_hub/integration_form.html",
        context,
    )


# ============================================================
# EDITAR INTEGRACIÓN
# ============================================================


@shipping_hub_manager_required
def integration_edit(
    request,
    integration_id,
):
    company, integration = _get_company_integration(
        request,
        integration_id,
    )

    if request.method == "POST":
        form = ShippingIntegrationForm(
            request.POST,
            instance=integration,
            company=company,
        )

        if form.is_valid():
            try:
                with transaction.atomic():
                    updated_integration = form.save()

                messages.success(
                    request,
                    ("Configuración actualizada " "correctamente."),
                )

                if not updated_integration.is_active and not (
                    updated_integration.last_connection_test_success
                ):
                    messages.info(
                        request,
                        (
                            "La integración quedó inactiva "
                            "hasta realizar nuevamente la "
                            "prueba de conexión."
                        ),
                    )

                return redirect("shipping_hub:integrations_list")

            except ValidationError as exc:
                form.add_error(
                    None,
                    exc,
                )

            except IntegrityError:
                form.add_error(
                    None,
                    (
                        "Ya existe una integración de este "
                        "proveedor para el entorno seleccionado."
                    ),
                )

    else:
        form = ShippingIntegrationForm(
            instance=integration,
            company=company,
        )

    context = {
        "company": company,
        "form": form,
        "editing": True,
        "integration": integration,
        "has_stored_credentials": bool(integration.credentials_encrypted),
    }

    return render(
        request,
        "shipping_hub/integration_form.html",
        context,
    )


# ============================================================
# PROBAR CONEXIÓN CON ENVIA
# ============================================================
@require_POST
@shipping_hub_manager_required
def integration_test(
    request,
    integration_id,
):
    company, integration = _get_company_integration(
        request,
        integration_id,
    )

    try:
        strategy = get_shipping_strategy(
            integration=integration,
            company=company,
            require_active=False,
        )

        result = strategy.test_connection()

        if not result.get("ok"):
            raise ShippingProviderError(
                (
                    result.get("message")
                    or "El proveedor rechazó la prueba de conexión."
                ),
                error_code="connection_test_failed",
            )

        integration.last_connection_test_at = timezone.now()

        integration.last_connection_test_success = True
        integration.last_connection_error = ""

        integration.save(
            update_fields=[
                "last_connection_test_at",
                "last_connection_test_success",
                "last_connection_error",
                "updated_at",
            ]
        )

        carrier_count = int(result.get("carrier_count") or 0)

        if carrier_count:
            success_message = (
                "Conexión correcta con "
                f"{integration.get_provider_display()}. "
                f"Se detectaron {carrier_count} "
                "paqueterías o servicios disponibles."
            )
        else:
            success_message = (
                "Conexión correcta con " f"{integration.get_provider_display()}."
            )

        messages.success(
            request,
            success_message,
        )

    except (
        ShippingProviderError,
        ValidationError,
    ) as error:
        integration.last_connection_test_at = timezone.now()

        integration.last_connection_test_success = False

        integration.last_connection_error = str(error)[:2000]

        # Una conexión fallida nunca debe permanecer activa.
        integration.is_active = False

        integration.save(
            update_fields=[
                "last_connection_test_at",
                "last_connection_test_success",
                "last_connection_error",
                "is_active",
                "updated_at",
            ]
        )

        messages.error(
            request,
            (
                "No fue posible validar la conexión con "
                f"{integration.get_provider_display()}: "
                f"{error}"
            ),
        )

    return redirect("shipping_hub:integrations_list")


# ============================================================
# ACTIVAR O DESACTIVAR INTEGRACIÓN
# ============================================================


@require_POST
@shipping_hub_manager_required
def integration_toggle(request, integration_id):
    company = _get_request_company_or_403(request)

    with transaction.atomic():
        integration = get_object_or_404(
            CompanyShippingIntegration.objects.select_for_update(),
            pk=integration_id,
            company=company,
        )

        requested_state = request.POST.get("state", "").strip().lower()

        should_activate = requested_state == "active"

        if should_activate:
            if not integration.last_connection_test_success:
                messages.error(
                    request,
                    ("Primero realiza una prueba de " "conexión exitosa."),
                )

                return redirect("shipping_hub:integrations_list")

            # Solo una conexión activa del mismo proveedor
            # por empresa.
            (
                CompanyShippingIntegration.objects.for_company(company)
                .filter(
                    provider=integration.provider,
                    is_active=True,
                )
                .exclude(pk=integration.pk)
                .update(
                    is_active=False,
                    is_primary=False,
                )
            )

            integration.is_active = True

            message = "Integración activada correctamente."

        else:
            integration.is_active = False
            integration.is_primary = False

            message = "Integración desactivada correctamente."

        integration.save(
            update_fields=[
                "is_active",
                "is_primary",
                "updated_at",
            ]
        )

    messages.success(
        request,
        message,
    )

    return redirect("shipping_hub:integrations_list")


# ============================================================
# DIRECCIONES LOGÃSTICAS DE ORIGEN
# ============================================================


@shipping_hub_manager_required
def origins_list(request):
    company = _get_request_company_or_403(request)

    origins = (
        ShippingOriginAddress.objects.for_company(company)
        .select_related("warehouse")
        .order_by("warehouse__name")
    )

    return render(
        request,
        "shipping_hub/origins_list.html",
        {
            "company": company,
            "origins": origins,
            "origin_count": origins.count(),
            "active_origin_count": origins.filter(is_active=True).count(),
        },
    )


@shipping_hub_manager_required
def origin_create(request):
    company = _get_request_company_or_403(request)

    if request.method == "POST":
        form = ShippingOriginAddressForm(
            request.POST,
            company=company,
        )

        if form.is_valid():
            try:
                with transaction.atomic():
                    origin = form.save(commit=False)

                    origin.company = company
                    origin.full_clean()
                    origin.save()

                messages.success(
                    request,
                    "Dirección de origen guardada correctamente.",
                )

                return redirect("shipping_hub:origins_list")

            except ValidationError as exc:
                form.add_error(
                    None,
                    exc,
                )

            except IntegrityError:
                form.add_error(
                    "warehouse",
                    ("Esta bodega ya tiene una dirección " "logística configurada."),
                )
    else:
        form = ShippingOriginAddressForm(
            company=company,
            initial={
                "company_name": company.name,
                "country": "México",
                "country_code": "MX",
                "is_active": True,
            },
        )

    return render(
        request,
        "shipping_hub/origin_form.html",
        {
            "company": company,
            "form": form,
            "editing": False,
            "origin": None,
        },
    )


@shipping_hub_manager_required
def origin_edit(request, origin_id):
    company = _get_request_company_or_403(request)

    origin = get_object_or_404(
        ShippingOriginAddress.objects.for_company(company).select_related("warehouse"),
        pk=origin_id,
    )

    if request.method == "POST":
        form = ShippingOriginAddressForm(
            request.POST,
            instance=origin,
            company=company,
        )

        if form.is_valid():
            try:
                with transaction.atomic():
                    updated_origin = form.save(commit=False)

                    updated_origin.company = company
                    updated_origin.full_clean()
                    updated_origin.save()

                messages.success(
                    request,
                    "Dirección de origen actualizada.",
                )

                return redirect("shipping_hub:origins_list")

            except ValidationError as exc:
                form.add_error(
                    None,
                    exc,
                )

            except IntegrityError:
                form.add_error(
                    "warehouse",
                    ("Esta bodega ya tiene otra dirección " "logística configurada."),
                )
    else:
        form = ShippingOriginAddressForm(
            instance=origin,
            company=company,
        )

    return render(
        request,
        "shipping_hub/origin_form.html",
        {
            "company": company,
            "form": form,
            "editing": True,
            "origin": origin,
        },
    )


@shipping_hub_manager_required
def shipping_cubication(
    request,
    shipping_id,
):
    company = _get_request_company_or_403(request)

    # =========================================================
    # PREPARAR ENVÃO, ORIGEN, DESTINO E INTEGRACIÓN
    # =========================================================

    try:
        cubication = prepare_cubication(
            company=company,
            shipping_id=shipping_id,
            user=request.user,
            require_integration=False,
        )
        _authorize_shipping_warehouse(
            request, cubication, required_level=ACCESS_OPERATE
        )

    except ValidationError as error:
        error_messages = getattr(
            error,
            "messages",
            [str(error)],
        )

        return render(
            request,
            "shipping_hub/cubication.html",
            {
                "fatal_error": " ".join(error_messages),
            },
            status=400,
        )

    carrier_shipment = cubication.carrier_shipment

    current_purchase = (
        ShippingPurchase.objects.for_company(company)
        .filter(carrier_shipment=carrier_shipment)
        .order_by("-generation", "-pk")
        .first()
    )

    purchase_ui = build_purchase_ui(
        purchase=current_purchase,
        carrier_shipment=carrier_shipment,
    )

    # Siempre filtrado por empresa.
    package_queryset = (
        ShipmentPackage.objects.for_company(company)
        .filter(
            carrier_shipment=carrier_shipment,
        )
        .order_by(
            "package_number",
        )
    )

    # =========================================================
    # GUARDAR CUBICACIÓN
    # =========================================================

    if request.method == "POST":
        formset = CubicationPackageFormSet(
            request.POST,
            prefix="packages",
        )

        if formset.is_valid():
            package_rows = []

            for package_form in formset:
                cleaned_data = package_form.cleaned_data or {}

                if not cleaned_data:
                    continue

                if cleaned_data.get("DELETE"):
                    continue

                package_rows.append(cleaned_data)

            if not package_rows:
                messages.error(
                    request,
                    ("Debes registrar al menos " "un bulto."),
                )

            else:
                try:
                    with transaction.atomic():
                        locked_shipment = (
                            CarrierShipment.objects.select_for_update().get(
                                id=carrier_shipment.id,
                                company=company,
                                status=(CarrierShipment.STATUS_DRAFT),
                            )
                        )

                        # Reemplazamos únicamente los
                        # paquetes de esta empresa y guía.
                        ShipmentPackage.objects.filter(
                            company=company,
                            carrier_shipment=(locked_shipment),
                        ).delete()

                        for (
                            package_number,
                            row,
                        ) in enumerate(
                            package_rows,
                            start=1,
                        ):
                            package = ShipmentPackage(
                                company=company,
                                carrier_shipment=(locked_shipment),
                                package_number=(package_number),
                                description=(row.get("description") or "Mercancía"),
                                weight_kg=row["weight_kg"],
                                length_cm=row["length_cm"],
                                width_cm=row["width_cm"],
                                height_cm=row["height_cm"],
                                status=(ShipmentPackage.STATUS_DRAFT),
                            )

                            package.full_clean()
                            package.save()

                        request_data = dict(locked_shipment.request_data or {})

                        request_data.update(
                            {
                                "origin": (cubication.origin_data),
                                "destination": (cubication.destination_data),
                                "package_count": len(package_rows),
                                "cubication_saved": True,
                            }
                        )

                        locked_shipment.request_data = request_data

                        locked_shipment.save(
                            update_fields=[
                                "request_data",
                                "updated_at",
                            ]
                        )

                except ValidationError as error:
                    error_messages = getattr(
                        error,
                        "messages",
                        [str(error)],
                    )

                    for error_message in error_messages:
                        messages.error(
                            request,
                            error_message,
                        )

                except CarrierShipment.DoesNotExist:
                    messages.error(
                        request,
                        (
                            "El borrador del envío ya "
                            "no está disponible para "
                            "modificar su cubicación."
                        ),
                    )

                else:
                    messages.success(
                        request,
                        ("Cubicación guardada: " f"{len(package_rows)} " "bulto(s)."),
                    )

                    return redirect(
                        ("shipping_hub:" "shipping_cubication"),
                        shipping_id=shipping_id,
                    )

        else:
            messages.error(
                request,
                ("Revisa el peso y las " "dimensiones de los bultos."),
            )

            for error_message in formset.non_form_errors():
                messages.error(
                    request,
                    error_message,
                )

    # =========================================================
    # MOSTRAR CUBICACIÓN GUARDADA
    # =========================================================

    else:
        initial_packages = [
            {
                "description": (package.description),
                "weight_kg": (package.weight_kg),
                "length_cm": (package.length_cm),
                "width_cm": (package.width_cm),
                "height_cm": (package.height_cm),
            }
            for package in package_queryset
        ]

        # Cuando todavía no hay bultos,
        # mostramos exactamente uno vacío.
        if not initial_packages:
            initial_packages = [
                {
                    "description": "Mercancía",
                }
            ]

        formset = CubicationPackageFormSet(
            initial=initial_packages,
            prefix="packages",
        )

    # =========================================================
    # RESUMEN DE CUBICACIÓN
    # =========================================================

    packages = list(package_queryset)

    total_weight = sum(
        (package.weight_kg for package in packages),
        Decimal("0"),
    )

    total_volume_cm3 = sum(
        (
            package.length_cm * package.width_cm * package.height_cm
            for package in packages
        ),
        Decimal("0"),
    )

    total_volume_m3 = total_volume_cm3 / Decimal("1000000")

    # =========================================================
    # COTIZACIONES GUARDADAS
    # =========================================================

    quotes = (
        ShippingQuote.objects.for_company(company)
        .filter(
            order=(cubication.shipping_order.order),
            shipping_order=(cubication.shipping_order),
            purchases__isnull=True,
        )
        .order_by(
            "amount",
            "id",
        )
    )

    # =========================================================
    # TARIFA SELECCIONADA
    # =========================================================

    selected_quote = carrier_shipment.selected_quote
    selected_integration = (
        selected_quote.integration
        if selected_quote and selected_quote.integration_id
        else cubication.integration
    )

    selected_quote_data = (selected_quote.response_data if selected_quote else {}) or {}

    quote_requires_branch = bool(
        selected_quote_data.get("dropOff") and selected_quote_data.get("branches")
    )

    selected_branch_code = str(
        (carrier_shipment.request_data or {}).get(
            "selected_branch_code",
            "",
        )
    ).strip()

    # La cuenta maestra de WMS360 solo cotiza. La compra automática
    # está disponible exclusivamente con credenciales propias de la
    # empresa cliente (BYOK).
    uses_company_api = bool(
        selected_integration
        and selected_integration.account_mode
        == CompanyShippingIntegration.ACCOUNT_MODE_COMPANY
    )
    byok_labels_enabled = getattr(
        settings,
        "SHIPPING_HUB_BYOK_LABELS_ENABLED",
        True,
    )

    can_generate_label = bool(
        selected_quote
        and packages
        and carrier_shipment.status == CarrierShipment.STATUS_DRAFT
        and uses_company_api
        and byok_labels_enabled
        and (not quote_requires_branch or bool(selected_branch_code))
    )

    purchase_confirmation_token = ""
    if can_generate_label:
        purchase_confirmation_token = build_purchase_confirmation(
            company=company,
            shipment=carrier_shipment,
            quote=selected_quote,
            integration=selected_integration,
        )

    label_generation_block_reason = ""

    if not selected_quote:
        label_generation_block_reason = "Selecciona una tarifa."

    elif not packages:
        label_generation_block_reason = "Primero guarda la cubicación."

    elif quote_requires_branch and not selected_branch_code:
        label_generation_block_reason = (
            "La tarifa Ocurre requiere seleccionar "
            "una sucursal. Para esta primera guía, "
            "selecciona Estafeta Terrestre de $200."
        )

    elif not uses_company_api:
        label_generation_block_reason = (
            "Cotización informativa. Compra la guía fuera de WMS360 "
            "y registra el tracking, o conecta la API propia de tu empresa."
        )

    elif not byok_labels_enabled:
        label_generation_block_reason = (
            "La compra mediante API propia está desactivada temporalmente."
        )

    elif carrier_shipment.status != (CarrierShipment.STATUS_DRAFT):
        label_generation_block_reason = "La guía ya salió del estado borrador."

    primary_package = packages[0] if packages else None

    # SH-8.8: el envío solo puede continuar al Centro de Despacho
    # cuando la compra terminó, el shipment está label_ready y
    # existen tracking y etiqueta imprimible.
    dispatch_tracking = str(
        carrier_shipment.master_tracking_number or ""
    ).strip()
    dispatch_label_url = str(
        getattr(primary_package, "label_url", "") or ""
    ).strip()
    dispatch_ready = bool(
        current_purchase
        and current_purchase.status
        == ShippingPurchase.STATUS_COMPLETED
        and carrier_shipment.status
        == CarrierShipment.STATUS_LABEL_READY
        and dispatch_tracking
        and dispatch_label_url
    )
    dispatch_center_url = (
        reverse("shipping_hub_dispatch:dispatch_center")
        + f"?tab=ready&highlight={carrier_shipment.pk}"
    )

    # =========================================================
    # RENDER
    # =========================================================

    return render(
        request,
        "shipping_hub/cubication.html",
        {
            "cubication": cubication,
            "shipping_order": (cubication.shipping_order),
            "carrier_shipment": (carrier_shipment),
            "current_purchase": current_purchase,
            "purchase_ui": purchase_ui,
            "show_manual_guide_resolution": bool(
                current_purchase
                and current_purchase.provider == "skydropx"
                and current_purchase.status
                in {
                    ShippingPurchase.STATUS_PROVIDER_PENDING,
                    ShippingPurchase.STATUS_RECONCILING,
                    ShippingPurchase.STATUS_MANUAL_REVIEW,
                }
                and current_purchase.charge_status
                in {
                    ShippingPurchase.CHARGE_POSSIBLE,
                    ShippingPurchase.CHARGE_CONFIRMED,
                }
            ),
            "manual_resolution_confirmation": (
                f"RECUPERAR {current_purchase.pk}"
                if current_purchase
                else ""
            ),
            "origin": (cubication.origin_data),
            "destination": (cubication.destination_data),
            "formset": formset,
            "packages": packages,
            "package_count": len(packages),
            "total_weight": (total_weight),
            "total_volume_m3": (total_volume_m3),
            "quotes": quotes,
            "can_quote": bool(packages),
            # Tarifa seleccionada.
            "selected_quote": (selected_quote),
            # Etiqueta principal para un envío
            # de un solo bulto.
            "primary_package": (primary_package),
            "dispatch_ready": dispatch_ready,
            "dispatch_center_url": dispatch_center_url,
            "dispatch_tracking": dispatch_tracking,
            "dispatch_label_url": dispatch_label_url,
            # Control del botón Generar guía.
            "can_generate_label": (can_generate_label),
            "label_generation_block_reason": (label_generation_block_reason),
            "uses_company_api": (uses_company_api),
            "byok_labels_enabled": (byok_labels_enabled),
            "purchase_confirmation_token": purchase_confirmation_token,
            # Servicios Ocurre.
            "quote_requires_branch": (quote_requires_branch),
            "selected_branch_code": (selected_branch_code),
            "origin_data": cubication.origin_data,
            "destination_data": cubication.destination_data,
            "shipping_integration": cubication.integration,
        },
    )


@shipping_hub_manager_required
@require_POST
def shipping_quote_rates(
    request,
    shipping_id,
):
    company = _get_request_company_or_403(request)

    created_quotes = []
    selection_preserved = False
    had_previous_selection = False
    provider_name = "El proveedor"

    def clean_comparison_text(value):
        return str(value or "").strip().lower()

    def rate_matches_quote(
        quote,
        rate,
    ):
        """
        Determina si la tarifa seleccionada anteriormente
        continúa existiendo en la respuesta nueva.

        Se exige que coincidan:
        - transportista;
        - servicio;
        - importe;
        - moneda;
        - modalidad ocurre/domicilio.

        Si el precio cambió, no se conserva automáticamente.
        """

        quote_carrier = clean_comparison_text(quote.carrier_code or quote.carrier_name)

        rate_carrier = clean_comparison_text(
            rate.get("carrier_code") or rate.get("carrier_name")
        )

        quote_service = clean_comparison_text(quote.service_code or quote.service_name)

        rate_service = clean_comparison_text(
            rate.get("service_code") or rate.get("service_name")
        )

        quote_currency = clean_comparison_text(quote.currency)

        rate_currency = clean_comparison_text(rate.get("currency"))

        try:
            rate_amount = Decimal(str(rate.get("amount")))

        except (
            TypeError,
            ValueError,
            ArithmeticError,
        ):
            return False

        quote_response = quote.response_data or {}

        rate_response = rate.get("response_data") or {}

        quote_is_drop_off = bool(quote_response.get("dropOff"))

        rate_is_drop_off = bool(rate_response.get("dropOff"))

        return all(
            (
                quote.provider == rate.get("provider"),
                quote_carrier == rate_carrier,
                quote_service == rate_service,
                quote.amount == rate_amount,
                quote_currency == rate_currency,
                quote_is_drop_off == rate_is_drop_off,
            )
        )

    try:
        cubication = prepare_cubication(
            company=company,
            shipping_id=shipping_id,
            user=request.user,
            require_integration=True,
        )
        _authorize_shipping_warehouse(
            request, cubication, required_level=ACCESS_OPERATE
        )

        order = cubication.shipping_order.order

        if order.company_id != company.id:
            raise ValidationError(
                "El pedido y la orden de envío pertenecen a empresas diferentes."
            )

        provider_name = "Los agregadores activos"

        packages = list(
            ShipmentPackage.objects.for_company(company)
            .filter(
                carrier_shipment=(cubication.carrier_shipment),
                status=(ShipmentPackage.STATUS_DRAFT),
            )
            .order_by(
                "package_number",
            )
        )

        if not packages:
            raise ValidationError(("Primero guarda la cubicación " "del envío."))

        result = quote_all_providers(
            company=company,
            origin=cubication.origin_data,
            destination=(cubication.destination_data),
            packages=packages,
            order_total=order.total,
            warehouse=cubication.warehouse,
        )

        if not isinstance(
            result,
            dict,
        ):
            raise ValidationError(
                ("El proveedor devolvió una " "respuesta de cotización inválida.")
            )

        rates = result.get("rates")

        if (
            not isinstance(
                rates,
                list,
            )
            or not rates
        ):
            raise ValidationError(("El proveedor no devolvió " "tarifas disponibles."))

        with transaction.atomic():
            locked_order = order.__class__.objects.select_for_update().get(
                id=order.id,
                company=company,
            )

            locked_shipment = CarrierShipment.objects.select_for_update().get(
                id=(cubication.carrier_shipment.id),
                company=company,
                shipping_order=(cubication.shipping_order),
                status=(CarrierShipment.STATUS_DRAFT),
            )

            # Bloqueamos todas las cotizaciones de este envío,
            # no solamente las del proveedor actual.
            #
            # Esto impide que sobrevivan tarifas antiguas
            # cuando se cambia de integración logística.
            existing_quotes = list(
                ShippingQuote.objects.select_for_update()
                .filter(
                    company=company,
                    order=locked_order,
                    shipping_order=(cubication.shipping_order),
                )
                .order_by(
                    "id",
                )
            )

            previous_selected_quote = None

            if locked_shipment.selected_quote_id:
                previous_selected_quote = next(
                    (
                        quote
                        for quote in existing_quotes
                        if quote.id == locked_shipment.selected_quote_id
                    ),
                    None,
                )

            if previous_selected_quote is None:
                previous_selected_quote = next(
                    (quote for quote in existing_quotes if quote.is_selected),
                    None,
                )

            had_previous_selection = bool(previous_selected_quote)

            # La selección del envío puede cambiar al crear el nuevo lote,
            # pero las compras históricas conservan para siempre la tarifa
            # exacta utilizada. ShippingPurchase.selected_quote usa PROTECT
            # precisamente para impedir pérdida de trazabilidad financiera.
            locked_shipment.selected_quote = None

            existing_quote_ids = [
                quote.pk
                for quote in existing_quotes
            ]

            protected_quote_ids = set(
                ShippingPurchase.objects.for_company(company)
                .filter(
                    selected_quote_id__in=existing_quote_ids,
                )
                .values_list(
                    "selected_quote_id",
                    flat=True,
                )
            )

            # Una tarifa histórica ya no debe seguir marcada como la
            # selección vigente. Esto libera la restricción única del
            # pedido para el nuevo lote de cotizaciones.
            if existing_quote_ids:
                ShippingQuote.objects.filter(
                    company=company,
                    order=locked_order,
                    shipping_order=(cubication.shipping_order),
                    pk__in=existing_quote_ids,
                ).update(
                    is_selected=False,
                )

            # Solo eliminamos cotizaciones huérfanas. Las referenciadas
            # por ShippingPurchase permanecen para auditoría, conciliación,
            # reintentos y evidencia del importe cotizado.
            removable_quotes = ShippingQuote.objects.filter(
                company=company,
                order=locked_order,
                shipping_order=(cubication.shipping_order),
            )

            if protected_quote_ids:
                removable_quotes = removable_quotes.exclude(
                    pk__in=protected_quote_ids,
                )

            removable_quotes.delete()

            preserved_quote = None

            for rate in rates:
                preserve_this_rate = bool(
                    previous_selected_quote
                    and preserved_quote is None
                    and rate_matches_quote(
                        previous_selected_quote,
                        rate,
                    )
                )

                quote = ShippingQuote(
                    company=company,
                    order=locked_order,
                    shipping_order=(cubication.shipping_order),
                    provider=rate.get("provider"),
                    integration_id=rate.get("integration_id"),
                    external_rate_id=(
                        rate.get(
                            "external_rate_id",
                            "",
                        )
                        or ""
                    ),
                    carrier_code=(
                        rate.get(
                            "carrier_code",
                            "",
                        )
                        or ""
                    ),
                    carrier_name=(
                        rate.get(
                            "carrier_name",
                            "",
                        )
                        or ""
                    ),
                    service_code=(
                        rate.get(
                            "service_code",
                            "",
                        )
                        or ""
                    ),
                    service_name=(
                        rate.get(
                            "service_name",
                            "",
                        )
                        or ""
                    ),
                    amount=rate.get("amount"),
                    currency=(
                        rate.get(
                            "currency",
                            "MXN",
                        )
                        or "MXN"
                    ),
                    estimated_delivery_days=(rate.get("estimated_delivery_days")),
                    estimated_delivery_text=(
                        rate.get(
                            "estimated_delivery_text",
                            "",
                        )
                        or ""
                    ),
                    response_data=(rate.get("response_data") or {}),
                    is_selected=(preserve_this_rate),
                    # Skydropx PRO documenta una vigencia de 24 horas para
                    # los rates. Aplicamos el mismo límite a todo el lote para
                    # impedir compras con una cotización antigua.
                    expires_at=timezone.now() + timedelta(hours=24),
                )

                quote.full_clean()
                quote.save()

                created_quotes.append(quote)

                if preserve_this_rate:
                    preserved_quote = quote
                    selection_preserved = True

            request_data = dict(locked_shipment.request_data or {})

            request_data["quote_requests"] = (
                result.get(
                    "request_payloads",
                    [],
                )
                or []
            )

            request_data["quote_carriers"] = (
                result.get(
                    "carriers_attempted",
                    [],
                )
                or []
            )

            if not selection_preserved:
                # Una selección eliminada tampoco puede conservar
                # datos dependientes de esa selección.
                request_data.pop(
                    "selected_branch_code",
                    None,
                )

                request_data.pop(
                    "selected_branch_name",
                    None,
                )

            response_data = dict(locked_shipment.response_data or {})

            response_data["quote_errors"] = (
                result.get(
                    "errors",
                    [],
                )
                or []
            )

            response_data["quote_count"] = len(created_quotes)

            response_data["selection_preserved"] = selection_preserved

            response_data["previous_selection_removed"] = bool(
                had_previous_selection and not selection_preserved
            )

            locked_shipment.provider = (
                preserved_quote.provider if preserved_quote else cubication.integration.provider
            )

            locked_shipment.selected_quote = preserved_quote

            if preserved_quote:
                locked_shipment.carrier_code = preserved_quote.carrier_code

                locked_shipment.carrier_name = preserved_quote.carrier_name

                locked_shipment.service_code = preserved_quote.service_code

                locked_shipment.service_name = preserved_quote.service_name

                locked_shipment.total_cost = preserved_quote.amount

                locked_shipment.currency = preserved_quote.currency

            else:
                locked_shipment.carrier_code = ""
                locked_shipment.carrier_name = ""
                locked_shipment.service_code = ""
                locked_shipment.service_name = ""
                locked_shipment.total_cost = 0
                locked_shipment.currency = "MXN"

            # Volver a cotizar tampoco altera el total del pedido.
            # La tarifa sigue siendo informativa hasta que exista una
            # operación logística realmente adquirida por la empresa.

            locked_shipment.request_data = request_data

            locked_shipment.response_data = response_data

            locked_shipment.last_error_type = ""
            locked_shipment.last_error_code = ""
            locked_shipment.last_error_message = ""

            locked_shipment.save(
                update_fields=[
                    "provider",
                    "selected_quote",
                    "carrier_code",
                    "carrier_name",
                    "service_code",
                    "service_name",
                    "total_cost",
                    "currency",
                    "request_data",
                    "response_data",
                    "last_error_type",
                    "last_error_code",
                    "last_error_message",
                    "updated_at",
                ]
            )

    except CarrierShipment.DoesNotExist:
        messages.error(
            request,
            ("El borrador del envío ya no " "está disponible."),
        )

    except ShippingProviderError as error:
        messages.error(
            request,
            str(error),
        )

    except ValidationError as error:
        error_message = " ".join(error.messages)

        messages.error(
            request,
            error_message,
        )

    else:
        success_message = (
            f"{provider_name} devolvió " f"{len(created_quotes)} tarifa(s)."
        )

        if had_previous_selection and selection_preserved:
            success_message += (
                " La tarifa seleccionada continúa " "disponible y fue conservada."
            )

        elif had_previous_selection and not selection_preserved:
            success_message += (
                " La tarifa seleccionada anteriormente "
                "ya no está disponible o cambió de precio; "
                "selecciona una nueva."
            )

        messages.success(
            request,
            success_message,
        )

    return redirect(
        "shipping_hub:shipping_cubication",
        shipping_id=shipping_id,
    )


@shipping_hub_manager_required
@require_POST
def shipping_quote_select(
    request,
    shipping_id,
    quote_id,
):
    """
    Selecciona una tarifa existente mediante el servicio central seguro.

    No compra la guía. Solo congela y firma la cotización para que el
    worker durable pueda validarla posteriormente.
    """
    company = _get_request_company_or_403(request)

    try:
        cubication = prepare_cubication(
            company=company,
            shipping_id=shipping_id,
            user=request.user,
            require_integration=True,
        )
        _authorize_shipping_warehouse(
            request,
            cubication,
            required_level=ACCESS_OPERATE,
        )

        if cubication.order.company_id != company.id:
            raise ValidationError(
                "El pedido y la orden de envío pertenecen "
                "a empresas diferentes."
            )

        quote_is_current = (
            ShippingQuote.objects.for_company(company)
            .filter(
                pk=quote_id,
                order=cubication.order,
                shipping_order=cubication.shipping_order,
                purchases__isnull=True,
            )
            .exists()
        )

        if not quote_is_current:
            raise ValidationError(
                "La cotización ya no pertenece al lote vigente. "
                "Vuelve a cotizar y selecciona una tarifa actual."
            )

        quote = freeze_existing_quote(
            company=company,
            cubication=cubication,
            quote_id=quote_id,
            actor=request.user,
        )

    except ValidationError as error:
        messages.error(
            request,
            " ".join(getattr(error, "messages", [str(error)])),
        )
    except ShippingQuote.DoesNotExist:
        messages.error(
            request,
            "La cotización solicitada no existe "
            "o no pertenece a esta empresa.",
        )
    except CarrierShipment.DoesNotExist:
        messages.error(
            request,
            "El borrador del envío no existe "
            "o ya no está disponible.",
        )
    else:
        messages.success(
            request,
            (
                f"Tarifa seleccionada y protegida: "
                f"{quote.carrier_name} — "
                f"{quote.service_name} — "
                f"${quote.amount} {quote.currency}."
            ),
        )

    return redirect(
        "shipping_hub:shipping_cubication",
        shipping_id=shipping_id,
    )


@shipping_hub_manager_required
@require_GET
def shipping_purchase_live_status(request, shipping_id):
    """
    Estado seguro y company-scoped para actualizar la compra desde el navegador.

    No procesa compras, no llama proveedores y no modifica estados.
    """
    company = _get_request_company_or_403(request)

    try:
        cubication = prepare_cubication(
            company=company,
            shipping_id=shipping_id,
            user=request.user,
            require_integration=False,
        )
        _authorize_shipping_warehouse(
            request,
            cubication,
            required_level=ACCESS_OPERATE,
        )
    except ValidationError as error:
        return JsonResponse(
            {
                "ok": False,
                "message": " ".join(
                    getattr(error, "messages", [str(error)])
                ),
            },
            status=400,
        )

    carrier_shipment = cubication.carrier_shipment
    purchase = (
        ShippingPurchase.objects.for_company(company)
        .filter(carrier_shipment=carrier_shipment)
        .order_by("-generation", "-pk")
        .first()
    )
    purchase_ui = build_purchase_ui(
        purchase=purchase,
        carrier_shipment=carrier_shipment,
    )

    heartbeat_path = (
        Path(settings.BASE_DIR)
        / "runtime"
        / "shipping_worker_heartbeat.json"
    )
    worker_online = False
    worker_age_seconds = None

    try:
        heartbeat = json.loads(
            heartbeat_path.read_text(encoding="utf-8")
        )
        heartbeat_at = timezone.datetime.fromisoformat(
            heartbeat["updated_at"]
        )
        if timezone.is_naive(heartbeat_at):
            heartbeat_at = timezone.make_aware(heartbeat_at)
        worker_age_seconds = max(
            0,
            int((timezone.now() - heartbeat_at).total_seconds()),
        )
        worker_online = worker_age_seconds <= 10
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        json.JSONDecodeError,
    ):
        worker_online = False

    package = (
        ShipmentPackage.objects.for_company(company)
        .filter(carrier_shipment=carrier_shipment)
        .order_by("package_number", "pk")
        .first()
    )

    tracking_number = str(
        carrier_shipment.master_tracking_number or ""
    ).strip()
    printable_label_url = str(
        getattr(package, "label_url", "") or ""
    ).strip()
    dispatch_ready = bool(
        purchase
        and purchase.status
        == ShippingPurchase.STATUS_COMPLETED
        and carrier_shipment.status
        == CarrierShipment.STATUS_LABEL_READY
        and tracking_number
        and printable_label_url
    )
    dispatch_center_url = (
        reverse("shipping_hub_dispatch:dispatch_center")
        + f"?tab=ready&highlight={carrier_shipment.pk}"
    )

    # SH-9.1: reconciling es un estado ACTIVO. El worker sigue
    # consultando la misma compra; el navegador debe continuar
    # haciendo polling hasta completed, failed_final o manual_review.
    terminal_states = {
        ShippingPurchase.STATUS_COMPLETED,
        ShippingPurchase.STATUS_FAILED_FINAL,
        ShippingPurchase.STATUS_MANUAL_REVIEW,
    }
    status_value = getattr(purchase, "status", "")
    terminal = bool(purchase and status_value in terminal_states)

    return JsonResponse(
        {
            "ok": True,
            "purchase_id": getattr(purchase, "pk", None),
            "status": status_value,
            "charge_status": getattr(
                purchase,
                "charge_status",
                "",
            ),
            "title": purchase_ui["title"],
            "description": purchase_ui["description"],
            "severity": purchase_ui["severity"],
            "blocks_repurchase": purchase_ui[
                "blocks_repurchase"
            ],
            "allow_retry": purchase_ui["allow_retry"],
            "terminal": terminal,
            "tracking": (
                carrier_shipment.master_tracking_number or ""
            ),
            "label_url": printable_label_url,
            "dispatch_ready": dispatch_ready,
            "dispatch_url": (
                dispatch_center_url if dispatch_ready else ""
            ),
            "carrier_shipment_status": (
                carrier_shipment.status or ""
            ),
            "worker_online": worker_online,
            "worker_age_seconds": worker_age_seconds,
            "updated_at": timezone.now().isoformat(),
        }
    )


@shipping_hub_manager_required
@require_POST
def shipping_purchase_manual_resolution(request, shipping_id):
    company = _get_request_company_or_403(request)

    try:
        cubication = prepare_cubication(
            company=company,
            shipping_id=shipping_id,
            user=request.user,
            require_integration=True,
        )
        _authorize_shipping_warehouse(
            request,
            cubication,
            required_level=ACCESS_APPROVE,
        )

        purchase_id = int(
            str(request.POST.get("purchase_id") or "").strip()
        )
        provider_shipment_id = str(
            request.POST.get("provider_shipment_id") or ""
        ).strip()
        confirmation = str(
            request.POST.get("resolution_confirmation") or ""
        ).strip()

        expected_confirmation = f"RECUPERAR {purchase_id}"
        if confirmation != expected_confirmation:
            raise ValidationError(
                "La confirmación de seguridad no coincide."
            )

        result = resolve_skydropx_purchase_by_exact_id(
            company=company,
            carrier_shipment=cubication.carrier_shipment,
            purchase_id=purchase_id,
            provider_shipment_id=provider_shipment_id,
            actor=request.user,
        )

        if result.completed:
            messages.success(
                request,
                (
                    "Guía recuperada correctamente. "
                    f"Tracking: {result.tracking_number}"
                ),
            )
        else:
            messages.warning(request, result.message)

    except (TypeError, ValueError, ValidationError) as error:
        messages.error(
            request,
            " ".join(getattr(error, "messages", [str(error)])),
        )
    except Exception as error:
        sentry_sdk.capture_exception(error)

        messages.error(
            request,
            (
                "No fue posible consultar la guía en SkydropX. "
                "No se realizó otra compra. "
                f"Diagnóstico: {type(error).__name__}: {error}"
            ),
        )

    return redirect(
        "shipping_hub:shipping_cubication",
        shipping_id=shipping_id,
    )


@shipping_hub_manager_required
@require_POST
def shipping_generate_label(request, shipping_id):
    """Registra la compra y responde sin esperar al proveedor."""
    company = _get_request_company_or_403(request)
    redirect_name = "shipping_hub:shipping_cubication"

    try:
        cubication = prepare_cubication(
            company=company,
            shipping_id=shipping_id,
            user=request.user,
            require_integration=True,
        )
        _authorize_shipping_warehouse(request, cubication, required_level=ACCESS_APPROVE)

        purchase_integration = cubication.integration
        selected_quote = cubication.carrier_shipment.selected_quote
        if selected_quote and selected_quote.integration_id:
            purchase_integration = selected_quote.integration

        if not getattr(settings, "SHIPPING_HUB_BYOK_LABELS_ENABLED", False):
            raise ValidationError("La generación automática de guías no está habilitada.")

        from shipping_hub.services.purchase_engine import request_shipping_purchase

        purchase, created = request_shipping_purchase(
            company=company,
            user=request.user,
            cubication=cubication,
            integration=purchase_integration,
            confirmation_token=request.POST.get("purchase_confirmation"),
        )
        if purchase is None:
            messages.info(request, "Esta guía ya fue generada. No se realizó otro cargo.")
        elif created:
            messages.info(
                request,
                (
                    f"Solicitud de compra #{purchase.pk} registrada. "
                    "La guía todavía no ha sido comprada. "
                    "El worker procesará la solicitud fuera del navegador."
                ),
            )
        else:
            messages.info(
                request,
                f"La compra #{purchase.pk} ya está en proceso. No se realizó otra solicitud ni otro cargo.",
            )
    except ValidationError as error:
        messages.error(request, " ".join(getattr(error, "messages", [str(error)])))
    except CarrierShipment.DoesNotExist:
        messages.error(request, "El borrador no existe o pertenece a otra empresa.")

    return redirect(redirect_name, shipping_id=shipping_id)


# ============================================================
# REGISTRAR GUÃA EXTERNA
# ============================================================


@shipping_hub_manager_required
def shipping_external_label(
    request,
    shipping_id,
):
    company = _get_request_company_or_403(request)

    try:
        cubication = prepare_cubication(
            company=company,
            shipping_id=shipping_id,
            user=request.user,
            require_integration=False,
            default_source=CarrierShipment.SOURCE_EXTERNAL,
        )
        _authorize_shipping_warehouse(
            request, cubication, required_level=ACCESS_APPROVE
        )
    except ValidationError as error:
        messages.error(
            request,
            " ".join(
                getattr(
                    error,
                    "messages",
                    [str(error)],
                )
            ),
        )
        return redirect("warehouse:lista_shipping")

    carrier_shipment = cubication.carrier_shipment
    shipping_order = cubication.shipping_order

    if request.method == "POST":
        form = ExternalShippingLabelForm(
            request.POST,
            request.FILES,
        )

        if form.is_valid():
            saved_storage_name = ""

            try:
                with transaction.atomic():
                    locked_shipment = (
                        CarrierShipment.objects.select_for_update()
                        .select_related(
                            "shipping_order",
                            "selected_quote",
                        )
                        .get(
                            pk=carrier_shipment.pk,
                            company=company,
                            shipping_order=shipping_order,
                        )
                    )

                    if locked_shipment.shipping_order.company_id != company.id:
                        raise ValidationError(
                            "La orden de envío pertenece a otra empresa."
                        )

                    locked_shipping_order = (
                        shipping_order.__class__.objects.select_for_update().get(
                            pk=shipping_order.pk,
                            company=company,
                        )
                    )

                    if locked_shipment.shipping_order_id != locked_shipping_order.id:
                        raise ValidationError(
                            "La guía logística no pertenece a esta orden de envío."
                        )

                    if locked_shipment.status not in {
                        CarrierShipment.STATUS_DRAFT,
                        CarrierShipment.STATUS_FAILED,
                    }:
                        raise ValidationError("Este envío ya tiene una guía activa.")

                    duplicate_tracking = (
                        CarrierShipment.objects.filter(
                            company=company,
                            master_tracking_number=(
                                form.cleaned_data["tracking_number"]
                            ),
                        )
                        .exclude(pk=locked_shipment.pk)
                        .exists()
                    )

                    if duplicate_tracking:
                        raise ValidationError(
                            "Este número de guía ya está registrado " "en la empresa."
                        )

                    label_file = form.cleaned_data.get("label_file")

                    if label_file:
                        safe_filename = get_valid_filename(label_file.name)

                        storage_path = (
                            f"shipping_hub/company_{company.pk}/"
                            f"external_labels/"
                            f"{uuid.uuid4().hex}_{safe_filename}"
                        )

                        saved_storage_name = default_storage.save(
                            storage_path,
                            label_file,
                        )

                    request_data = dict(locked_shipment.request_data or {})

                    request_data.update(
                        {
                            "shipping_source": "external",
                            "external_registered_by_id": (request.user.pk),
                            "external_registered_at": (timezone.now().isoformat()),
                        }
                    )

                    if saved_storage_name:
                        request_data["external_label_storage_name"] = saved_storage_name
                        request_data["external_label_original_name"] = label_file.name

                    locked_shipment.selected_quote = None
                    locked_shipment.source = CarrierShipment.SOURCE_EXTERNAL
                    locked_shipment.provider = "manual"
                    locked_shipment.carrier_code = "manual"
                    locked_shipment.carrier_name = form.cleaned_data["carrier_name"]
                    locked_shipment.service_code = "external"
                    locked_shipment.service_name = "Guía externa"
                    locked_shipment.master_tracking_number = form.cleaned_data[
                        "tracking_number"
                    ]
                    locked_shipment.master_tracking_url = (
                        form.cleaned_data.get("tracking_url") or ""
                    )
                    locked_shipment.total_cost = form.cleaned_data.get(
                        "total_cost"
                    ) or Decimal("0")
                    locked_shipment.currency = form.cleaned_data["currency"]
                    transition_shipment(
                        entity_id=locked_shipment.pk,
                        company_id=company.id,
                        to_state=CarrierShipment.STATUS_LABEL_READY,
                        source=SOURCE_USER_ACTION,
                        reason="Etiqueta externa registrada.",
                        actor=request.user,
                        metadata={"operation": "register_external_label"},
                    )
                    locked_shipment.generated_at = timezone.now()
                    locked_shipment.external_registered_at = timezone.now()
                    locked_shipment.request_data = request_data
                    locked_shipment.last_error_type = ""
                    locked_shipment.last_error_code = ""
                    locked_shipment.last_error_message = ""

                    locked_shipment.full_clean()
                    locked_shipment.save(
                        update_fields=[
                            "selected_quote",
                            "source",
                            "provider",
                            "carrier_code",
                            "carrier_name",
                            "service_code",
                            "service_name",
                            "master_tracking_number",
                            "master_tracking_url",
                            "total_cost",
                            "currency",
                            "status",
                            "generated_at",
                            "external_registered_at",
                            "request_data",
                            "last_error_type",
                            "last_error_code",
                            "last_error_message",
                            "updated_at",
                        ]
                    )

                    # Shipping Hub y el flujo operativo de Warehouse comparten
                    # una sola paquetería y una sola guía. La sincronización se
                    # guarda en la misma transacción para impedir estados
                    # parciales o datos de otra empresa.
                    locked_shipping_order.carrier = locked_shipment.carrier_name
                    locked_shipping_order.tracking_number = (
                        locked_shipment.master_tracking_number
                    )
                    locked_shipping_order.save(
                        update_fields=[
                            "carrier",
                            "tracking_number",
                            "updated_at",
                        ]
                    )

                    for package in ShipmentPackage.objects.select_for_update().filter(
                        company=company,
                        carrier_shipment=locked_shipment,
                    ):
                        transition_package(
                            entity_id=package.pk,
                            company_id=company.id,
                            to_state=ShipmentPackage.STATUS_LABEL_READY,
                            source=SOURCE_USER_ACTION,
                            reason="Etiqueta externa registrada.",
                            actor=request.user,
                            metadata={
                                "operation": "register_external_label",
                                "shipment_id": locked_shipment.pk,
                            },
                        )
                        package.tracking_number = (
                            locked_shipment.master_tracking_number
                        )
                        package.tracking_url = (
                            locked_shipment.master_tracking_url
                        )
                        package.label_format = ShipmentPackage.LABEL_FORMAT_PDF
                        package.last_error_message = ""
                        package.save(
                            update_fields=[
                                "tracking_number",
                                "tracking_url",
                                "label_format",
                                "last_error_message",
                                "updated_at",
                            ]
                        )

                    ShippingAuditEvent.objects.create(
                        company=company,
                        shipping_order=locked_shipping_order,
                        carrier_shipment=locked_shipment,
                        actor=request.user,
                        action="external_label_registered",
                        detail={
                            "carrier": locked_shipment.carrier_name,
                            "tracking_number": locked_shipment.master_tracking_number,
                            "package_count": locked_shipment.packages.count(),
                        },
                    )

            except (
                ValidationError,
                CarrierShipment.DoesNotExist,
            ) as error:
                if saved_storage_name:
                    default_storage.delete(saved_storage_name)

                messages.error(
                    request,
                    " ".join(
                        getattr(
                            error,
                            "messages",
                            [str(error)],
                        )
                    ),
                )
            else:
                messages.success(
                    request,
                    "Guía externa registrada correctamente.",
                )
                return redirect(
                    "warehouse:detalle_shipping",
                    shipping_id=shipping_id,
                )
    else:
        form = ExternalShippingLabelForm()

    return render(
        request,
        "shipping_hub/external_label_form.html",
        {
            "company": company,
            "shipping_order": shipping_order,
            "carrier_shipment": carrier_shipment,
            "form": form,
        },
    )


# ============================================================
# CONFIGURAR ENTREGA PROPIA / LOCAL
# ============================================================


@shipping_hub_manager_required
def shipping_local_delivery(
    request,
    shipping_id,
):
    company = _get_request_company_or_403(request)

    try:
        cubication = prepare_cubication(
            company=company,
            shipping_id=shipping_id,
            user=request.user,
            require_integration=False,
            default_source=CarrierShipment.SOURCE_LOCAL,
        )
        _authorize_shipping_warehouse(
            request, cubication, required_level=ACCESS_OPERATE
        )
    except ValidationError as error:
        messages.error(
            request,
            " ".join(
                getattr(
                    error,
                    "messages",
                    [str(error)],
                )
            ),
        )
        return redirect("warehouse:lista_shipping")

    carrier_shipment = cubication.carrier_shipment
    shipping_order = cubication.shipping_order

    if request.method == "POST":
        form = LocalDeliveryForm(request.POST, company=company)

        if form.is_valid():
            try:
                with transaction.atomic():
                    locked_shipment = (
                        CarrierShipment.objects.select_for_update()
                        .select_related("shipping_order")
                        .get(
                            pk=carrier_shipment.pk,
                            company=company,
                            shipping_order=shipping_order,
                        )
                    )

                    if locked_shipment.shipping_order.company_id != company.id:
                        raise ValidationError(
                            "La orden de envío pertenece a otra empresa."
                        )

                    if locked_shipment.status not in {
                        CarrierShipment.STATUS_DRAFT,
                        CarrierShipment.STATUS_FAILED,
                    }:
                        raise ValidationError(
                            "Este envío ya tiene una guía o método "
                            "de entrega activo."
                        )

                    request_data = dict(locked_shipment.request_data or {})

                    request_data.update(
                        {
                            "shipping_source": "local",
                            "local_delivery_notes": (
                                form.cleaned_data.get("notes") or ""
                            ),
                            "local_registered_by_id": request.user.pk,
                            "local_registered_at": (timezone.now().isoformat()),
                        }
                    )

                    locked_shipment.selected_quote = None
                    locked_shipment.source = CarrierShipment.SOURCE_LOCAL
                    locked_shipment.provider = "local"
                    locked_shipment.carrier_code = "local"
                    locked_shipment.carrier_name = form.cleaned_data["carrier_name"]
                    locked_shipment.service_code = "local_delivery"
                    locked_shipment.service_name = "Entrega propia / local"
                    locked_shipment.master_tracking_number = ""
                    locked_shipment.master_tracking_url = ""
                    locked_shipment.total_cost = Decimal("0")
                    locked_shipment.currency = "MXN"
                    transition_shipment(
                        entity_id=locked_shipment.pk,
                        company_id=company.id,
                        to_state=CarrierShipment.STATUS_READY,
                        source=SOURCE_USER_ACTION,
                        reason="Entrega local configurada.",
                        actor=request.user,
                        metadata={"operation": "configure_local_delivery"},
                    )
                    locked_shipment.generated_at = timezone.now()
                    locked_shipment.external_registered_at = timezone.now()
                    locked_shipment.request_data = request_data
                    locked_shipment.last_error_type = ""
                    locked_shipment.last_error_code = ""
                    locked_shipment.last_error_message = ""

                    locked_shipment.full_clean()
                    locked_shipment.save(
                        update_fields=[
                            "selected_quote",
                            "source",
                            "provider",
                            "carrier_code",
                            "carrier_name",
                            "service_code",
                            "service_name",
                            "master_tracking_number",
                            "master_tracking_url",
                            "total_cost",
                            "currency",
                            "status",
                            "generated_at",
                            "external_registered_at",
                            "request_data",
                            "last_error_type",
                            "last_error_code",
                            "last_error_message",
                            "updated_at",
                        ]
                    )

                    route, _ = LocalDeliveryRoute.objects.get_or_create(
                        company=company,
                        warehouse=cubication.warehouse,
                        scheduled_date=form.cleaned_data["scheduled_date"],
                        name=form.cleaned_data["route_name"],
                        defaults={
                            "driver": form.cleaned_data["driver"],
                            "vehicle": form.cleaned_data["vehicle"],
                            "notes": form.cleaned_data.get("notes") or "",
                            "created_by": request.user,
                        },
                    )
                    if (
                        route.driver_id != form.cleaned_data["driver"].id
                        or route.vehicle_id != form.cleaned_data["vehicle"].id
                    ):
                        raise ValidationError(
                            "Ya existe una ruta con ese nombre y fecha, pero tiene otro chofer o vehículo."
                        )
                    route.full_clean()
                    assignment, _ = LocalDeliveryAssignment.objects.update_or_create(
                        company=company,
                        carrier_shipment=locked_shipment,
                        defaults={
                            "route": route,
                            "stop_order": form.cleaned_data["stop_order"],
                            "status": LocalDeliveryAssignment.STATUS_READY,
                        },
                    )
                    assignment.full_clean()
                    assignment.save()

                    for package in locked_shipment.packages.select_for_update().all():
                        transition_package(
                            entity_id=package.pk,
                            company_id=company.id,
                            to_state=ShipmentPackage.STATUS_LABEL_READY,
                            source=SOURCE_USER_ACTION,
                            reason="Bulto preparado para entrega local.",
                            actor=request.user,
                            metadata={
                                "operation": "configure_local_delivery",
                                "shipment_id": locked_shipment.pk,
                            },
                        )

                    ShippingAuditEvent.objects.create(
                        company=company,
                        shipping_order=shipping_order,
                        carrier_shipment=locked_shipment,
                        actor=request.user,
                        action="local_delivery_configured",
                        detail={
                            "carrier_name": locked_shipment.carrier_name,
                            "package_count": locked_shipment.packages.count(),
                            "route_id": route.id,
                            "driver_id": route.driver_id,
                            "vehicle_id": route.vehicle_id,
                            "scheduled_date": route.scheduled_date.isoformat(),
                        },
                    )

            except (
                ValidationError,
                CarrierShipment.DoesNotExist,
            ) as error:
                messages.error(
                    request,
                    " ".join(
                        getattr(
                            error,
                            "messages",
                            [str(error)],
                        )
                    ),
                )
            else:
                messages.success(
                    request,
                    "Entrega propia configurada correctamente.",
                )
                return redirect(
                    "shipping_hub:shipping_cubication",
                    shipping_id=shipping_id,
                )
    else:
        form = LocalDeliveryForm(company=company)

    return render(
        request,
        "shipping_hub/local_delivery_form.html",
        {
            "company": company,
            "shipping_order": shipping_order,
            "carrier_shipment": carrier_shipment,
            "form": form,
        },
    )


@shipping_hub_manager_required
def local_delivery_resources(request):
    """Alta segura de choferes y vehículos de la empresa actual."""
    company = _get_request_company_or_403(request)
    driver_form = LocalDeliveryDriverForm(prefix="driver")
    vehicle_form = LocalDeliveryVehicleForm(prefix="vehicle")
    if request.method == "POST":
        resource = request.POST.get("resource")
        if resource == "driver":
            driver_form = LocalDeliveryDriverForm(request.POST, prefix="driver")
            if driver_form.is_valid():
                driver = driver_form.save(commit=False)
                driver.company = company
                driver.full_clean()
                driver.save()
                messages.success(request, "Chofer registrado correctamente.")
                return redirect("shipping_hub:local_delivery_resources")
        elif resource == "vehicle":
            vehicle_form = LocalDeliveryVehicleForm(request.POST, prefix="vehicle")
            if vehicle_form.is_valid():
                vehicle = vehicle_form.save(commit=False)
                vehicle.company = company
                vehicle.full_clean()
                vehicle.save()
                messages.success(request, "Vehículo registrado correctamente.")
                return redirect("shipping_hub:local_delivery_resources")
    return render(request, "shipping_hub/local_delivery_resources.html", {
        "driver_form": driver_form,
        "vehicle_form": vehicle_form,
        "drivers": LocalDeliveryDriver.objects.filter(company=company),
        "vehicles": LocalDeliveryVehicle.objects.filter(company=company),
    })


@shipping_hub_manager_required
def local_delivery_label(request, shipping_id, package_id):
    company = _get_request_company_or_403(request)
    try:
        cubication = prepare_cubication(
            company=company, shipping_id=shipping_id, user=request.user,
            require_integration=False, default_source=CarrierShipment.SOURCE_LOCAL,
        )
    except ValidationError as error:
        # Envío ajeno o inexistente: 404, nunca un 500.
        raise Http404(" ".join(getattr(error, "messages", [str(error)]))) from error
    _authorize_shipping_warehouse(request, cubication, required_level=ACCESS_OPERATE)
    shipment = cubication.carrier_shipment
    if shipment.source != CarrierShipment.SOURCE_LOCAL:
        raise Http404("El envío no utiliza reparto propio.")
    package = get_object_or_404(
        ShipmentPackage, pk=package_id, company=company, carrier_shipment=shipment
    )
    assignment = get_object_or_404(
        LocalDeliveryAssignment.objects.select_related(
            "route", "route__driver", "route__vehicle"
        ), company=company, carrier_shipment=shipment,
    )
    qr_value = f"WMS360:LOCAL:{company.pk}:{shipping_id}:{package.pk}"
    qr = qrcode.make(qr_value)
    buffer = BytesIO()
    qr.save(buffer, format="PNG")
    qr_data = base64.b64encode(buffer.getvalue()).decode("ascii")
    if request.GET.get("printed") == "1":
        transition_package(
            entity_id=package.pk,
            company_id=company.id,
            to_state=ShipmentPackage.STATUS_PRINTED,
            source=SOURCE_USER_ACTION,
            reason="Etiqueta local impresa.",
            actor=request.user,
            metadata={"operation": "print_local_delivery_label"},
        )
        package.printed_at = timezone.now()
        package.save(update_fields=["printed_at", "updated_at"])
        ShippingAuditEvent.objects.create(
            company=company, shipping_order=cubication.shipping_order,
            carrier_shipment=shipment, actor=request.user,
            action="local_label_printed", detail={"package_id": package.id},
        )
    return render(request, "shipping_hub/local_delivery_label.html", {
        "shipping_order": cubication.shipping_order,
        "shipment": shipment, "package": package, "assignment": assignment,
        "package_total": shipment.packages.count(), "qr_data": qr_data,
        "barcode_value": f"WMS{company.pk:04d}{shipping_id:07d}{package.package_number:03d}",
    })


@require_POST
@shipping_hub_manager_required
def local_delivery_transition(request, shipping_id):
    company = _get_request_company_or_403(request)
    try:
        cubication = prepare_cubication(
            company=company, shipping_id=shipping_id, user=request.user,
            require_integration=False, default_source=CarrierShipment.SOURCE_LOCAL,
        )
    except ValidationError as error:
        # Envío ajeno o inexistente: 404, nunca un 500.
        raise Http404(" ".join(getattr(error, "messages", [str(error)]))) from error
    _authorize_shipping_warehouse(request, cubication, required_level=ACCESS_OPERATE)
    assignment = get_object_or_404(
        LocalDeliveryAssignment, company=company,
        carrier_shipment=cubication.carrier_shipment,
    )
    action = request.POST.get("action")
    now = timezone.now()
    mapping = {
        "load": (LocalDeliveryAssignment.STATUS_LOADED, CarrierShipment.STATUS_READY, ShipmentPackage.STATUS_PRINTED),
        "depart": (LocalDeliveryAssignment.STATUS_IN_TRANSIT, CarrierShipment.STATUS_IN_TRANSIT, ShipmentPackage.STATUS_IN_TRANSIT),
        "failed": (LocalDeliveryAssignment.STATUS_FAILED_ATTEMPT, CarrierShipment.STATUS_EXCEPTION, ShipmentPackage.STATUS_EXCEPTION),
        "deliver": (LocalDeliveryAssignment.STATUS_DELIVERED, CarrierShipment.STATUS_DELIVERED, ShipmentPackage.STATUS_DELIVERED),
    }
    if action not in mapping:
        messages.error(request, "Transición de reparto no válida.")
        return redirect("shipping_hub:shipping_cubication", shipping_id=shipping_id)
    allowed = {
        LocalDeliveryAssignment.STATUS_READY: {"load"},
        LocalDeliveryAssignment.STATUS_LOADED: {"depart"},
        LocalDeliveryAssignment.STATUS_IN_TRANSIT: {"failed", "deliver"},
        LocalDeliveryAssignment.STATUS_FAILED_ATTEMPT: {"depart"},
        LocalDeliveryAssignment.STATUS_DELIVERED: set(),
    }
    if action not in allowed.get(assignment.status, set()):
        messages.error(request, "La transición no corresponde al estado actual del reparto.")
        return redirect("shipping_hub:shipping_cubication", shipping_id=shipping_id)
    if action == "load":
        scanned = set(cubication.carrier_shipment.request_data.get("local_scanned_package_ids", []))
        expected = set(cubication.carrier_shipment.packages.values_list("id", flat=True))
        if scanned != expected:
            messages.error(request, "Debes escanear todos los bultos antes de marcar la carga.")
            return redirect("shipping_hub:shipping_cubication", shipping_id=shipping_id)
    assignment.status, shipment_status, package_status = mapping[action]
    if action == "load":
        assignment.loaded_at = now
    elif action == "depart":
        assignment.departed_at = now
    elif action == "failed":
        assignment.failed_attempts += 1
        assignment.last_failure_reason = (request.POST.get("reason") or "").strip()[:300]
    else:
        assignment.delivered_at = now
    assignment.save()
    shipment = cubication.carrier_shipment
    transition_shipment(
        entity_id=shipment.pk,
        company_id=company.id,
        to_state=shipment_status,
        source=SOURCE_USER_ACTION,
        reason=f"Entrega local actualizada: {action}.",
        actor=request.user,
        metadata={
            "operation": "local_delivery_action",
            "assignment_id": assignment.pk,
        },
    )
    for package in shipment.packages.select_for_update().all():
        transition_package(
            entity_id=package.pk,
            company_id=company.id,
            to_state=package_status,
            source=SOURCE_USER_ACTION,
            reason=f"Entrega local actualizada: {action}.",
            actor=request.user,
            metadata={
                "operation": "local_delivery_action",
                "shipment_id": shipment.pk,
            },
        )
    ShippingAuditEvent.objects.create(
        company=company, shipping_order=cubication.shipping_order,
        carrier_shipment=shipment, actor=request.user,
        action=f"local_delivery_{action}", detail={"assignment_id": assignment.id},
    )
    messages.success(request, "Estado de reparto actualizado correctamente.")
    return redirect("shipping_hub:shipping_cubication", shipping_id=shipping_id)


@require_POST
@shipping_hub_manager_required
def local_delivery_scan(request, shipping_id):
    company = _get_request_company_or_403(request)
    try:
        cubication = prepare_cubication(
            company=company, shipping_id=shipping_id, user=request.user,
            require_integration=False, default_source=CarrierShipment.SOURCE_LOCAL,
        )
    except ValidationError as error:
        # Envío ajeno o inexistente: 404, nunca un 500.
        raise Http404(" ".join(getattr(error, "messages", [str(error)]))) from error
    _authorize_shipping_warehouse(request, cubication, required_level=ACCESS_OPERATE)
    token = (request.POST.get("scan_code") or "").strip()
    prefix = f"WMS360:LOCAL:{company.pk}:{shipping_id}:"
    if not token.startswith(prefix):
        messages.error(request, "El código escaneado no pertenece a este envío.")
        return redirect("shipping_hub:shipping_cubication", shipping_id=shipping_id)
    try:
        package_id = int(token[len(prefix):])
    except (TypeError, ValueError):
        messages.error(request, "El código del bulto no es válido.")
        return redirect("shipping_hub:shipping_cubication", shipping_id=shipping_id)
    package = get_object_or_404(
        ShipmentPackage, pk=package_id, company=company,
        carrier_shipment=cubication.carrier_shipment,
    )
    shipment = cubication.carrier_shipment
    data = dict(shipment.request_data or {})
    scanned = {int(value) for value in data.get("local_scanned_package_ids", [])}
    scanned.add(package.id)
    data["local_scanned_package_ids"] = sorted(scanned)
    shipment.request_data = data
    shipment.save(update_fields=["request_data", "updated_at"])
    ShippingAuditEvent.objects.create(
        company=company, shipping_order=cubication.shipping_order,
        carrier_shipment=shipment, actor=request.user,
        action="local_package_scanned", detail={"package_id": package.id},
    )
    messages.success(request, f"Bulto {package.package_number} escaneado correctamente.")
    return redirect("shipping_hub:shipping_cubication", shipping_id=shipping_id)


# ============================================================
# DESCARGAR PDF DE GUÃA EXTERNA
# ============================================================


def _validated_shipping_asset_storage_name(
    storage_name,
    expected_prefix,
):
    """
    Valida que un asset privado permanezca dentro del prefijo
    exacto asignado a la empresa/bulto.
    """
    raw_name = str(storage_name or "").strip()

    if not raw_name:
        return ""

    if "\\" in raw_name:
        raise PermissionDenied(
            "La ruta privada del archivo no es válida."
        )

    normalized = str(PurePosixPath(raw_name))
    path = PurePosixPath(normalized)

    if (
        path.is_absolute()
        or ".." in path.parts
        or normalized != raw_name
    ):
        raise PermissionDenied(
            "La ruta privada del archivo no es válida."
        )

    prefix = str(PurePosixPath(expected_prefix)).rstrip("/") + "/"

    if not normalized.startswith(prefix):
        raise PermissionDenied(
            "El archivo solicitado no pertenece al recurso autorizado."
        )

    return normalized


def shipping_external_label_download(
    request,
    shipping_id,
):
    from accounts.activity_route_authorization import (
        authorize_activity_route,
    )

    company = _get_request_company_or_403(request)

    carrier_shipment = get_object_or_404(
        CarrierShipment.objects.select_related(
            "shipping_order",
            "shipping_order__packing_order",
            "shipping_order__packing_order__picking_order",
            "shipping_order__packing_order__picking_order__warehouse",
        ),
        company=company,
        shipping_order_id=shipping_id,
        source=CarrierShipment.SOURCE_EXTERNAL,
    )

    shipment_warehouse = (
        carrier_shipment.shipping_order
        .packing_order
        .picking_order
        .warehouse
    )

    authorize_activity_route(
        request,
        "shipping_hub",
        "shipping_external_label_download",
        warehouse=shipment_warehouse,
    )

    request_data = carrier_shipment.request_data or {}

    storage_name = request_data.get(
        "external_label_storage_name"
    )

    if not storage_name:
        raise Http404(
            "Este envío no tiene un PDF registrado."
        )

    expected_prefix = (
        f"shipping_hub/company_{company.pk}/"
        "external_labels/"
    )

    storage_name = _validated_shipping_asset_storage_name(
        storage_name,
        expected_prefix,
    )

    if not default_storage.exists(storage_name):
        raise Http404(
            "El archivo de la guía ya no está disponible."
        )

    original_name = request_data.get(
        "external_label_original_name",
        "guia_externa.pdf",
    )

    return FileResponse(
        default_storage.open(
            storage_name,
            "rb",
        ),
        as_attachment=True,
        filename=get_valid_filename(original_name),
        content_type="application/pdf",
    )


@shipping_hub_manager_required
def custom_carriers(request):
    company = _get_request_company_or_403(request)
    form = CustomCarrierForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        carrier = form.save(commit=False)
        carrier.company = company
        carrier.full_clean()
        carrier.save()
        messages.success(request, "Transportista personalizado registrado.")
        return redirect("shipping_hub:custom_carriers")
    return render(request, "shipping_hub/custom_carriers.html", {
        "form": form,
        "carriers": CustomCarrier.objects.for_company(company),
    })


def _external_package_or_404(company, shipping_id, package_id):
    return get_object_or_404(
        ShipmentPackage.objects.select_related("carrier_shipment", "carrier_shipment__shipping_order"),
        pk=package_id,
        company=company,
        carrier_shipment__shipping_order_id=shipping_id,
    )


@shipping_hub_manager_required
def external_package_label(request, shipping_id, package_id):
    company = _get_request_company_or_403(request)
    try:
        cubication = prepare_cubication(
            company=company, shipping_id=shipping_id, user=request.user,
            require_integration=False, default_source=CarrierShipment.SOURCE_EXTERNAL,
        )
    except ValidationError as error:
        # Envío ya enviado/entregado/cancelado o ajeno: aviso, nunca un 500.
        return _cubication_blocked_redirect(request, shipping_id, error)
    _authorize_shipping_warehouse(request, cubication, required_level=ACCESS_APPROVE)
    package = _external_package_or_404(company, shipping_id, package_id)
    form = PackageExternalLabelForm(request.POST or None, request.FILES or None, company=company)
    if request.method == "POST" and form.is_valid():
        uploaded = form.cleaned_data["label_file"]
        new_name = ""
        old_name = package.label_file.name if package.label_file else ""
        try:
            with transaction.atomic():
                locked = ShipmentPackage.objects.select_for_update().select_related(
                    "carrier_shipment", "carrier_shipment__shipping_order"
                ).get(pk=package.pk, company=company, carrier_shipment__shipping_order_id=shipping_id)
                tracking = form.cleaned_data["tracking_number"]
                if ShipmentPackage.objects.filter(company=company, tracking_number__iexact=tracking).exclude(pk=locked.pk).exists():
                    raise ValidationError("Este rastreo ya pertenece a otro bulto de la empresa.")
                if CarrierShipment.objects.filter(company=company, master_tracking_number__iexact=tracking).exclude(pk=locked.carrier_shipment_id).exists():
                    raise ValidationError("Este rastreo ya pertenece a otro envío de la empresa.")
                safe_name = get_valid_filename(uploaded.name)
                storage_path = f"shipping_hub/company_{company.pk}/external_packages/package_{locked.pk}/{uuid.uuid4().hex}_{safe_name}"
                new_name = default_storage.save(storage_path, uploaded)
                locked.tracking_number = tracking
                locked.tracking_url = form.cleaned_data.get("tracking_url") or ""
                locked.label_format = uploaded.wms_label_format
                locked.label_file.name = new_name
                locked.label_zpl = ""
                locked.external_label_original_name = uploaded.name
                locked.external_label_content_type = uploaded.wms_content_type
                locked.external_registered_at = timezone.now()
                locked.external_registered_by = request.user
                transition_package(
                    entity_id=locked.pk,
                    company_id=company.id,
                    to_state=ShipmentPackage.STATUS_LABEL_READY,
                    source=SOURCE_USER_ACTION,
                    reason="Etiqueta externa de bulto registrada.",
                    actor=request.user,
                    metadata={"operation": "register_package_external_label"},
                )
                locked.last_error_message = ""
                locked.full_clean(exclude=["status"])
                locked.save(
                    update_fields=[
                        "tracking_number",
                        "tracking_url",
                        "label_format",
                        "label_file",
                        "label_zpl",
                        "external_label_original_name",
                        "external_label_content_type",
                        "external_registered_at",
                        "external_registered_by",
                        "last_error_message",
                        "updated_at",
                    ]
                )

                shipment = CarrierShipment.objects.select_for_update().get(pk=locked.carrier_shipment_id, company=company)
                shipment.source = CarrierShipment.SOURCE_CUSTOM if form.cleaned_data.get("custom_carrier") else CarrierShipment.SOURCE_EXTERNAL
                shipment.provider = "custom" if shipment.source == CarrierShipment.SOURCE_CUSTOM else "manual"
                shipment.carrier_code = shipment.provider
                shipment.carrier_name = form.cleaned_data["carrier_name"]
                shipment.service_name = form.cleaned_data.get("service_name") or "Guía externa"
                if not shipment.master_tracking_number:
                    shipment.master_tracking_number = tracking
                    shipment.master_tracking_url = locked.tracking_url
                remaining = shipment.packages.exclude(status__in=[ShipmentPackage.STATUS_LABEL_READY, ShipmentPackage.STATUS_PRINTED]).exists()
                transition_shipment(
                    entity_id=shipment.pk,
                    company_id=company.id,
                    to_state=(
                        CarrierShipment.STATUS_DRAFT
                        if remaining
                        else CarrierShipment.STATUS_LABEL_READY
                    ),
                    source=SOURCE_USER_ACTION,
                    reason="Estado recalculado tras registrar etiqueta de bulto.",
                    actor=request.user,
                    metadata={
                        "operation": "register_package_external_label",
                        "package_id": locked.pk,
                    },
                )
                shipment.external_registered_at = timezone.now()
                shipment.save(
                    update_fields=[
                        "source",
                        "provider",
                        "carrier_code",
                        "carrier_name",
                        "master_tracking_number",
                        "master_tracking_url",
                        "external_registered_at",
                        "updated_at",
                    ]
                )
                shipping_order = shipment.shipping_order
                shipping_order.carrier = shipment.carrier_name
                shipping_order.tracking_number = shipment.master_tracking_number
                shipping_order.save(update_fields=["carrier", "tracking_number", "updated_at"])
                ShippingAuditEvent.objects.create(
                    company=company, shipping_order=shipment.shipping_order,
                    carrier_shipment=shipment, actor=request.user,
                    action="external_package_label_registered",
                    detail={"package_id": locked.pk, "package_number": locked.package_number,
                            "tracking_number": tracking, "format": locked.label_format,
                            "replaced": bool(old_name)},
                )
        except (ValidationError, ShipmentPackage.DoesNotExist) as error:
            if new_name:
                default_storage.delete(new_name)
            messages.error(request, " ".join(getattr(error, "messages", [str(error)])))
        else:
            if old_name and old_name != new_name:
                default_storage.delete(old_name)
            messages.success(request, f"Guía del bulto {package.package_number} registrada.")
            return redirect("shipping_hub:shipping_cubication", shipping_id=shipping_id)
    return render(request, "shipping_hub/external_package_label.html", {
        "form": form, "package": package, "shipping_order": cubication.shipping_order,
    })


@shipping_hub_manager_required
def external_package_label_file(
    request,
    shipping_id,
    package_id,
):
    from accounts.activity_route_authorization import (
        authorize_activity_route,
    )

    company = _get_request_company_or_403(request)

    package = _external_package_or_404(
        company,
        shipping_id,
        package_id,
    )

    package_warehouse = (
        package.carrier_shipment
        .shipping_order
        .packing_order
        .picking_order
        .warehouse
    )

    authorize_activity_route(
        request,
        "shipping_hub",
        "external_package_label_file",
        warehouse=package_warehouse,
    )

    if (
        not package.label_file
        or not package.label_file.name
    ):
        raise Http404(
            "El bulto no tiene una guía externa."
        )

    expected_prefix = (
        f"shipping_hub/company_{company.pk}/"
        f"external_packages/package_{package.pk}/"
    )

    storage_name = _validated_shipping_asset_storage_name(
        package.label_file.name,
        expected_prefix,
    )

    if not default_storage.exists(storage_name):
        raise Http404(
            "La guía ya no está disponible."
        )

    filename = get_valid_filename(
        package.external_label_original_name
        or (
            f"guia_bulto_{package.package_number}."
            f"{package.label_format}"
        )
    )

    return FileResponse(
        default_storage.open(
            storage_name,
            "rb",
        ),
        as_attachment=(
            request.GET.get("print") != "1"
        ),
        filename=filename,
        content_type=(
            package.external_label_content_type
            or "application/octet-stream"
        ),
    )


def _cubication_blocked_redirect(request, shipping_id, error):
    """La Mesa de Cubicación rechazó el envío: se explica y se regresa."""
    from warehouse.models import ShippingOrder

    company = _get_request_company_or_403(request)
    if not ShippingOrder.objects.filter(pk=shipping_id, company=company).exists():
        raise Http404("El envío no existe o no pertenece a tu empresa.")
    messages.error(request, " ".join(getattr(error, "messages", [str(error)])))
    return redirect("warehouse:detalle_shipping", shipping_id=shipping_id)


@shipping_hub_manager_required
@require_POST
def external_package_label_print(request, shipping_id, package_id):
    company = _get_request_company_or_403(request)
    try:
        cubication = prepare_cubication(company=company, shipping_id=shipping_id, user=request.user, require_integration=False)
    except ValidationError as error:
        return _cubication_blocked_redirect(request, shipping_id, error)
    _authorize_shipping_warehouse(request, cubication, required_level=ACCESS_OPERATE)
    package = _external_package_or_404(company, shipping_id, package_id)
    if not package.label_file:
        raise Http404("El bulto no tiene guía para imprimir.")
    ShipmentPackage.objects.filter(pk=package.pk, company=company).update(
        print_count=F("print_count") + 1,
        printed_at=timezone.now(),
    )
    ShippingAuditEvent.objects.create(
        company=company, shipping_order=cubication.shipping_order,
        carrier_shipment=package.carrier_shipment, actor=request.user,
        action="external_package_label_printed",
        detail={"package_id": package.pk, "package_number": package.package_number},
    )
    package.refresh_from_db(fields=["label_file", "label_format", "external_label_original_name", "external_label_content_type"])
    expected = f"shipping_hub/company_{company.pk}/external_packages/package_{package.pk}/"
    if not package.label_file.name.startswith(expected):
        raise PermissionDenied("El archivo no pertenece a esta empresa y bulto.")
    return FileResponse(
        default_storage.open(package.label_file.name, "rb"),
        as_attachment=package.label_format == ShipmentPackage.LABEL_FORMAT_ZPL,
        filename=get_valid_filename(package.external_label_original_name or f"guia_bulto_{package.package_number}.{package.label_format}"),
        content_type=package.external_label_content_type or "application/octet-stream",
    )


@shipping_hub_manager_required
@require_POST
def external_package_tracking_event(request, shipping_id, package_id):
    company = _get_request_company_or_403(request)
    try:
        cubication = prepare_cubication(company=company, shipping_id=shipping_id, user=request.user, require_integration=False)
    except ValidationError as error:
        return _cubication_blocked_redirect(request, shipping_id, error)
    _authorize_shipping_warehouse(request, cubication, required_level=ACCESS_OPERATE)
    package = _external_package_or_404(company, shipping_id, package_id)
    form = ManualTrackingEventForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Revisa el estado y la descripción del seguimiento.")
    else:
        status = form.cleaned_data["status"]
        with transaction.atomic():
            transition_package(
                entity_id=package.pk,
                company_id=company.id,
                to_state=status,
                source=SOURCE_USER_ACTION,
                reason="Evento de tracking manual registrado.",
                actor=request.user,
                metadata={"operation": "manual_tracking_event"},
            )
            ShipmentTrackingEvent.objects.create(
                company=company, carrier_shipment=package.carrier_shipment, package=package,
                event_code=f"manual_{status}", event_name=dict(ManualTrackingEventForm.base_fields["status"].choices)[status],
                description=form.cleaned_data["description"], location=form.cleaned_data.get("location") or "",
                occurred_at=timezone.now(), raw_data={"manual": True, "actor_id": request.user.pk},
            )
            shipment = package.carrier_shipment
            target_shipment_status = None
            if (
                status == ShipmentPackage.STATUS_DELIVERED
                and not shipment.packages.exclude(
                    status=ShipmentPackage.STATUS_DELIVERED
                ).exists()
            ):
                target_shipment_status = CarrierShipment.STATUS_DELIVERED
            elif status in {
                ShipmentPackage.STATUS_IN_TRANSIT,
                ShipmentPackage.STATUS_OUT_FOR_DELIVERY,
                ShipmentPackage.STATUS_EXCEPTION,
            }:
                target_shipment_status = status

            if target_shipment_status:
                transition_shipment(
                    entity_id=shipment.pk,
                    company_id=company.id,
                    to_state=target_shipment_status,
                    source=SOURCE_USER_ACTION,
                    reason="Estado del envío sincronizado con tracking manual.",
                    actor=request.user,
                    metadata={
                        "operation": "manual_tracking_event",
                        "package_id": package.pk,
                    },
                )
            ShippingAuditEvent.objects.create(
                company=company, shipping_order=cubication.shipping_order,
                carrier_shipment=shipment, actor=request.user, action="manual_tracking_event",
                detail={"package_id": package.pk, "status": status},
            )
        messages.success(request, "Seguimiento manual actualizado.")
    return redirect("shipping_hub:shipping_cubication", shipping_id=shipping_id)


def _printable_packages(company, warehouse):
    """Cola aislada por empresa y por la bodega real del envío."""
    return (
        ShipmentPackage.objects.for_company(company)
        .select_related(
            "carrier_shipment",
            "carrier_shipment__shipping_order",
            "carrier_shipment__shipping_order__order",
            "carrier_shipment__shipping_order__packing_order__picking_order__warehouse",
        )
        .filter(
            carrier_shipment__shipping_order__packing_order__picking_order__warehouse=warehouse,
            status__in=PRINTABLE_PACKAGE_STATUSES,
        )
        .order_by(
            "carrier_shipment__shipping_order_id", "package_number", "id"
        )
    )


def _package_has_printable_label(package):
    shipment = package.carrier_shipment
    if shipment.source == CarrierShipment.SOURCE_LOCAL:
        return hasattr(shipment, "local_assignment")
    return bool(package.label_file or package.label_zpl or package.label_url)


@shipping_hub_manager_required
def print_center(request):
    company = _get_request_company_or_403(request)
    warehouse = require_active_warehouse(request, required_level=ACCESS_VIEW)
    if warehouse.company_id != company.id or not user_can_access_warehouse(
        request.user, warehouse, required_level=ACCESS_VIEW
    ):
        raise PermissionDenied("No tienes acceso a esta bodega.")

    packages = _printable_packages(company, warehouse)
    source = str(request.GET.get("source") or "").strip()
    status = str(request.GET.get("status") or "").strip()
    query = str(request.GET.get("q") or "").strip()
    if source in dict(CarrierShipment.SOURCE_CHOICES):
        packages = packages.filter(carrier_shipment__source=source)
    if status in dict(ShipmentPackage.STATUS_CHOICES):
        packages = packages.filter(status=status)
    if query:
        packages = packages.filter(
            Q(tracking_number__icontains=query)
            | Q(carrier_shipment__master_tracking_number__icontains=query)
            | Q(carrier_shipment__shipping_order__order__folio__icontains=query)
        )

    package_list = [p for p in packages[:500] if _package_has_printable_label(p)]
    history = (
        ShippingPrintJob.objects.for_company(company)
        .filter(warehouse_id_snapshot=warehouse.pk)
        .select_related("requested_by")[:25]
    )
    return render(
        request,
        "shipping_hub/print_center.html",
        {
            "packages": package_list,
            "history": history,
            "warehouse": warehouse,
            "source_choices": CarrierShipment.SOURCE_CHOICES,
            "status_choices": ShipmentPackage.STATUS_CHOICES,
            "selected_source": source,
            "selected_status": status,
            "query": query,
        },
    )


@shipping_hub_manager_required
@require_POST
def print_job_create(request):
    company = _get_request_company_or_403(request)
    warehouse = require_active_warehouse(request, required_level=ACCESS_OPERATE)
    if warehouse.company_id != company.id or not user_can_access_warehouse(
        request.user, warehouse, required_level=ACCESS_OPERATE
    ):
        raise PermissionDenied("No tienes permiso para imprimir en esta bodega.")

    raw_ids = request.POST.getlist("package_ids")
    try:
        package_ids = list(dict.fromkeys(int(value) for value in raw_ids))
    except (TypeError, ValueError):
        raise PermissionDenied("La selección de bultos no es válida.")
    if not package_ids or len(package_ids) > 200:
        messages.error(request, "Selecciona entre 1 y 200 etiquetas.")
        return redirect("shipping_hub:print_center")

    reason = str(request.POST.get("reason") or "").strip()
    with transaction.atomic():
        packages = list(
            _printable_packages(company, warehouse)
            .select_for_update()
            .filter(pk__in=package_ids)
        )
        if len(packages) != len(package_ids):
            raise PermissionDenied(
                "Uno o más bultos no pertenecen a esta empresa/bodega o no son imprimibles."
            )
        packages_by_id = {package.pk: package for package in packages}
        packages = [packages_by_id[pk] for pk in package_ids]
        if any(not _package_has_printable_label(package) for package in packages):
            raise ValidationError("Uno o más bultos no tienen etiqueta disponible.")
        is_reprint = any(package.print_count > 0 for package in packages)
        if is_reprint and len(reason) < 5:
            messages.error(request, "Indica un motivo de al menos 5 caracteres para reimprimir.")
            return redirect("shipping_hub:print_center")

        job = ShippingPrintJob.objects.create(
            company=company,
            requested_by=request.user,
            mode=(
                ShippingPrintJob.MODE_SINGLE
                if len(packages) == 1
                else ShippingPrintJob.MODE_BATCH
            ),
            warehouse_id_snapshot=warehouse.pk,
            reason=reason,
            item_count=len(packages),
        )
        now = timezone.now()
        affected_shipments = {}
        for sequence, package in enumerate(packages, 1):
            ShippingPrintItem.objects.create(
                company=company,
                job=job,
                package=package,
                carrier_shipment=package.carrier_shipment,
                sequence=sequence,
                label_format_snapshot=package.label_format,
                source_snapshot=package.carrier_shipment.source,
                was_reprint=package.print_count > 0,
                previous_print_count=package.print_count,
            )
            if package.status == ShipmentPackage.STATUS_LABEL_READY:
                transition_package(
                    entity_id=package.pk,
                    company_id=company.id,
                    to_state=ShipmentPackage.STATUS_PRINTED,
                    source=SOURCE_USER_ACTION,
                    reason="Etiqueta incluida en trabajo de impresión.",
                    actor=request.user,
                    metadata={
                        "operation": "create_print_job",
                        "print_job_id": job.pk,
                    },
                )
                transition_package(
                    entity_id=package.pk,
                    company_id=company.id,
                    to_state=ShipmentPackage.STATUS_PRINTED,
                    source=SOURCE_USER_ACTION,
                    reason="Etiqueta del bulto registrada como impresa.",
                    actor=request.user,
                    metadata={
                        "operation": "register_package_print",
                        },
                    )
                ShipmentPackage.objects.filter(
                    pk=package.pk,
                    company=company,
                    ).update(
                        print_count=F("print_count") + 1,
                        printed_at=timezone.now(),
                        )
            affected_shipments[package.carrier_shipment_id] = package.carrier_shipment
        for shipment in affected_shipments.values():
            ShippingAuditEvent.objects.create(
                company=company,
                shipping_order=shipment.shipping_order,
                carrier_shipment=shipment,
                actor=request.user,
                action="labels_print_job_created",
                detail={"job_id": job.pk, "item_count": len(packages), "reason": reason},
            )

    return redirect("shipping_hub:print_job_preview", job_id=job.pk)


def _get_authorized_print_job(request, job_id, required_level=ACCESS_VIEW):
    company = _get_request_company_or_403(request)
    warehouse = require_active_warehouse(request, required_level=required_level)
    job = get_object_or_404(
        ShippingPrintJob.objects.for_company(company),
        pk=job_id,
        warehouse_id_snapshot=warehouse.pk,
    )
    if warehouse.company_id != company.id or not user_can_access_warehouse(
        request.user, warehouse, required_level=required_level
    ):
        raise PermissionDenied("No tienes acceso a este trabajo de impresión.")
    return company, warehouse, job


@shipping_hub_manager_required
def print_job_preview(request, job_id):
    company, warehouse, job = _get_authorized_print_job(request, job_id)
    items = list(
        job.items.filter(company=company)
        .select_related("package", "carrier_shipment", "carrier_shipment__shipping_order")
        .order_by("sequence")
    )
    return render(
        request,
        "shipping_hub/print_job_preview.html",
        {"job": job, "items": items, "warehouse": warehouse},
    )


@xframe_options_exempt
@shipping_hub_manager_required
def print_item_asset(request, job_id, item_id):
    company, warehouse, job = _get_authorized_print_job(request, job_id)
    item = get_object_or_404(
        ShippingPrintItem.objects.select_related(
            "package", "carrier_shipment", "carrier_shipment__shipping_order"
        ),
        pk=item_id,
        job=job,
        company=company,
    )
    package = item.package
    shipment = item.carrier_shipment
    if shipment.shipping_order.packing_order.picking_order.warehouse_id != warehouse.pk:
        raise PermissionDenied("La etiqueta no pertenece a esta bodega.")
    if shipment.source == CarrierShipment.SOURCE_LOCAL:
        return redirect(
            "shipping_hub:local_delivery_label",
            shipping_id=shipment.shipping_order_id,
            package_id=package.pk,
        )
    if package.label_zpl:
        return HttpResponse(
            package.label_zpl,
            content_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'inline; filename="etiqueta_{package.pk}.zpl"'},
        )
    if package.label_file and package.label_file.name:
        if not default_storage.exists(package.label_file.name):
            raise Http404("La etiqueta ya no está disponible.")

        fallback_content_type = (
            "application/pdf"
            if str(package.label_format or "").lower() == "pdf"
            else "application/octet-stream"
            )
        return FileResponse(
            default_storage.open(package.label_file.name, "rb"),
            as_attachment=False,
            filename=get_valid_filename(
                package.external_label_original_name
                or f"etiqueta_{package.pk}.{package.label_format}"
            ),
            content_type=package.external_label_content_type or fallback_content_type,
        )
    if package.label_url:
        return redirect(package.label_url)
    raise Http404("La etiqueta no está disponible.")


@shipping_hub_manager_required
@require_POST
def print_job_complete(request, job_id):
    company, warehouse, job = _get_authorized_print_job(
        request, job_id, required_level=ACCESS_OPERATE
    )
    if job.status == ShippingPrintJob.STATUS_CREATED:
        job.status = ShippingPrintJob.STATUS_COMPLETED
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "completed_at"])
    messages.success(request, "Trabajo de impresión marcado como completado.")
    return redirect("shipping_hub:print_center")
