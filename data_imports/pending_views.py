from copy import deepcopy
from decimal import Decimal
from io import BytesIO

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from crm.models import Customer, CustomerAddress
from .models import ImportChangeLog, ImportJob, ImportRowResult
from .services.customer_import import (
    normalize_customer_row,
    normalize_email,
    normalize_phone,
    normalize_rfc,
)
from .views import get_company_job, require_company


INVALID_NAMES = {
    "", "-", "[vacío]", "[vacio]", "vacío", "vacio",
    "n/a", "na", "null", "none", "sin nombre",
}


class PendingCustomerForm(forms.Form):
    full_name = forms.CharField(
        label="Nombre completo / Razón social",
        max_length=200,
        widget=forms.TextInput(attrs={"class": "pending-input"}),
    )
    business_name = forms.CharField(
        label="Nombre comercial",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"class": "pending-input"}),
    )
    rfc = forms.CharField(
        label="RFC",
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={"class": "pending-input"}),
    )
    email = forms.EmailField(
        label="Correo",
        required=False,
        widget=forms.EmailInput(attrs={"class": "pending-input"}),
    )
    phone = forms.CharField(
        label="Teléfono",
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={"class": "pending-input"}),
    )
    whatsapp = forms.CharField(
        label="WhatsApp",
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={"class": "pending-input"}),
    )
    customer_type = forms.ChoiceField(
        label="Tipo de cliente",
        choices=[
            ("lead", "Lead"),
            ("prospect", "Prospecto"),
            ("customer", "Cliente"),
            ("vip", "Cliente VIP"),
            ("distributor", "Distribuidor"),
        ],
        widget=forms.Select(attrs={"class": "pending-input"}),
    )
    source = forms.ChoiceField(
        label="Origen",
        choices=[
            ("whatsapp", "WhatsApp"),
            ("facebook", "Facebook"),
            ("instagram", "Instagram"),
            ("tiktok", "TikTok"),
            ("website", "Sitio web"),
            ("referral", "Recomendación"),
            ("marketplace", "Marketplace"),
            ("manual", "Manual"),
        ],
        widget=forms.Select(attrs={"class": "pending-input"}),
    )
    estimated_monthly_purchase = forms.DecimalField(
        label="Compra mensual estimada",
        min_value=0,
        decimal_places=2,
        max_digits=14,
        required=False,
        widget=forms.NumberInput(
            attrs={"class": "pending-input", "step": "0.01", "min": "0"}
        ),
    )
    notes = forms.CharField(
        label="Notas",
        required=False,
        widget=forms.Textarea(attrs={"class": "pending-input", "rows": 4}),
    )
    is_active = forms.BooleanField(
        label="Cliente activo",
        required=False,
        initial=True,
    )
    address_type = forms.ChoiceField(
        label="Tipo de dirección",
        choices=[
            ("billing", "Facturación"),
            ("shipping", "Envío"),
            ("warehouse", "Bodega cliente"),
        ],
        required=False,
        widget=forms.Select(attrs={"class": "pending-input"}),
    )
    street = forms.CharField(
        label="Calle",
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={"class": "pending-input"}),
    )
    exterior_number = forms.CharField(
        label="Número exterior",
        max_length=40,
        required=False,
        widget=forms.TextInput(attrs={"class": "pending-input"}),
    )
    interior_number = forms.CharField(
        label="Número interior",
        max_length=40,
        required=False,
        widget=forms.TextInput(attrs={"class": "pending-input"}),
    )
    neighborhood = forms.CharField(
        label="Colonia",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "pending-input"}),
    )
    city = forms.CharField(
        label="Ciudad",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "pending-input"}),
    )
    municipality = forms.CharField(
        label="Municipio / Alcaldía",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "pending-input"}),
    )
    state = forms.CharField(
        label="Estado",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "pending-input"}),
    )
    country = forms.CharField(
        label="País",
        max_length=100,
        required=False,
        initial="México",
        widget=forms.TextInput(attrs={"class": "pending-input"}),
    )
    zip_code = forms.CharField(
        label="Código postal",
        max_length=20,
        required=False,
        widget=forms.TextInput(attrs={"class": "pending-input"}),
    )
    is_default = forms.BooleanField(
        label="Dirección principal",
        required=False,
        initial=True,
    )
    correction_note = forms.CharField(
        label="Nota de corrección",
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "pending-input",
                "rows": 3,
                "placeholder": "Ej. Se confirmó el teléfono correcto.",
            }
        ),
    )

    def clean_full_name(self):
        value = str(self.cleaned_data.get("full_name") or "").strip()
        if value.lower() in INVALID_NAMES:
            raise forms.ValidationError(
                "Escribe un nombre real o una razón social válida."
            )
        return value


def _has_field(model_class, field_name):
    return any(field.name == field_name for field in model_class._meta.fields)


def _set_existing_fields(instance, values):
    for field_name, value in values.items():
        if _has_field(instance.__class__, field_name):
            setattr(instance, field_name, value)


def _company_row(company, job, row_pk):
    return get_object_or_404(
        ImportRowResult.objects.for_company(company),
        pk=row_pk,
        job=job,
    )


def _find_existing(company, data):
    checks = [
        ("rfc", normalize_rfc(data.get("rfc"))),
        ("email", normalize_email(data.get("email"))),
        ("whatsapp", normalize_phone(data.get("whatsapp"))),
        ("phone", normalize_phone(data.get("phone"))),
    ]
    for customer in Customer.objects.filter(company=company):
        current = {
            "rfc": normalize_rfc(customer.rfc),
            "email": normalize_email(customer.email),
            "phone": normalize_phone(customer.phone),
            "whatsapp": normalize_phone(customer.whatsapp),
        }
        for field_name, value in checks:
            if value and current.get(field_name) == value:
                return customer, field_name
    return None, ""


def _initial_from_row(row):
    normalized = row.normalized_data or {}
    customer = normalized.get("customer") or {}
    address = normalized.get("address") or {}
    initial = {
        "full_name": customer.get("full_name"),
        "business_name": customer.get("business_name"),
        "rfc": customer.get("rfc"),
        "email": customer.get("email"),
        "phone": customer.get("phone"),
        "whatsapp": customer.get("whatsapp"),
        "customer_type": customer.get("customer_type") or "customer",
        "source": customer.get("source") or "manual",
        "estimated_monthly_purchase": customer.get(
            "estimated_monthly_purchase"
        ) or "0",
        "notes": customer.get("notes"),
        "is_active": customer.get("is_active", True),
        "address_type": address.get("address_type") or "shipping",
        "street": address.get("street"),
        "exterior_number": address.get("exterior_number"),
        "interior_number": address.get("interior_number"),
        "neighborhood": address.get("neighborhood"),
        "city": address.get("city"),
        "state": address.get("state"),
        "country": address.get("country") or "México",
        "zip_code": address.get("zip_code"),
        "is_default": address.get("is_default", True),
    }
    return initial


def _mapped_data(cleaned):
    return {
        "customer.full_name": cleaned.get("full_name", ""),
        "customer.business_name": cleaned.get("business_name", ""),
        "customer.rfc": cleaned.get("rfc", ""),
        "customer.email": cleaned.get("email", ""),
        "customer.phone": cleaned.get("phone", ""),
        "customer.whatsapp": cleaned.get("whatsapp", ""),
        "customer.customer_type": cleaned.get("customer_type", "customer"),
        "customer.source": cleaned.get("source", "manual"),
        "customer.notes": cleaned.get("notes", ""),
        "customer.estimated_monthly_purchase": (
            cleaned.get("estimated_monthly_purchase") or "0"
        ),
        "customer.is_active": cleaned.get("is_active", True),
        "address.address_type": cleaned.get("address_type", "shipping"),
        "address.street": cleaned.get("street", ""),
        "address.exterior_number": cleaned.get("exterior_number", ""),
        "address.interior_number": cleaned.get("interior_number", ""),
        "address.neighborhood": cleaned.get("neighborhood", ""),
        "address.city": cleaned.get("city", ""),
        "address.municipality": cleaned.get("municipality", ""),
        "address.state": cleaned.get("state", ""),
        "address.country": cleaned.get("country", "México"),
        "address.zip_code": cleaned.get("zip_code", ""),
        "address.is_default": cleaned.get("is_default", True),
    }


@transaction.atomic
def _revalidate(company, job, row, cleaned, user):
    locked_job = ImportJob.objects.select_for_update().get(
        pk=job.pk,
        company=company,
    )
    locked_row = ImportRowResult.objects.select_for_update().get(
        pk=row.pk,
        company=company,
        job=locked_job,
    )

    before = deepcopy(locked_row.normalized_data or {})
    customer, address, custom_values, errors, warnings = normalize_customer_row(
        _mapped_data(cleaned),
        company=company,
    )
    existing, matched_by = _find_existing(company, customer)

    if errors:
        status = ImportRowResult.STATUS_ERROR
    elif existing:
        status = ImportRowResult.STATUS_DUPLICATE
        warnings.append(
            f'Coincide con el cliente #{existing.pk} '
            f'"{existing.full_name}" por {matched_by}.'
        )
    elif warnings:
        status = ImportRowResult.STATUS_WARNING
    else:
        status = ImportRowResult.STATUS_VALID

    history = list(
        (locked_row.normalized_data or {}).get("_correction_history", [])
    )
    history.append(
        {
            "corrected_at": timezone.now().isoformat(),
            "corrected_by_id": user.pk,
            "note": str(cleaned.get("correction_note") or "").strip(),
            "before": before,
        }
    )

    locked_row.normalized_data = {
        "customer": customer,
        "address": address,
        "custom_fields": (
            custom_values
            or before.get("custom_fields", {})
        ),
        "_correction_history": history,
    }
    locked_row.errors = errors
    locked_row.warnings = warnings
    locked_row.status = status
    locked_row.matched_model = "crm.Customer" if existing else ""
    locked_row.matched_object_id = str(existing.pk) if existing else ""
    locked_row.action_taken = "corrected_pending_validation"

    if _has_field(ImportRowResult, "duplicate_resolution"):
        locked_row.duplicate_resolution = ""
    if _has_field(ImportRowResult, "duplicate_resolution_result"):
        locked_row.duplicate_resolution_result = ""
    if _has_field(ImportRowResult, "duplicate_match_data"):
        locked_row.duplicate_match_data = (
            {
                "matched_by": matched_by,
                "customer_id": existing.pk,
                "customer_name": existing.full_name,
            }
            if existing
            else {}
        )

    locked_row.full_clean()
    locked_row.save()
    return locked_row


def _create_change_log(job, row, instance):
    possible = {
        "company": job.company,
        "job": job,
        "row_result": row,
        "action": getattr(ImportChangeLog, "ACTION_CREATED", "created"),
        "model_label": (
            f"{instance._meta.app_label}.{instance.__class__.__name__}"
        ),
        "object_pk": str(instance.pk),
        "before_data": {},
        "after_data": {
            field.name: str(getattr(instance, field.name, "") or "")
            for field in instance._meta.fields
            if field.name not in {
                "id", "company", "customer", "assigned_to",
                "created_at", "updated_at",
            }
        },
    }
    allowed = {field.name for field in ImportChangeLog._meta.fields}
    ImportChangeLog.objects.create(
        **{key: value for key, value in possible.items() if key in allowed}
    )


def _address_complete(address):
    if not address or address.get("is_complete") is False:
        return False
    return all(
        str(address.get(name, "") or "").strip()
        for name in ("street", "city", "state", "zip_code")
    )


@transaction.atomic
def _import_corrected(company, job, row, user):
    locked_job = ImportJob.objects.select_for_update().get(
        pk=job.pk,
        company=company,
    )
    locked_row = ImportRowResult.objects.select_for_update().get(
        pk=row.pk,
        company=company,
        job=locked_job,
    )

    if locked_row.status not in {
        ImportRowResult.STATUS_VALID,
        ImportRowResult.STATUS_WARNING,
    }:
        raise ValidationError(
            "La fila debe quedar Válida o con Advertencia antes de importarse."
        )

    normalized = locked_row.normalized_data or {}
    customer_data = normalized.get("customer") or {}
    address_data = normalized.get("address") or {}

    existing, matched_by = _find_existing(company, customer_data)
    if existing:
        locked_row.status = ImportRowResult.STATUS_DUPLICATE
        locked_row.matched_model = "crm.Customer"
        locked_row.matched_object_id = str(existing.pk)
        locked_row.action_taken = "duplicate_detected_after_correction"
        locked_row.warnings = list(locked_row.warnings or []) + [
            f"Se detectó coincidencia por {matched_by}."
        ]
        locked_row.save()
        return None

    customer = Customer(company=company)
    if _has_field(Customer, "assigned_to"):
        customer.assigned_to = user

    _set_existing_fields(
        customer,
        {
            "full_name": str(customer_data.get("full_name") or "").strip(),
            "business_name": (
                str(customer_data.get("business_name") or "").strip() or None
            ),
            "rfc": str(customer_data.get("rfc") or "").strip() or None,
            "email": str(customer_data.get("email") or "").strip() or None,
            "phone": str(customer_data.get("phone") or "").strip() or None,
            "whatsapp": (
                str(customer_data.get("whatsapp") or "").strip() or None
            ),
            "customer_type": customer_data.get("customer_type") or "customer",
            "source": customer_data.get("source") or "manual",
            "notes": str(customer_data.get("notes") or "").strip() or None,
            "estimated_monthly_purchase": Decimal(
                str(customer_data.get("estimated_monthly_purchase") or "0")
            ),
            "is_active": bool(customer_data.get("is_active", True)),
        },
    )
    customer.full_clean()
    customer.save()
    _create_change_log(locked_job, locked_row, customer)

    address_action = "address_omitted"
    if _address_complete(address_data):
        duplicate_address = CustomerAddress.objects.filter(
            company=company,
            customer=customer,
            street=address_data.get("street", ""),
            city=address_data.get("city", ""),
            state=address_data.get("state", ""),
            zip_code=address_data.get("zip_code", ""),
        ).first()
        if duplicate_address:
            address_action = "address_existing"
        else:
            address = CustomerAddress(company=company, customer=customer)
            _set_existing_fields(
                address,
                {
                    "address_type": (
                        address_data.get("address_type") or "shipping"
                    ),
                    "street": address_data.get("street") or "",
                    "exterior_number": (
                        address_data.get("exterior_number") or None
                    ),
                    "interior_number": (
                        address_data.get("interior_number") or None
                    ),
                    "neighborhood": address_data.get("neighborhood") or None,
                    "city": address_data.get("city") or "",
                    "state": address_data.get("state") or "",
                    "country": address_data.get("country") or "México",
                    "zip_code": address_data.get("zip_code") or "",
                    "is_default": bool(address_data.get("is_default", True)),
                    "label": "Dirección corregida en importación",
                    "contact_name": customer.full_name,
                    "phone": customer.whatsapp or customer.phone or "",
                    "email": customer.email or "",
                    "country_code": "MX",
                    "reference": "Corregida desde el importador WMS360",
                },
            )
            address.full_clean()
            address.save()
            _create_change_log(locked_job, locked_row, address)
            address_action = "address_created"

    locked_row.status = ImportRowResult.STATUS_IMPORTED
    locked_row.matched_model = "crm.Customer"
    locked_row.matched_object_id = str(customer.pk)
    locked_row.action_taken = (
        f"customer_created_after_correction|{address_action}"
    )
    locked_row.errors = []
    locked_row.save()

    locked_job.created_count += 1
    locked_job.save(update_fields=["created_count", "updated_at"])
    return customer


@login_required(login_url="/admin/login/")
def pending_customers(request, pk):
    company = require_company(request)
    if not company:
        return redirect("data_imports:dashboard")

    job = get_company_job(company=company, pk=pk)
    rows = ImportRowResult.objects.for_company(company).filter(
        job=job
    ).order_by("row_number")

    error_rows = rows.filter(status=ImportRowResult.STATUS_ERROR)
    duplicate_rows = rows.filter(status=ImportRowResult.STATUS_DUPLICATE)
    skipped_rows = rows.filter(status=ImportRowResult.STATUS_SKIPPED)
    corrected_rows = rows.filter(
        status=ImportRowResult.STATUS_IMPORTED,
        action_taken__icontains="after_correction",
    )
    address_omitted = rows.filter(
        action_taken__icontains="address_omitted"
    )

    return render(
        request,
        "data_imports/pending_customers.html",
        {
            "company": company,
            "job": job,
            "error_rows": error_rows,
            "duplicate_rows": duplicate_rows,
            "skipped_rows": skipped_rows,
            "corrected_rows": corrected_rows,
            "error_count": error_rows.count(),
            "duplicate_count": duplicate_rows.count(),
            "skipped_count": skipped_rows.count(),
            "corrected_count": corrected_rows.count(),
            "address_omitted_count": address_omitted.count(),
        },
    )


@login_required(login_url="/admin/login/")
def edit_pending_customer(request, pk, row_pk):
    company = require_company(request)
    if not company:
        return redirect("data_imports:dashboard")

    job = get_company_job(company=company, pk=pk)
    row = _company_row(company, job, row_pk)

    if row.status == ImportRowResult.STATUS_IMPORTED:
        messages.info(request, "Esta fila ya fue importada.")
        return redirect("data_imports:pending_customers", pk=job.pk)

    if request.method == "POST":
        form = PendingCustomerForm(request.POST)
        if form.is_valid():
            updated = _revalidate(
                company,
                job,
                row,
                form.cleaned_data,
                request.user,
            )
            if updated.status == ImportRowResult.STATUS_DUPLICATE:
                messages.warning(
                    request,
                    "La corrección coincide con un cliente existente.",
                )
                return redirect(
                    "data_imports:resolve_duplicates",
                    pk=job.pk,
                )
            if updated.status == ImportRowResult.STATUS_ERROR:
                messages.error(
                    request,
                    "La fila todavía contiene errores.",
                )
            else:
                messages.success(
                    request,
                    "Corrección guardada. La fila ya puede importarse.",
                )
            return redirect(
                "data_imports:edit_pending_customer",
                pk=job.pk,
                row_pk=row.pk,
            )
    else:
        form = PendingCustomerForm(initial=_initial_from_row(row))

    return render(
        request,
        "data_imports/pending_customer_edit.html",
        {
            "company": company,
            "job": job,
            "row": row,
            "form": form,
        },
    )


@login_required(login_url="/admin/login/")
@require_POST
def import_pending_customer(request, pk, row_pk):
    company = require_company(request)
    if not company:
        return redirect("data_imports:dashboard")

    job = get_company_job(company=company, pk=pk)
    row = _company_row(company, job, row_pk)

    try:
        customer = _import_corrected(
            company,
            job,
            row,
            request.user,
        )
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return redirect(
            "data_imports:edit_pending_customer",
            pk=job.pk,
            row_pk=row.pk,
        )

    if customer is None:
        messages.warning(
            request,
            "La fila coincide con un cliente existente. Resuelve el duplicado.",
        )
        return redirect(
            "data_imports:resolve_duplicates",
            pk=job.pk,
        )

    messages.success(
        request,
        f'Cliente "{customer.full_name}" creado con ID {customer.pk}.',
    )
    return redirect("data_imports:pending_customers", pk=job.pk)


@login_required(login_url="/admin/login/")
def download_pending_report(request, pk):
    company = require_company(request)
    if not company:
        return redirect("data_imports:dashboard")

    job = get_company_job(company=company, pk=pk)
    rows = ImportRowResult.objects.for_company(company).filter(
        job=job,
        status__in=[
            ImportRowResult.STATUS_ERROR,
            ImportRowResult.STATUS_DUPLICATE,
            ImportRowResult.STATUS_SKIPPED,
        ],
    ).order_by("row_number")

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "No importados"
    headers = [
        "Fila",
        "Estado",
        "Nombre / Razón social",
        "RFC",
        "Correo",
        "Teléfono",
        "WhatsApp",
        "Errores",
        "Advertencias",
        "Cliente relacionado",
        "Acción tomada",
        "Acción recomendada",
    ]
    sheet.append(headers)

    fill = PatternFill("solid", fgColor="991B1B")
    font = Font(color="FFFFFF", bold=True)
    for cell in sheet[1]:
        cell.fill = fill
        cell.font = font
        cell.alignment = Alignment(
            horizontal="center",
            vertical="center",
            wrap_text=True,
        )

    for row in rows:
        customer = (row.normalized_data or {}).get("customer") or {}
        if row.status == ImportRowResult.STATUS_ERROR:
            recommendation = "Corregir y volver a validar."
        elif row.status == ImportRowResult.STATUS_DUPLICATE:
            recommendation = "Resolver la coincidencia."
        else:
            recommendation = "Revisar la acción aplicada."

        sheet.append(
            [
                row.row_number,
                row.get_status_display(),
                customer.get("full_name", ""),
                customer.get("rfc", ""),
                customer.get("email", ""),
                customer.get("phone", ""),
                customer.get("whatsapp", ""),
                " | ".join(row.errors or []),
                " | ".join(row.warnings or []),
                row.matched_object_id,
                row.action_taken,
                recommendation,
            ]
        )

    widths = {
        "A": 12, "B": 18, "C": 34, "D": 18,
        "E": 28, "F": 19, "G": 19, "H": 48,
        "I": 48, "J": 20, "K": 38, "L": 42,
    }
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:L{sheet.max_row}"

    output = BytesIO()
    workbook.save(output)
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{job.folio}_clientes_no_importados.xlsx"'
    )
    return response
