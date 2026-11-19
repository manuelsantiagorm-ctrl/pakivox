import logging
from collections import Counter
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import (
    get_object_or_404,
    redirect,
    render,
)
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from accounts.warehouse_access import (
    ACCESS_OPERATE,
    require_active_warehouse,
)

from inventory.models import ProductVariant
from inventory.stock_services import register_manual_stock_entry

from .forms import (
    CustomerImportUploadForm,
    CustomerMappingForm,
    ImportSheetForm,
    InventoryImportUploadForm,
    InventoryMappingForm,
    ProductImportUploadForm,
    ProductMappingForm,
    SupplierImportUploadForm,
    SupplierMappingForm,
)

from .models import (
    ImportColumnMapping,
    ImportJob,
    ImportRowResult,
)

from .services.customer_import import (
    CUSTOMER_FIELD_POLICIES,
    CUSTOMER_MAPPING_DEFAULTS_OPTION_KEY,
    CUSTOMER_TARGET_CHOICES,
    build_customer_mapping_analysis,
    get_customer_mapping_defaults,
    get_saved_mapping,
    persist_mapping,
    sanitize_customer_mapping_defaults,
    suggest_mapping,
    validate_customer_rows,
)

from .services.customer_writer import import_customer_job

from .services.duplicate_resolver import (
    apply_completed_duplicate_resolutions,
    build_duplicate_cards,
    bulk_resolve_duplicate_confidence,
    count_duplicates_by_confidence,
    duplicate_rows_queryset,
    save_duplicate_resolutions,
)
from .services.inventory_import import (
    INVENTORY_TARGET_CHOICES,
    get_inventory_mapping,
    import_inventory_job,
    persist_inventory_mapping,
    suggest_inventory_mapping,
    validate_inventory_rows,
)

from .services.product_import import (
    PRODUCT_TARGET_CHOICES,
    get_product_mapping,
    persist_product_mapping,
    suggest_product_mapping,
)

from .services.product_writer import (
    import_product_job,
    validate_product_rows,
)

from .services.supplier_import import (
    SUPPLIER_TARGET_CHOICES,
    get_supplier_mapping,
    persist_supplier_mapping,
    suggest_supplier_mapping,
    validate_supplier_rows,
)

from .services.supplier_writer import import_supplier_job

from .services.tabular_reader import (
    ImportFileError,
    list_sheet_names,
    read_tabular_file,
)

logger = logging.getLogger(__name__)


logger = logging.getLogger(
    __name__
)


def _get_inventory_job_warehouse(
    *,
    request,
    job,
):
    """
    Obtiene y valida la bodega operativa asociada
    a un lote de inventario inicial.
    """

    warehouse = require_active_warehouse(
        request=request,
        required_level=ACCESS_OPERATE,
    )

    if warehouse.company_id != job.company_id:
        raise ValidationError("La bodega activa no pertenece a esta empresa.")

    options = job.options if isinstance(job.options, dict) else {}

    stored_warehouse_id = options.get("inventory_warehouse_id")

    if stored_warehouse_id:
        try:
            stored_warehouse_id = int(stored_warehouse_id)
        except (
            TypeError,
            ValueError,
        ):
            raise ValidationError("La bodega asociada al lote no es válida.")

        if warehouse.pk != stored_warehouse_id:
            raise ValidationError(
                (
                    "Este lote de inventario pertenece a otra bodega. "
                    "Selecciona la misma bodega con la que "
                    "creaste la importación."
                )
            )

    return warehouse


def get_user_company(user):
    profile = getattr(
        user,
        "profile",
        None,
    )

    if profile and getattr(
        profile,
        "company",
        None,
    ):
        return profile.company

    return getattr(
        user,
        "company",
        None,
    )


def require_company(request):
    company = get_user_company(
        request.user
    )

    if not company:
        messages.error(
            request,
            "Tu usuario no tiene una empresa asignada.",
        )

    return company


def get_company_job(
    *,
    company,
    pk,
):
    return get_object_or_404(
        ImportJob.objects
        .for_company(company)
        .select_related(
            "company",
            "created_by",
            "reverted_by",
        ),
        pk=pk,
    )


def _rows_with_saved_corrections(job, file_data):
    """Apply user corrections without modifying the uploaded source file."""
    options = job.options if isinstance(job.options, dict) else {}
    corrections = options.get("row_corrections", {})
    headers = [str(header) for header in file_data["headers"]]
    header_indexes = {
        header: index
        for index, header in enumerate(headers)
    }
    corrected_rows = []

    for fallback_number, original_row in enumerate(
        file_data["rows"],
        start=2,
    ):
        if isinstance(original_row, dict):
            row = dict(original_row)
            values = list(row.get("values", []))
            row_number = row.get("row_number", fallback_number)
        else:
            values = list(original_row)
            row_number = fallback_number
            row = {"row_number": row_number}

        row_changes = corrections.get(str(row_number), {})

        for source_column, value in row_changes.items():
            index = header_indexes.get(str(source_column))

            if index is None:
                continue

            while len(values) <= index:
                values.append("")

            values[index] = value

        row["values"] = values
        corrected_rows.append(row)

    return corrected_rows


def _apply_saved_skips(job, counters):
    options = job.options if isinstance(job.options, dict) else {}
    skipped_numbers = {
        int(value)
        for value in options.get("skipped_row_numbers", [])
        if str(value).isdigit()
    }

    if not skipped_numbers:
        return

    rows = list(
        ImportRowResult.objects
        .for_company(job.company)
        .filter(job=job, row_number__in=skipped_numbers)
    )
    counter_by_status = {
        ImportRowResult.STATUS_VALID: "valid_rows",
        ImportRowResult.STATUS_WARNING: "warning_rows",
        ImportRowResult.STATUS_ERROR: "error_rows",
        ImportRowResult.STATUS_DUPLICATE: "duplicate_rows",
    }

    for row in rows:
        counter_name = counter_by_status.get(row.status)

        if counter_name and counters[counter_name] > 0:
            counters[counter_name] -= 1

        row.status = ImportRowResult.STATUS_SKIPPED
        row.action_taken = "Omitida manualmente"
        row.errors = []
        row.warnings = ["Esta fila fue omitida por el usuario."]

    if rows:
        ImportRowResult.objects.bulk_update(
            rows,
            ["status", "action_taken", "errors", "warnings", "updated_at"],
        )


def _set_job_validation_result(job, counters):
    for field_name in (
        "total_rows",
        "valid_rows",
        "warning_rows",
        "error_rows",
        "duplicate_rows",
    ):
        setattr(job, field_name, counters[field_name])

    importable = job.valid_rows + job.warning_rows

    if job.duplicate_policy in {
        ImportJob.DUPLICATE_UPDATE,
        ImportJob.DUPLICATE_CREATE,
    }:
        importable += job.duplicate_rows

    if (
        job.duplicate_rows
        and job.duplicate_policy == ImportJob.DUPLICATE_REVIEW
    ):
        job.status = ImportJob.STATUS_RESOLVING_DUPLICATES
        job.error_summary = ""
    elif (
        job.duplicate_rows
        and job.duplicate_policy == ImportJob.DUPLICATE_STOP
    ):
        job.status = ImportJob.STATUS_FAILED
        job.error_summary = "La importación se detuvo por duplicados."
    elif importable:
        job.status = ImportJob.STATUS_READY
        job.error_summary = ""
    else:
        job.status = ImportJob.STATUS_FAILED
        job.error_summary = "Ninguna fila quedó disponible para importar."

    job.save(
        update_fields=[
            "total_rows",
            "valid_rows",
            "warning_rows",
            "error_rows",
            "duplicate_rows",
            "status",
            "error_summary",
            "updated_at",
        ]
    )


def _revalidate_job_with_saved_changes(
    job,
    *,
    request=None,
    inventory_warehouse=None,
):
    """
    Revalida un lote aplicando primero las correcciones
    guardadas por el usuario.

    Compatible con:
    - Clientes
    - Proveedores
    - Productos
    - Inventario inicial

    Para inventario se requiere una bodega concreta.
    """

    # ==========================================================
    # 1. CARGAR ARCHIVO
    # ==========================================================

    file_data = load_job_file(job)

    # ==========================================================
    # 2. APLICAR CORRECCIONES GUARDADAS
    # ==========================================================

    rows = _rows_with_saved_corrections(
        job,
        file_data,
    )

    # ==========================================================
    # 3. PROVEEDORES
    # ==========================================================

    if job.entity_type == ImportJob.ENTITY_SUPPLIERS:
        mapping = get_supplier_mapping(job)

        counters = validate_supplier_rows(
            job=job,
            headers=file_data["headers"],
            rows=rows,
            mapping=mapping,
        )

    # ==========================================================
    # 4. CLIENTES
    # ==========================================================

    elif job.entity_type == ImportJob.ENTITY_CUSTOMERS:
        mapping = get_saved_mapping(job)

        counters = validate_customer_rows(
            job=job,
            headers=file_data["headers"],
            rows=rows,
            mapping=mapping,
        )

    # ==========================================================
    # 5. PRODUCTOS
    # ==========================================================

    elif job.entity_type == ImportJob.ENTITY_PRODUCTS:
        mapping = get_product_mapping(job)

        counters = validate_product_rows(
            job=job,
            headers=file_data["headers"],
            rows=rows,
            mapping=mapping,
        )

    # ==========================================================
    # 6. INVENTARIO INICIAL
    # ==========================================================

    elif job.entity_type == ImportJob.ENTITY_INVENTORY:
        mapping = get_inventory_mapping(job)

        # ------------------------------------------------------
        # Obtener bodega
        # ------------------------------------------------------

        warehouse = inventory_warehouse

        # Si no vino explícitamente,
        # intentamos obtenerla desde request.
        if warehouse is None and request is not None:
            warehouse = _get_inventory_job_warehouse(
                request=request,
                job=job,
            )

        if warehouse is None:
            raise ValidationError(
                ("No se pudo identificar la bodega " "del lote de inventario.")
            )

        # ------------------------------------------------------
        # Seguridad adicional
        # ------------------------------------------------------

        if warehouse.company_id != job.company_id:
            raise ValidationError(
                ("La bodega del lote no pertenece " "a esta empresa.")
            )

        if not warehouse.is_active:
            raise ValidationError(
                ("No puedes revalidar inventario " "en una bodega inactiva.")
            )

        # ------------------------------------------------------
        # Validar filas de inventario
        # ------------------------------------------------------

        counters = validate_inventory_rows(
            job=job,
            warehouse=warehouse,
            headers=file_data["headers"],
            rows=rows,
            mapping=mapping,
        )

    # ==========================================================
    # 7. TIPO NO SOPORTADO
    # ==========================================================

    else:
        raise ValidationError(
            (
                "La corrección de filas no está "
                "disponible para este tipo de importación."
            )
        )

    # ==========================================================
    # 8. APLICAR FILAS OMITIDAS
    # ==========================================================

    _apply_saved_skips(
        job,
        counters,
    )

    # ==========================================================
    # 9. ACTUALIZAR ESTADO DEL LOTE
    # ==========================================================

    _set_job_validation_result(
        job,
        counters,
    )

    # ==========================================================
    # 10. RESULTADO
    # ==========================================================

    return counters


def load_job_file(job):
    return read_tabular_file(
        job.original_file,
        job.file_extension,
        sheet_name=job.sheet_name,
    )


@login_required(login_url="/admin/login/")
def dashboard(request):
    company = require_company(
        request
    )

    if not company:
        return render(
            request,
            "data_imports/dashboard.html",
            {
                "company": None,
                "jobs": [],
                "total_jobs": 0,
                "completed_jobs": 0,
                "failed_jobs": 0,
                "ready_jobs": 0,
            },
        )

    jobs_qs = (
        ImportJob.objects
        .for_company(company)
        .select_related(
            "company",
            "created_by",
        )
    )

    context = {
        "company": company,
        "jobs": jobs_qs[:20],
        "total_jobs": jobs_qs.count(),
        "completed_jobs": jobs_qs.filter(
            status=ImportJob.STATUS_COMPLETED,
        ).count(),
        "failed_jobs": jobs_qs.filter(
            status=ImportJob.STATUS_FAILED,
        ).count(),
        "ready_jobs": jobs_qs.filter(
            status=ImportJob.STATUS_READY,
        ).count(),
    }

    return render(
        request,
        "data_imports/dashboard.html",
        context,
    )


@login_required(login_url="/admin/login/")
def upload_customers(request):
    company = require_company(
        request
    )

    if not company:
        return redirect(
            "data_imports:dashboard"
        )

    if request.method == "POST":
        form = CustomerImportUploadForm(
            request.POST,
            request.FILES,
        )

        if form.is_valid():
            uploaded_file = form.cleaned_data[
                "original_file"
            ]

            extension = Path(
                uploaded_file.name
            ).suffix.lower()

            with transaction.atomic():
                job = ImportJob.objects.create(
                    company=company,
                    created_by=request.user,
                    entity_type=ImportJob.ENTITY_CUSTOMERS,
                    status=ImportJob.STATUS_UPLOADED,
                    original_file=uploaded_file,
                    original_filename=uploaded_file.name,
                    file_extension=extension,
                    file_size=uploaded_file.size,
                    duplicate_policy=form.cleaned_data[
                        "duplicate_policy"
                    ],
                )

                try:
                    sheet_names = list_sheet_names(
                        job.original_file,
                        extension,
                    )

                    selected_sheet = (
                        sheet_names[0]
                        if sheet_names
                        else ""
                    )

                    file_data = read_tabular_file(
                        job.original_file,
                        extension,
                        sheet_name=selected_sheet,
                    )

                    proposed_mapping = suggest_mapping(
                        file_data["headers"],
                        company=company,
                    )

                    job.sheet_name = file_data[
                        "sheet_name"
                    ]
                    job.detected_headers = file_data[
                        "headers"
                    ]
                    job.proposed_mapping = proposed_mapping
                    job.total_rows = len(
                        file_data["rows"]
                    )
                    job.status = ImportJob.STATUS_MAPPING
                    job.error_summary = ""
                    job.save()

                    persist_mapping(
                        job=job,
                        headers=file_data["headers"],
                        mapping=proposed_mapping,
                    )

                except ImportFileError as error:
                    job.status = ImportJob.STATUS_FAILED
                    job.error_summary = str(error)
                    job.save(
                        update_fields=[
                            "status",
                            "error_summary",
                            "updated_at",
                        ]
                    )

                    messages.error(
                        request,
                        str(error),
                    )

                    return redirect(
                        "data_imports:job_detail",
                        pk=job.pk,
                    )

            messages.success(
                request,
                "Archivo cargado. Revisa la relación de columnas.",
            )

            return redirect(
                "data_imports:customer_mapping",
                pk=job.pk,
            )

    else:
        form = CustomerImportUploadForm()

    return render(
        request,
        "data_imports/upload_customers.html",
        {
            "company": company,
            "form": form,
        },
    )


@login_required(login_url="/admin/login/")
def customer_mapping(
    request,
    pk,
):
    company = require_company(
        request
    )

    if not company:
        return redirect(
            "data_imports:dashboard"
        )

    job = get_company_job(
        company=company,
        pk=pk,
    )

    if job.entity_type != ImportJob.ENTITY_CUSTOMERS:
        messages.error(
            request,
            "Esta importación no corresponde a clientes.",
        )

        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )

    if job.status == ImportJob.STATUS_COMPLETED:
        messages.warning(
            request,
            "Este lote ya fue importado y no puede volver a mapearse.",
        )

        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )

    try:
        sheet_names = list_sheet_names(
            job.original_file,
            job.file_extension,
        )
    except ImportFileError as error:
        messages.error(
            request,
            str(error),
        )

        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )

    if request.method == "POST":
        action = request.POST.get(
            "action",
            "save_mapping",
        )

        if action == "change_sheet":
            sheet_form = ImportSheetForm(
                request.POST,
                sheet_names=sheet_names,
            )

            if sheet_form.is_valid():
                selected_sheet = sheet_form.cleaned_data[
                    "sheet_name"
                ]

                try:
                    file_data = read_tabular_file(
                        job.original_file,
                        job.file_extension,
                        sheet_name=selected_sheet,
                    )
                except ImportFileError as error:
                    messages.error(
                        request,
                        str(error),
                    )
                else:
                    proposed_mapping = suggest_mapping(
                        file_data["headers"],
                        company=company,
                    )

                    with transaction.atomic():
                        job.sheet_name = file_data[
                            "sheet_name"
                        ]
                        job.detected_headers = file_data[
                            "headers"
                        ]
                        job.proposed_mapping = proposed_mapping
                        options = dict(job.options or {})
                        options.pop(
                            CUSTOMER_MAPPING_DEFAULTS_OPTION_KEY,
                            None,
                        )
                        job.options = options
                        job.total_rows = len(
                            file_data["rows"]
                        )
                        job.status = ImportJob.STATUS_MAPPING
                        job.error_summary = ""
                        job.save()

                        persist_mapping(
                            job=job,
                            headers=file_data["headers"],
                            mapping=proposed_mapping,
                        )

                    messages.success(
                        request,
                        f'Hoja "{selected_sheet}" seleccionada.',
                    )

            return redirect(
                "data_imports:customer_mapping",
                pk=job.pk,
            )

        mapping_form = CustomerMappingForm(
            request.POST,
            headers=job.detected_headers,
            company=company,
            initial_mapping=get_saved_mapping(
                job
            ),
        )

        if mapping_form.is_valid():
            mapping = mapping_form.get_mapping()

            raw_defaults = {
                target: request.POST.get(
                    f"default__{target}",
                    "",
                )
                for target in CUSTOMER_FIELD_POLICIES
            }
            mapping_defaults = sanitize_customer_mapping_defaults(
                values=raw_defaults,
                mapping=mapping,
            )

            with transaction.atomic():
                persist_mapping(
                    job=job,
                    headers=job.detected_headers,
                    mapping=mapping,
                )

                options = dict(job.options or {})
                options[CUSTOMER_MAPPING_DEFAULTS_OPTION_KEY] = mapping_defaults

                job.proposed_mapping = mapping
                job.options = options
                job.status = ImportJob.STATUS_VALIDATING
                job.error_summary = ""
                job.save(
                    update_fields=[
                        "proposed_mapping",
                        "options",
                        "status",
                        "error_summary",
                        "updated_at",
                    ]
                )

            messages.success(
                request,
                "Relación de columnas guardada. Ya puedes validar el archivo.",
            )

            return redirect(
                "data_imports:job_detail",
                pk=job.pk,
            )

    else:
        mapping_form = CustomerMappingForm(
            headers=job.detected_headers,
            company=company,
            initial_mapping=get_saved_mapping(
                job
            ),
        )

    sheet_form = ImportSheetForm(
        initial={
            "sheet_name": job.sheet_name,
        },
        sheet_names=sheet_names,
    )

    current_mapping = (
        mapping_form.get_mapping()
        if request.method == "POST" and mapping_form.is_valid()
        else get_saved_mapping(job)
    )
    mapping_defaults = get_customer_mapping_defaults(job)

    mapping_analysis = build_customer_mapping_analysis(
        headers=job.detected_headers,
        mapping=current_mapping,
        defaults=mapping_defaults,
    )

    mapping_rows = []

    for index, header in enumerate(
        job.detected_headers
    ):
        field_name = f"column_{index}"

        mapping_rows.append(
            {
                "index": index,
                "header": header,
                "field": mapping_form[
                    field_name
                ],
            }
        )

    return render(
        request,
        "data_imports/customer_mapping.html",
        {
            "company": company,
            "job": job,
            "sheet_form": sheet_form,
            "mapping_form": mapping_form,
            "mapping_rows": mapping_rows,
            "target_choices": mapping_form.target_choices,
            "mapping_analysis": mapping_analysis,
            "mapping_defaults": mapping_defaults,
        },
    )


@login_required(login_url="/admin/login/")
@require_POST
def validate_customers(
    request,
    pk,
):
    company = require_company(
        request
    )

    if not company:
        return redirect(
            "data_imports:dashboard"
        )

    job = get_company_job(
        company=company,
        pk=pk,
    )

    if job.status == ImportJob.STATUS_COMPLETED:
        messages.warning(
            request,
            "Este lote ya fue importado y no puede validarse otra vez.",
        )

        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )

    if job.entity_type != ImportJob.ENTITY_CUSTOMERS:
        messages.error(
            request,
            "Esta importación no corresponde a clientes.",
        )

        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )

    mapping = get_saved_mapping(
        job
    )

    if "customer.full_name" not in mapping.values():
        messages.error(
            request,
            "Primero relaciona la columna de nombre del cliente.",
        )

        return redirect(
            "data_imports:customer_mapping",
            pk=job.pk,
        )

    try:
        file_data = load_job_file(
            job
        )

        counters = validate_customer_rows(
            job=job,
            headers=file_data["headers"],
            rows=_rows_with_saved_corrections(job, file_data),
            mapping=mapping,
        )
        _apply_saved_skips(job, counters)

    except ImportFileError as error:
        job.status = ImportJob.STATUS_FAILED
        job.error_summary = str(error)
        job.save(
            update_fields=[
                "status",
                "error_summary",
                "updated_at",
            ]
        )

        messages.error(
            request,
            str(error),
        )

        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )

    with transaction.atomic():
        job.total_rows = counters[
            "total_rows"
        ]
        job.valid_rows = counters[
            "valid_rows"
        ]
        job.warning_rows = counters[
            "warning_rows"
        ]
        job.error_rows = counters[
            "error_rows"
        ]
        job.duplicate_rows = counters[
            "duplicate_rows"
        ]

        importable_rows = (
            job.valid_rows
            + job.warning_rows
            + (
                job.duplicate_rows
                if job.duplicate_policy
                in {
                    ImportJob.DUPLICATE_UPDATE,
                    ImportJob.DUPLICATE_CREATE,
                }
                else 0
            )
        )

        if (
            job.duplicate_rows > 0
            and job.duplicate_policy == ImportJob.DUPLICATE_REVIEW
        ):
            job.status = ImportJob.STATUS_RESOLVING_DUPLICATES
            job.error_summary = ""
        elif (
            job.duplicate_rows > 0
            and job.duplicate_policy == ImportJob.DUPLICATE_STOP
        ):
            job.status = ImportJob.STATUS_FAILED
            job.error_summary = (
                "La importación se detuvo porque se detectaron duplicados."
            )
        elif importable_rows > 0:
            job.status = ImportJob.STATUS_READY
            job.error_summary = ""
        else:
            job.status = ImportJob.STATUS_FAILED
            job.error_summary = (
                "Ninguna fila quedó disponible para importar."
            )

        job.save(
            update_fields=[
                "total_rows",
                "valid_rows",
                "warning_rows",
                "error_rows",
                "duplicate_rows",
                "status",
                "error_summary",
                "updated_at",
            ]
        )

    messages.success(
        request,
        "Validación terminada. Revisa los resultados antes de importar.",
    )

    return redirect(
        "data_imports:job_detail",
        pk=job.pk,
    )


@login_required(login_url="/admin/login/")
def resolve_duplicates(
    request,
    pk,
):
    company = require_company(request)

    if not company:
        return redirect("data_imports:dashboard")

    job = get_company_job(company=company, pk=pk)
    base_queryset = duplicate_rows_queryset(job)

    pending_count = base_queryset.filter(
        duplicate_resolution="",
    ).count()
    resolved_count = base_queryset.exclude(
        duplicate_resolution="",
    ).count()
    paginator = Paginator(base_queryset, 100)
    page_number = request.GET.get("page", 1)
    page_obj = paginator.get_page(page_number)

    cards = build_duplicate_cards(job, rows=page_obj.object_list)
    confidence_counts = count_duplicates_by_confidence(job)

    return render(
        request,
        "data_imports/duplicate_resolution.html",
        {
            "company": company,
            "job": job,
            "cards": cards,
            "page_obj": page_obj,
            "pending_count": pending_count,
            "resolved_count": resolved_count,
            "confidence_counts": confidence_counts,
            "resolution_choices": (
                ImportRowResult.DUPLICATE_RESOLUTION_CHOICES
            ),
        },
    )


@login_required(login_url="/admin/login/")
@require_POST
def bulk_resolve_duplicates(
    request,
    pk,
):
    company = require_company(request)

    if not company:
        return redirect("data_imports:dashboard")

    job = get_company_job(company=company, pk=pk)
    confidence = request.POST.get("confidence")

    if confidence not in {"alta", "media"}:
        messages.error(request, "Nivel de confianza no válido.")
        return redirect("data_imports:resolve_duplicates", pk=job.pk)

    updated = bulk_resolve_duplicate_confidence(
        job=job,
        user=request.user,
        confidence=confidence,
    )

    if not updated:
        messages.info(
            request,
            "No hay duplicados pendientes en ese nivel de confianza.",
        )
        return redirect("data_imports:resolve_duplicates", pk=job.pk)

    if job.status == ImportJob.STATUS_COMPLETED:
        result = apply_completed_duplicate_resolutions(
            job=job,
            user=request.user,
        )
        messages.success(
            request,
            (
                f"Se resolvieron {updated} duplicados de confianza "
                f"{confidence} automáticamente. "
                f"Resultado: {result['created']} clientes creados, "
                f"{result['updated']} actualizados y "
                f"{result['activated']} registros reactivados."
            ),
        )
    else:
        messages.success(
            request,
            f"Se resolvieron {updated} duplicados de confianza "
            f"{confidence} automáticamente.",
        )

    unresolved = (
        duplicate_rows_queryset(job)
        .filter(
            duplicate_resolution="",
        )
        .count()
    )
    if unresolved == 0:
        outcome = "completed" if job.status == ImportJob.STATUS_COMPLETED else "ready"
        return redirect(
            reverse("data_imports:job_detail", args=[job.pk])
            + f"?resolved=1&outcome={outcome}"
        )

    return redirect("data_imports:resolve_duplicates", pk=job.pk)

@login_required(login_url="/admin/login/")
@require_POST
def save_duplicates(
    request,
    pk,
):
    company = require_company(request)

    if not company:
        return redirect("data_imports:dashboard")

    job = get_company_job(company=company, pk=pk)

    try:
        saved, unresolved = save_duplicate_resolutions(
            job=job,
            user=request.user,
            post_data=request.POST,
        )

        if job.status == ImportJob.STATUS_COMPLETED:
            result = apply_completed_duplicate_resolutions(
                job=job,
                user=request.user,
            )
            messages.success(
                request,
                (
                    f"Resoluciones aplicadas: {result['created']} clientes "
                    f"creados, {result['updated']} actualizados y "
                    f"{result['activated']} registros reactivados."
                ),
            )
        elif unresolved:
            messages.warning(
                request,
                f"Se guardaron {saved} decisiones. Aún faltan "
                f"{unresolved} duplicados por resolver.",
            )
        else:
            messages.success(
                request,
                "Todos los duplicados fueron resueltos. "
                "El lote ya está listo para confirmar.",
                )
            return redirect(
                reverse("data_imports:job_detail", args=[job.pk])
                + "?resolved=1&outcome=ready"
            )
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))

    return redirect(
        "data_imports:resolve_duplicates",
        pk=job.pk,
    )


@login_required(login_url="/admin/login/")
@require_POST
def confirm_customer_import(
    request,
    pk,
):
    company = require_company(
        request
    )

    if not company:
        return redirect(
            "data_imports:dashboard"
        )

    job = get_company_job(
        company=company,
        pk=pk,
    )

    if job.entity_type != ImportJob.ENTITY_CUSTOMERS:
        messages.error(
            request,
            "Esta importación no corresponde a clientes.",
        )

        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )

    unresolved_duplicates = duplicate_rows_queryset(job).filter(
        status=ImportRowResult.STATUS_DUPLICATE,
        duplicate_resolution="",
    ).count()

    if unresolved_duplicates:
        messages.error(
            request,
            "Primero debes resolver todos los duplicados.",
        )
        return redirect(
            "data_imports:resolve_duplicates",
            pk=job.pk,
        )
    from .tasks import process_customer_import_task

    process_customer_import_task.delay(job.pk, request.user.pk)

    messages.success(
        request,
        "Tu importación se está procesando en segundo plano. "
        "Esta página se actualizará con el resultado en unos momentos.",
    )
    return redirect(
        "data_imports:job_detail",
        pk=job.pk,
    )


def _resolver_target_from_issue(
    *,
    job,
    message,
):
    """
    Detecta qué campo lógico de WMS360 está relacionado
    con un error o advertencia del importador.

    Compatible con:
    - Clientes
    - Proveedores
    - Productos
    - Inventario inicial

    Devuelve el target utilizado por el mapeador, por ejemplo:
        customer.rfc
        supplier.email
        product.sale_price
        variant.sku
        inventory.sku
        inventory.quantity

    Si no puede identificar el campo con seguridad,
    devuelve una cadena vacía.
    """

    text = str(message or "").strip().casefold()

    if not text:
        return ""

    # ==========================================================
    # PRODUCTOS
    # ==========================================================

    if job.entity_type == ImportJob.ENTITY_PRODUCTS:
        rules = [
            (
                (
                    "nombre del producto",
                    "producto es obligatorio",
                ),
                "product.name",
            ),
            # SKU base antes del SKU genérico.
            (
                (
                    "sku base",
                    "sku del producto",
                ),
                "product.base_sku",
            ),
            (
                (
                    "precio de venta de variante",
                    "precio venta de variante",
                ),
                "variant.sale_price",
            ),
            (
                (
                    "precio mayoreo de variante",
                    "precio de mayoreo de variante",
                    "precio mayorista de variante",
                ),
                "variant.wholesale_price",
            ),
            (
                (
                    "costo de variante",
                    "costo de la variante",
                ),
                "variant.cost_price",
            ),
            (
                (
                    "stock mínimo",
                    "stock minimo",
                ),
                "variant.min_stock",
            ),
            (
                (
                    "precio de venta",
                    "precio venta",
                ),
                "product.sale_price",
            ),
            (
                (
                    "precio mayoreo",
                    "precio de mayoreo",
                    "precio mayorista",
                ),
                "product.wholesale_price",
            ),
            (
                (
                    "costo del producto",
                    "costo producto",
                ),
                "product.cost_price",
            ),
            (
                (
                    "factor de conversión",
                    "factor de conversion",
                ),
                "product.conversion_factor",
            ),
            (
                ("tipo de producto",),
                "product.product_type",
            ),
            (
                ("modo de stock",),
                "product.stock_mode",
            ),
            (
                ("unidad base",),
                "product.base_unit",
            ),
            (
                ("unidad comercial",),
                "product.commercial_unit",
            ),
            (
                (
                    "categoría",
                    "categoria",
                ),
                "product.category",
            ),
            (
                ("marca",),
                "product.brand",
            ),
            (
                (
                    "presentación",
                    "presentacion",
                ),
                "variant.presentation",
            ),
            (
                ("color",),
                "variant.color",
            ),
            (
                (
                    "talla",
                    "size",
                ),
                "variant.size",
            ),
            (
                (
                    "código de barras",
                    "codigo de barras",
                    "barcode",
                ),
                "variant.barcode",
            ),
            # SKU genérico siempre al final.
            (
                (
                    "sku de la variante",
                    "sku está repetido",
                    "sku esta repetido",
                    "sku ya existe",
                    "sku obligatorio",
                    "sku de variante",
                    "sku",
                ),
                "variant.sku",
            ),
        ]

    # ==========================================================
    # INVENTARIO INICIAL
    # ==========================================================

    elif job.entity_type == ImportJob.ENTITY_INVENTORY:
        rules = [
            # SKU
            (
                (
                    "sku es obligatorio",
                    "sku obligatorio",
                    "sku no existe",
                    "no existe en el catálogo",
                    "no existe en el catalogo",
                    "sku está inactivo",
                    "sku esta inactivo",
                    "combo virtual",
                    "no puede recibir stock directo",
                    "aparece más de una vez",
                    "aparece mas de una vez",
                ),
                "inventory.sku",
            ),
            # Cantidad
            (
                (
                    "cantidad es obligatoria",
                    "cantidad obligatoria",
                    "cantidad debe ser un número entero",
                    "cantidad debe ser un numero entero",
                    "cantidad debe ser mayor que cero",
                    "unidades físicas",
                    "unidades fisicas",
                    "ya tiene",
                    "cantidad del archivo",
                    "entrada adicional",
                ),
                "inventory.quantity",
            ),
            # Referencia
            (
                (
                    "referencia",
                    "folio",
                    "documento",
                ),
                "inventory.reference",
            ),
            # Notas
            (
                (
                    "notas",
                    "nota",
                    "observaciones",
                    "comentarios",
                ),
                "inventory.notes",
            ),
        ]

    # ==========================================================
    # PROVEEDORES
    # ==========================================================

    elif job.entity_type == ImportJob.ENTITY_SUPPLIERS:
        rules = [
            (
                (
                    "nombre del proveedor",
                    "nombre es obligatorio",
                    "nombre repetido",
                ),
                "supplier.name",
            ),
            (
                (
                    "razón social",
                    "razon social",
                ),
                "supplier.legal_name",
            ),
            (
                ("rfc",),
                "supplier.rfc",
            ),
            (
                (
                    "correo",
                    "email",
                    "correo electrónico",
                    "correo electronico",
                ),
                "supplier.email",
            ),
            (
                (
                    "teléfono",
                    "telefono",
                ),
                "supplier.phone",
            ),
            (
                ("whatsapp",),
                "supplier.whatsapp",
            ),
            (
                (
                    "días de crédito",
                    "dias de credito",
                    "crédito",
                    "credito",
                ),
                "supplier.credit_days",
            ),
            (
                (
                    "dirección",
                    "direccion",
                ),
                "supplier.address",
            ),
            (
                (
                    "país",
                    "pais",
                ),
                "supplier.country",
            ),
            (
                ("notas",),
                "supplier.notes",
            ),
        ]

    # ==========================================================
    # CLIENTES
    # ==========================================================

    elif job.entity_type == ImportJob.ENTITY_CUSTOMERS:
        rules = [
            (
                (
                    "nombre del cliente",
                    "nombre completo",
                    "razón social",
                    "razon social",
                    "nombre es obligatorio",
                ),
                "customer.full_name",
            ),
            (
                ("nombre comercial",),
                "customer.business_name",
            ),
            (
                ("rfc",),
                "customer.rfc",
            ),
            (
                (
                    "correo",
                    "email",
                    "correo electrónico",
                    "correo electronico",
                ),
                "customer.email",
            ),
            (
                (
                    "teléfono",
                    "telefono",
                ),
                "customer.phone",
            ),
            (
                ("whatsapp",),
                "customer.whatsapp",
            ),
            (
                ("tipo de cliente",),
                "customer.customer_type",
            ),
            (
                ("origen",),
                "customer.source",
            ),
            (
                (
                    "compra mensual",
                    "compra mensual estimada",
                ),
                "customer.estimated_monthly_purchase",
            ),
            (
                ("calle",),
                "address.street",
            ),
            (
                (
                    "número exterior",
                    "numero exterior",
                ),
                "address.exterior_number",
            ),
            (
                (
                    "número interior",
                    "numero interior",
                ),
                "address.interior_number",
            ),
            (
                ("colonia",),
                "address.neighborhood",
            ),
            (
                ("ciudad",),
                "address.city",
            ),
            (
                ("estado",),
                "address.state",
            ),
            (
                (
                    "código postal",
                    "codigo postal",
                    "cp",
                ),
                "address.postal_code",
            ),
            (
                (
                    "país",
                    "pais",
                ),
                "address.country",
            ),
        ]

    else:
        return ""

    # ==========================================================
    # BUSCAR COINCIDENCIA
    # ==========================================================

    for keywords, target in rules:
        if any(keyword in text for keyword in keywords):
            return target

    return ""


def _resolver_source_column(
    *,
    job,
    mapping_rows,
    message="",
    target_field="",
):
    """
    Obtiene la columna REAL del archivo Excel relacionada
    con un error o advertencia.

    Prioridad:

    1. Usa target_field estructurado cuando el analizador
       ya conoce exactamente qué campo falló.

       Ejemplo:
           product.sale_price
           variant.sku
           customer.email
           supplier.rfc

    2. Si el lote es antiguo y no tiene diagnóstico
       estructurado, usa _resolver_target_from_issue()
       como fallback por compatibilidad.

    Funciona con:
    - Productos
    - Clientes
    - Proveedores
    """

    target = str(target_field or "").strip()

    # ==========================================================
    # FALLBACK PARA LOTES ANTIGUOS
    # ==========================================================

    if not target:
        target = _resolver_target_from_issue(
            job=job,
            message=message,
        )

    if not target:
        return ""

    # ==========================================================
    # 1. BUSCAR EN ImportColumnMapping
    # ==========================================================

    for mapping in mapping_rows:
        mapping_target = str(mapping.target_field or "").strip()

        if mapping_target == target:
            return str(mapping.source_column or "").strip()

    # ==========================================================
    # 2. FALLBACK PARA PRODUCTOS
    #
    # Productos históricamente guarda el mapping principal
    # dentro de job.proposed_mapping.
    # ==========================================================

    proposed_mapping = (
        job.proposed_mapping
        if isinstance(
            job.proposed_mapping,
            dict,
        )
        else {}
    )

    headers = (
        job.detected_headers
        if isinstance(
            job.detected_headers,
            list,
        )
        else []
    )

    for source_index, mapped_target in proposed_mapping.items():
        if str(mapped_target or "").strip() != target:
            continue

        try:
            index = int(source_index)
        except (
            TypeError,
            ValueError,
        ):
            continue

        if index >= 0 and index < len(headers):
            return str(headers[index] or "").strip()

    return ""


@login_required(login_url="/admin/login/")
@require_POST
def resolve_row_issue(
    request,
    pk,
    row_pk,
):
    company = require_company(request)

    if not company:
        return redirect("data_imports:dashboard")

    with transaction.atomic():
        # ======================================================
        # 1. BLOQUEAR Y OBTENER EL LOTE DE ESTA EMPRESA
        # ======================================================

        job = get_object_or_404(
            ImportJob.objects.for_company(company).select_for_update(),
            pk=pk,
        )

        # ======================================================
        # 2. OBTENER LA FILA DEL MISMO LOTE / EMPRESA
        # ======================================================

        row = get_object_or_404(
            ImportRowResult.objects.for_company(company),
            pk=row_pk,
            job=job,
        )

        # ======================================================
        # 3. NO PERMITIR CAMBIOS EN LOTES COMPLETADOS
        # ======================================================

        if job.status == ImportJob.STATUS_COMPLETED:
            messages.error(
                request,
                ("Este lote ya fue importado " "y no admite correcciones."),
            )

            return redirect(
                "data_imports:job_detail",
                pk=job.pk,
            )

        # ======================================================
        # 4. TIPOS DE IMPORTACIÓN SOPORTADOS
        # ======================================================

        if job.entity_type not in {
            ImportJob.ENTITY_CUSTOMERS,
            ImportJob.ENTITY_SUPPLIERS,
            ImportJob.ENTITY_PRODUCTS,
            ImportJob.ENTITY_INVENTORY,
        }:
            messages.error(
                request,
                ("Esta herramienta no admite " "este tipo de importación."),
            )

            return redirect(
                "data_imports:job_detail",
                pk=job.pk,
            )

        # ======================================================
        # 5. VALIDAR BODEGA DEL LOTE DE INVENTARIO
        #
        # Para clientes / proveedores / productos no aplica.
        # Para inventario sí exigimos la misma bodega con la
        # que se creó el lote.
        # ======================================================

        inventory_warehouse = None

        if job.entity_type == ImportJob.ENTITY_INVENTORY:
            try:
                inventory_warehouse = _get_inventory_job_warehouse(
                    request=request,
                    job=job,
                )

            except ValidationError as error:
                messages.error(
                    request,
                    str(error),
                )

                return redirect(
                    "data_imports:job_detail",
                    pk=job.pk,
                )

        # ======================================================
        # 6. ACCIÓN SELECCIONADA
        # ======================================================

        action = str(
            request.POST.get(
                "resolution_action",
                "",
            )
            or ""
        ).strip()

        # ======================================================
        # 7. LEER CAMBIOS GUARDADOS DEL LOTE
        # ======================================================

        options = (
            dict(job.options)
            if isinstance(
                job.options,
                dict,
            )
            else {}
        )

        corrections = dict(
            options.get(
                "row_corrections",
                {},
            )
        )

        skipped = {
            int(value)
            for value in options.get(
                "skipped_row_numbers",
                [],
            )
            if str(value).isdigit()
        }

        row_key = str(row.row_number)

        # ======================================================
        # 8. CORREGIR / VACIAR CAMPO
        # ======================================================

        if action in {
            "edit",
            "clear",
        }:
            source_column = str(
                request.POST.get(
                    "source_column",
                    "",
                )
                or ""
            ).strip()

            valid_headers = {
                str(header)
                for header in (
                    job.detected_headers
                    if isinstance(
                        job.detected_headers,
                        list,
                    )
                    else []
                )
            }

            if source_column not in valid_headers:
                messages.error(
                    request,
                    ("Selecciona una columna válida " "del archivo."),
                )

                return redirect(
                    "data_imports:job_detail",
                    pk=job.pk,
                )

            value = (
                ""
                if action == "clear"
                else str(
                    request.POST.get(
                        "corrected_value",
                        "",
                    )
                    or ""
                ).strip()
            )

            if len(value) > 5000:
                messages.error(
                    request,
                    ("El valor corregido no puede " "superar 5,000 caracteres."),
                )

                return redirect(
                    "data_imports:job_detail",
                    pk=job.pk,
                )

            row_changes = dict(
                corrections.get(
                    row_key,
                    {},
                )
            )

            row_changes[source_column] = value

            corrections[row_key] = row_changes

            # Si estaba omitida y el usuario la corrige,
            # vuelve a participar en la validación.
            skipped.discard(row.row_number)

            success_message = (
                f"La fila {row.row_number} " "se corrigió y volvió a validar."
            )

        # ======================================================
        # 9. OMITIR FILA
        # ======================================================

        elif action == "skip":
            skipped.add(row.row_number)

            success_message = (
                f"La fila {row.row_number} " "quedó omitida de la importación."
            )

        # ======================================================
        # 10. RESTAURAR VALORES ORIGINALES
        # ======================================================

        elif action == "restore":
            skipped.discard(row.row_number)

            corrections.pop(
                row_key,
                None,
            )

            success_message = (
                f"La fila {row.row_number} " "recuperó los valores del archivo."
            )

        # ======================================================
        # 11. ACCIÓN INVÁLIDA
        # ======================================================

        else:
            messages.error(
                request,
                ("La solución seleccionada " "no es válida."),
            )

            return redirect(
                "data_imports:job_detail",
                pk=job.pk,
            )

        # ======================================================
        # 12. GUARDAR CORRECCIONES
        # ======================================================

        options["row_corrections"] = corrections

        options["skipped_row_numbers"] = sorted(skipped)

        job.options = options

        job.save(
            update_fields=[
                "options",
                "updated_at",
            ]
        )

        # ======================================================
        # 13. REVALIDAR TODO EL LOTE
        # ======================================================

        try:
            _revalidate_job_with_saved_changes(
                job,
                request=request,
                inventory_warehouse=inventory_warehouse,
            )

        except (
            ImportFileError,
            ValidationError,
        ) as error:
            transaction.set_rollback(True)

            messages.error(
                request,
                str(error),
            )

            return redirect(
                "data_imports:job_detail",
                pk=job.pk,
            )

    # ==========================================================
    # 14. ÉXITO
    # ==========================================================

    messages.success(
        request,
        success_message,
    )

    return redirect(
        "data_imports:job_detail",
        pk=job.pk,
    )


@login_required(login_url="/admin/login/")
def download_customer_template(request):
    company = require_company(
        request
    )

    if not company:
        return redirect(
            "data_imports:dashboard"
        )

    workbook = Workbook()

    sheet = workbook.active
    sheet.title = "Clientes"

    headers = [
        "Nombre completo / Razón social",
        "Nombre comercial",
        "RFC",
        "Correo",
        "Teléfono",
        "WhatsApp",
        "Tipo de cliente",
        "Origen",
        "Compra mensual estimada",
        "Notas",
        "Tipo de dirección",
        "Calle",
        "Número exterior",
        "Número interior",
        "Colonia",
        "Ciudad",
        "Estado",
        "País",
        "Código postal",
        "Dirección principal",
        "Activo",
    ]

    example = [
        "Distribuidora Ejemplo SA de CV",
        "Distribuidora Ejemplo",
        "XAXX010101000",
        "compras@ejemplo.com",
        "5512345678",
        "525512345678",
        "Cliente",
        "Manual",
        25000,
        "Cliente migrado desde Excel",
        "Envío",
        "Av. Reforma",
        "123",
        "4B",
        "Centro",
        "Ciudad de México",
        "Ciudad de México",
        "México",
        "06000",
        "Sí",
        "Sí",
    ]

    sheet.append(
        headers
    )
    sheet.append(
        example
    )

    header_fill = PatternFill(
        "solid",
        fgColor="0F766E",
    )

    header_font = Font(
        color="FFFFFF",
        bold=True,
    )

    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(
            horizontal="center",
            vertical="center",
            wrap_text=True,
        )

    widths = {
        "A": 34,
        "B": 25,
        "C": 18,
        "D": 28,
        "E": 18,
        "F": 18,
        "G": 18,
        "H": 18,
        "I": 22,
        "J": 34,
        "K": 20,
        "L": 28,
        "M": 18,
        "N": 18,
        "O": 22,
        "P": 22,
        "Q": 24,
        "R": 16,
        "S": 18,
        "T": 20,
        "U": 12,
    }

    for column_letter, width in widths.items():
        sheet.column_dimensions[
            column_letter
        ].width = width

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = (
        f"A1:U{sheet.max_row}"
    )

    catalogs = workbook.create_sheet(
        "Catálogos"
    )

    catalogs.append(
        [
            "Tipos de cliente",
            "Origen",
            "Tipos de dirección",
            "Booleanos",
        ]
    )

    catalog_rows = [
        ["Lead", "WhatsApp", "Facturación", "Sí"],
        ["Prospecto", "Facebook", "Envío", "No"],
        ["Cliente", "Instagram", "Bodega cliente", ""],
        ["Cliente VIP", "TikTok", "", ""],
        ["Distribuidor", "Sitio web", "", ""],
        ["", "Recomendación", "", ""],
        ["", "Marketplace", "", ""],
        ["", "Manual", "", ""],
    ]

    for row in catalog_rows:
        catalogs.append(
            row
        )

    for cell in catalogs[1]:
        cell.fill = header_fill
        cell.font = header_font

    for column_letter in (
        "A",
        "B",
        "C",
        "D",
    ):
        catalogs.column_dimensions[
            column_letter
        ].width = 24

    customer_type_validation = DataValidation(
        type="list",
        formula1="'Catálogos'!$A$2:$A$6",
        allow_blank=True,
    )

    source_validation = DataValidation(
        type="list",
        formula1="'Catálogos'!$B$2:$B$9",
        allow_blank=True,
    )

    address_type_validation = DataValidation(
        type="list",
        formula1="'Catálogos'!$C$2:$C$4",
        allow_blank=True,
    )

    boolean_validation = DataValidation(
        type="list",
        formula1="'Catálogos'!$D$2:$D$3",
        allow_blank=True,
    )

    sheet.add_data_validation(
        customer_type_validation
    )
    sheet.add_data_validation(
        source_validation
    )
    sheet.add_data_validation(
        address_type_validation
    )
    sheet.add_data_validation(
        boolean_validation
    )

    customer_type_validation.add(
        "G2:G5000"
    )
    source_validation.add(
        "H2:H5000"
    )
    address_type_validation.add(
        "K2:K5000"
    )
    boolean_validation.add(
        "T2:T5000"
    )
    boolean_validation.add(
        "U2:U5000"
    )

    output = BytesIO()
    workbook.save(
        output
    )
    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )

    response[
        "Content-Disposition"
    ] = (
        'attachment; filename="plantilla_clientes_wms360.xlsx"'
    )

    return response


# ---------------------------------------------------------------------------
# IMPORTACIÓN DE PROVEEDORES
# ---------------------------------------------------------------------------

@login_required(login_url="/admin/login/")
def upload_suppliers(request):
    company = require_company(request)
    if not company:
        return redirect("data_imports:dashboard")
    form = SupplierImportUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        uploaded = form.cleaned_data["original_file"]
        extension = Path(uploaded.name).suffix.lower()
        job = ImportJob.objects.create(
            company=company, created_by=request.user,
            entity_type=ImportJob.ENTITY_SUPPLIERS,
            status=ImportJob.STATUS_UPLOADED,
            original_file=uploaded, original_filename=uploaded.name,
            file_extension=extension, file_size=uploaded.size,
            duplicate_policy=form.cleaned_data["duplicate_policy"],
        )
        try:
            sheets = list_sheet_names(job.original_file, extension)
            data = read_tabular_file(
                job.original_file, extension,
                sheet_name=sheets[0] if sheets else "",
            )
            mapping = suggest_supplier_mapping(data["headers"], company=company)
            job.sheet_name = data["sheet_name"]
            job.detected_headers = data["headers"]
            job.proposed_mapping = mapping
            job.total_rows = len(data["rows"])
            job.status = ImportJob.STATUS_MAPPING
            job.save()
            persist_supplier_mapping(
                job=job, headers=data["headers"], mapping=mapping
            )
        except ImportFileError as error:
            job.status = ImportJob.STATUS_FAILED
            job.error_summary = str(error)
            job.save()
            messages.error(request, str(error))
            return redirect("data_imports:job_detail", pk=job.pk)
        messages.success(request, "Archivo cargado. Revisa las columnas.")
        return redirect("data_imports:supplier_mapping", pk=job.pk)
    return render(request, "data_imports/upload_suppliers.html", {
        "company": company, "form": form,
    })


@login_required(login_url="/admin/login/")
def supplier_mapping(request, pk):
    company = require_company(request)
    job = get_company_job(company=company, pk=pk)
    if job.entity_type != ImportJob.ENTITY_SUPPLIERS:
        messages.error(request, "Esta importación no corresponde a proveedores.")
        return redirect("data_imports:job_detail", pk=job.pk)
    if job.status == ImportJob.STATUS_COMPLETED:
        messages.warning(request, "Este lote ya fue importado.")
        return redirect("data_imports:job_detail", pk=job.pk)
    try:
        sheets = list_sheet_names(job.original_file, job.file_extension)
    except ImportFileError as error:
        messages.error(request, str(error))
        return redirect("data_imports:job_detail", pk=job.pk)

    if request.method == "POST" and request.POST.get("action") == "change_sheet":
        sheet_form = ImportSheetForm(request.POST, sheet_names=sheets)
        if sheet_form.is_valid():
            data = read_tabular_file(
                job.original_file, job.file_extension,
                sheet_name=sheet_form.cleaned_data["sheet_name"],
            )
            mapping = suggest_supplier_mapping(data["headers"], company=company)
            job.sheet_name = data["sheet_name"]
            job.detected_headers = data["headers"]
            job.proposed_mapping = mapping
            job.total_rows = len(data["rows"])
            job.status = ImportJob.STATUS_MAPPING
            job.save()
            persist_supplier_mapping(
                job=job, headers=data["headers"], mapping=mapping
            )
        return redirect("data_imports:supplier_mapping", pk=job.pk)

    form = SupplierMappingForm(
        request.POST or None, headers=job.detected_headers, company=company,
        initial_mapping=get_supplier_mapping(job),
    )
    if request.method == "POST" and form.is_valid():
        mapping = form.get_mapping()
        persist_supplier_mapping(
            job=job, headers=job.detected_headers, mapping=mapping
        )
        job.proposed_mapping = mapping
        job.status = ImportJob.STATUS_VALIDATING
        job.error_summary = ""
        job.save()
        messages.success(request, "Relación de proveedores guardada.")
        return redirect("data_imports:job_detail", pk=job.pk)
    sheet_form = ImportSheetForm(
        initial={"sheet_name": job.sheet_name}, sheet_names=sheets
    )
    mapping_rows = [
        {"index": index, "header": header, "field": form[f"column_{index}"]}
        for index, header in enumerate(job.detected_headers)
    ]
    return render(request, "data_imports/supplier_mapping.html", {
        "company": company, "job": job, "sheet_form": sheet_form,
        "mapping_form": form, "mapping_rows": mapping_rows,
        "target_choices": form.target_choices,
    })


@login_required(login_url="/admin/login/")
@require_POST
def validate_suppliers(request, pk):
    company = require_company(request)
    job = get_company_job(company=company, pk=pk)
    if job.entity_type != ImportJob.ENTITY_SUPPLIERS:
        return redirect("data_imports:job_detail", pk=job.pk)
    mapping = get_supplier_mapping(job)
    if "supplier.name" not in mapping.values():
        messages.error(request, "Relaciona primero el nombre del proveedor.")
        return redirect("data_imports:supplier_mapping", pk=job.pk)
    try:
        data = load_job_file(job)
        counters = validate_supplier_rows(
            job=job,
            headers=data["headers"],
            rows=_rows_with_saved_corrections(job, data),
            mapping=mapping,
        )
        _apply_saved_skips(job, counters)
    except (ImportFileError, ValidationError) as error:
        job.status = ImportJob.STATUS_FAILED
        job.error_summary = str(error)
        job.save()
        messages.error(request, str(error))
        return redirect("data_imports:job_detail", pk=job.pk)
    for key, value in counters.items():
        setattr(job, key, value)
    importable = job.valid_rows + job.warning_rows
    if job.duplicate_rows and job.duplicate_policy == ImportJob.DUPLICATE_REVIEW:
        job.status = ImportJob.STATUS_RESOLVING_DUPLICATES
    elif job.duplicate_rows and job.duplicate_policy == ImportJob.DUPLICATE_STOP:
        job.status = ImportJob.STATUS_FAILED
        job.error_summary = "La importación se detuvo por duplicados."
    elif importable or (
        job.duplicate_rows and job.duplicate_policy in {
            ImportJob.DUPLICATE_UPDATE, ImportJob.DUPLICATE_CREATE
        }
    ):
        job.status = ImportJob.STATUS_READY
        job.error_summary = ""
    else:
        job.status = ImportJob.STATUS_FAILED
        job.error_summary = "Ninguna fila quedó disponible para importar."
    job.save()
    messages.success(request, "Análisis de proveedores terminado.")
    return redirect("data_imports:job_detail", pk=job.pk)


@login_required(login_url="/admin/login/")
def supplier_duplicates(request, pk):
    company = require_company(request)
    job = get_company_job(company=company, pk=pk)
    rows = ImportRowResult.objects.for_company(company).filter(
        job=job, status=ImportRowResult.STATUS_DUPLICATE
    ).order_by("row_number")
    return render(request, "data_imports/supplier_duplicates.html", {
        "company": company, "job": job, "rows": rows,
    })


@login_required(login_url="/admin/login/")
@require_POST
def save_supplier_duplicates(request, pk):
    company = require_company(request)
    job = get_company_job(company=company, pk=pk)
    rows = ImportRowResult.objects.for_company(company).filter(
        job=job, status=ImportRowResult.STATUS_DUPLICATE
    )
    unresolved = 0
    for row in rows:
        resolution = request.POST.get(f"resolution_{row.pk}", "")
        if resolution not in {"skip", "update_address", "create_new"}:
            unresolved += 1
            continue
        row.duplicate_resolution = resolution
        row.duplicate_resolved_by = request.user
        row.duplicate_resolved_at = timezone.now()
        row.save()
    job.status = (
        ImportJob.STATUS_RESOLVING_DUPLICATES
        if unresolved else ImportJob.STATUS_READY
    )
    job.save(update_fields=["status", "updated_at"])
    messages.success(request, "Decisiones de duplicados guardadas.")
    return redirect("data_imports:supplier_duplicates", pk=job.pk)


@login_required(login_url="/admin/login/")
@require_POST
def confirm_supplier_import(request, pk):
    company = require_company(request)
    job = get_company_job(company=company, pk=pk)
    if job.entity_type != ImportJob.ENTITY_SUPPLIERS:
        return redirect("data_imports:job_detail", pk=job.pk)
    unresolved = (
        ImportRowResult.objects.for_company(company)
        .filter(
            job=job,
            status=ImportRowResult.STATUS_DUPLICATE,
            duplicate_resolution="",
        )
        .exists()
    )
    if job.duplicate_policy == ImportJob.DUPLICATE_REVIEW and unresolved:
        messages.error(request, "Primero resuelve todos los duplicados.")
        return redirect("data_imports:supplier_duplicates", pk=job.pk)

    from .tasks import process_supplier_import_task

    ImportJob.objects.filter(company=company, pk=job.pk).update(
        status=ImportJob.STATUS_PROCESSING
    )
    process_supplier_import_task.delay(job.pk, request.user.pk)

    messages.success(
        request,
        "Tu importación se está procesando en segundo plano. "
        "Esta página se actualizará con el resultado en unos momentos.",
    )
    return redirect("data_imports:job_detail", pk=job.pk)


@login_required(login_url="/admin/login/")
def download_supplier_template(request):
    require_company(request)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "PROVEEDORES"
    headers = [
        "Nombre proveedor", "Razón social", "RFC", "Persona de contacto",
        "Correo", "Teléfono", "WhatsApp", "Dirección",
        "Ciudad / Municipio", "Estado", "País", "Condiciones de pago",
        "Días de crédito", "Notas", "Activo",
    ]
    sheet.append(headers)
    sheet.append([
        "Distribuidora Ejemplo", "Distribuidora Ejemplo SA de CV",
        "DEX010101AA1", "Ana López", "compras@ejemplo.mx", "5512345678",
        "5512345678", "Av. Reforma 100", "Cuauhtémoc", "CDMX",
        "México", "Crédito 30 días", 30, "Entrega los martes", "Sí",
    ])
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="0EA5E9")
    sheet.freeze_panes = "A2"
    for column in sheet.columns:
        sheet.column_dimensions[column[0].column_letter].width = 23
    output = BytesIO()
    workbook.save(output)
    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = (
        'attachment; filename="WMS360_PLANTILLA_PROVEEDORES.xlsx"'
    )
    return response


# ===========================================================================
# IMPORTACIÓN DE PRODUCTOS
# ===========================================================================


@login_required(login_url="accounts:login")
def upload_products(request):
    company = require_company(request)

    if not company:
        return redirect("data_imports:dashboard")

    form = ProductImportUploadForm(
        request.POST or None,
        request.FILES or None,
    )

    if request.method == "POST" and form.is_valid():
        uploaded_file = form.cleaned_data["original_file"]

        extension = Path(uploaded_file.name).suffix.lower()

        with transaction.atomic():
            job = ImportJob.objects.create(
                company=company,
                created_by=request.user,
                entity_type=ImportJob.ENTITY_PRODUCTS,
                status=ImportJob.STATUS_UPLOADED,
                original_file=uploaded_file,
                original_filename=uploaded_file.name,
                file_extension=extension,
                file_size=uploaded_file.size,
                duplicate_policy=(form.cleaned_data["duplicate_policy"]),
            )

            try:
                sheet_names = list_sheet_names(
                    job.original_file,
                    extension,
                )

                selected_sheet = sheet_names[0] if sheet_names else ""

                file_data = read_tabular_file(
                    job.original_file,
                    extension,
                    sheet_name=selected_sheet,
                )

                proposed_mapping = suggest_product_mapping(
                    file_data["headers"],
                    company=company,
                )

                job.sheet_name = file_data["sheet_name"]

                job.detected_headers = file_data["headers"]

                job.proposed_mapping = proposed_mapping

                job.total_rows = len(file_data["rows"])

                job.status = ImportJob.STATUS_MAPPING

                job.error_summary = ""

                job.save()

                persist_product_mapping(
                    job=job,
                    headers=file_data["headers"],
                    mapping=proposed_mapping,
                )

            except ImportFileError as error:
                job.status = ImportJob.STATUS_FAILED

                job.error_summary = str(error)

                job.save(
                    update_fields=[
                        "status",
                        "error_summary",
                        "updated_at",
                    ]
                )

                messages.error(
                    request,
                    str(error),
                )

                return redirect(
                    "data_imports:job_detail",
                    pk=job.pk,
                )

        messages.success(
            request,
            ("Archivo de productos cargado. " "Revisa la relación de columnas."),
        )

        return redirect(
            "data_imports:product_mapping",
            pk=job.pk,
        )

    return render(
        request,
        "data_imports/upload_products.html",
        {
            "company": company,
            "form": form,
        },
    )


@login_required(login_url="accounts:login")
def product_mapping(
    request,
    pk,
):
    company = require_company(request)

    if not company:
        return redirect("data_imports:dashboard")

    job = get_company_job(
        company=company,
        pk=pk,
    )

    if job.entity_type != ImportJob.ENTITY_PRODUCTS:
        messages.error(
            request,
            "Esta importación no corresponde a productos.",
        )

        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )

    if job.status == ImportJob.STATUS_COMPLETED:
        messages.warning(
            request,
            "Este lote ya fue importado.",
        )

        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )

    try:
        sheet_names = list_sheet_names(
            job.original_file,
            job.file_extension,
        )

    except ImportFileError as error:
        messages.error(
            request,
            str(error),
        )

        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )

    if request.method == "POST" and request.POST.get("action") == "change_sheet":
        sheet_form = ImportSheetForm(
            request.POST,
            sheet_names=sheet_names,
        )

        if sheet_form.is_valid():
            selected_sheet = sheet_form.cleaned_data["sheet_name"]

            try:
                file_data = read_tabular_file(
                    job.original_file,
                    job.file_extension,
                    sheet_name=selected_sheet,
                )

            except ImportFileError as error:
                messages.error(
                    request,
                    str(error),
                )

            else:
                proposed_mapping = suggest_product_mapping(
                    file_data["headers"],
                    company=company,
                )

                job.sheet_name = file_data["sheet_name"]

                job.detected_headers = file_data["headers"]

                job.proposed_mapping = proposed_mapping

                job.total_rows = len(file_data["rows"])

                job.status = ImportJob.STATUS_MAPPING

                job.error_summary = ""

                job.save()

                persist_product_mapping(
                    job=job,
                    headers=file_data["headers"],
                    mapping=proposed_mapping,
                )

                messages.success(
                    request,
                    (f'Hoja "{selected_sheet}" ' "seleccionada."),
                )

        return redirect(
            "data_imports:product_mapping",
            pk=job.pk,
        )

    mapping_form = ProductMappingForm(
        request.POST or None,
        headers=job.detected_headers,
        initial_mapping=(get_product_mapping(job)),
        company=company,
    )

    if request.method == "POST" and mapping_form.is_valid():
        mapping = mapping_form.get_mapping()

        persist_product_mapping(
            job=job,
            headers=job.detected_headers,
            mapping=mapping,
        )

        job.status = ImportJob.STATUS_VALIDATING

        job.error_summary = ""

        job.save(
            update_fields=[
                "status",
                "error_summary",
                "updated_at",
            ]
        )

        messages.success(
            request,
            ("Relación de productos guardada. " "Ahora valida el archivo."),
        )

        return redirect(
            "data_imports:product_mapping",
            pk=job.pk,
        )

    sheet_form = ImportSheetForm(
        initial={
            "sheet_name": job.sheet_name,
        },
        sheet_names=sheet_names,
    )

    mapping_rows = [
        {
            "index": index,
            "header": header,
            "field": mapping_form[f"column_{index}"],
        }
        for index, header in enumerate(job.detected_headers)
    ]

    validation_summary = {}

    if isinstance(job.options, dict):
        validation_summary = job.options.get(
            "product_validation",
            {},
        )

    return render(
        request,
        "data_imports/product_mapping.html",
        {
            "company": company,
            "job": job,
            "sheet_form": sheet_form,
            "mapping_form": mapping_form,
            "mapping_rows": mapping_rows,
            "validation_summary": validation_summary,
        },
    )


@login_required(login_url="accounts:login")
@require_POST
def validate_products(
    request,
    pk,
):
    company = require_company(request)

    if not company:
        return redirect("data_imports:dashboard")

    job = get_company_job(
        company=company,
        pk=pk,
    )

    if job.entity_type != ImportJob.ENTITY_PRODUCTS:
        messages.error(
            request,
            "Esta importación no corresponde a productos.",
        )

        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )

    mapping = get_product_mapping(job)

    if "product.name" not in mapping.values():
        messages.error(
            request,
            "Relaciona primero la columna Producto · Nombre.",
        )

        return redirect(
            "data_imports:product_mapping",
            pk=job.pk,
        )

    if "variant.sku" not in mapping.values():
        messages.error(
            request,
            "Relaciona primero la columna Variante · SKU.",
        )

        return redirect(
            "data_imports:product_mapping",
            pk=job.pk,
        )

    try:
        file_data = load_job_file(job)

        rows = _rows_with_saved_corrections(
            job,
            file_data,
        )

        counters = validate_product_rows(
            job=job,
            headers=file_data["headers"],
            rows=rows,
            mapping=mapping,
        )

        _apply_saved_skips(
            job,
            counters,
        )

        _set_job_validation_result(
            job,
            counters,
        )

    except (
        ImportFileError,
        ValidationError,
    ) as error:
        job.status = ImportJob.STATUS_FAILED

        job.error_summary = str(error)

        job.save(
            update_fields=[
                "status",
                "error_summary",
                "updated_at",
            ]
        )

        messages.error(
            request,
            str(error),
        )

        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )

    if counters["error_rows"]:
        messages.warning(
            request,
            (
                "El análisis encontró registros "
                "que requieren atención. "
                "Puedes corregirlos directamente "
                "desde WMS360."
            ),
        )

    else:
        messages.success(
            request,
            ("Análisis de productos terminado. " "El lote está listo para continuar."),
        )

    return redirect(
        "data_imports:job_detail",
        pk=job.pk,
    )


def confirm_product_import(
    request,
    pk,
):
    company = require_company(request)
    if not company:
        return redirect("data_imports:dashboard")
    job = get_company_job(
        company=company,
        pk=pk,
    )
    # ==========================================================
    # SEGURIDAD: SOLO LOTES DE PRODUCTOS
    # ==========================================================
    if job.entity_type != ImportJob.ENTITY_PRODUCTS:
        messages.error(
            request,
            "Esta importación no corresponde a productos.",
        )
        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )
    # ==========================================================
    # EL LOTE DEBE ESTAR LISTO
    # ==========================================================
    if job.status != ImportJob.STATUS_READY:
        messages.error(
            request,
            ("Debes validar correctamente el archivo " "antes de importarlo."),
        )
        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )

    from .tasks import process_product_import_task

    ImportJob.objects.filter(company=company, pk=job.pk).update(
        status=ImportJob.STATUS_PROCESSING
    )
    process_product_import_task.delay(job.pk, request.user.pk)

    messages.success(
        request,
        "Tu importación se está procesando en segundo plano. "
        "Esta página se actualizará con el resultado en unos momentos.",
    )
    return redirect(
        "data_imports:job_detail",
        pk=job.pk,
    )


@login_required(login_url="accounts:login")
def download_product_template(request):
    company = require_company(request)

    if not company:
        return redirect("data_imports:dashboard")

    workbook = Workbook()

    sheet = workbook.active
    sheet.title = "Productos"

    headers = [
        "Nombre",
        "Descripción",
        "Categoría",
        "Marca",
        "SKU base",
        "SKU",
        "Código de barras",
        "Color",
        "Talla",
        "Presentación",
        "Costo",
        "Precio venta",
        "Precio mayoreo",
        "Stock mínimo",
    ]

    sheet.append(headers)

    example = [
        "Camiseta WMS360",
        "Camiseta algodón premium",
        "Ropa",
        "WMS360",
        "CAM-001",
        "CAM-001-NEG-M",
        "7501234567890",
        "Negro",
        "M",
        "Pieza",
        150,
        299,
        250,
        5,
    ]

    sheet.append(example)

    header_fill = PatternFill(
        fill_type="solid",
        fgColor="142A43",
    )

    header_font = Font(
        color="FFFFFF",
        bold=True,
    )

    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = header_font

        cell.alignment = Alignment(
            horizontal="center",
            vertical="center",
        )

    widths = {
        "A": 28,
        "B": 36,
        "C": 22,
        "D": 22,
        "E": 18,
        "F": 22,
        "G": 22,
        "H": 16,
        "I": 14,
        "J": 18,
        "K": 16,
        "L": 16,
        "M": 18,
        "N": 16,
    }

    for column, width in widths.items():
        sheet.column_dimensions[column].width = width

    sheet.freeze_panes = "A2"

    guide = workbook.create_sheet("Instrucciones")

    guide.append(
        [
            "Campo",
            "Descripción",
            "Obligatorio",
        ]
    )

    instructions = [
        (
            "Nombre",
            "Nombre comercial del producto.",
            "Sí",
        ),
        (
            "SKU",
            ("SKU único de la variante " "dentro de tu empresa."),
            "Sí",
        ),
        (
            "SKU base",
            (
                "Identificador general del producto. "
                "Útil cuando existen varias variantes."
            ),
            "No",
        ),
        (
            "Categoría",
            ("Si no existe en tu empresa, " "WMS360 la creará."),
            "No",
        ),
        (
            "Marca",
            ("Si no existe en tu empresa, " "WMS360 la creará."),
            "No",
        ),
        (
            "Costo",
            "Costo unitario.",
            "No",
        ),
        (
            "Precio venta",
            "Precio regular de venta.",
            "No",
        ),
        (
            "Precio mayoreo",
            "Precio comercial mayorista.",
            "No",
        ),
        (
            "Stock mínimo",
            "Nivel mínimo recomendado.",
            "No",
        ),
    ]

    for row in instructions:
        guide.append(row)

    for cell in guide[1]:
        cell.fill = header_fill
        cell.font = header_font

    guide.column_dimensions["A"].width = 22
    guide.column_dimensions["B"].width = 65
    guide.column_dimensions["C"].width = 16

    output = BytesIO()

    workbook.save(output)

    output.seek(0)

    response = HttpResponse(
        output.getvalue(),
        content_type=(
            "application/" "vnd.openxmlformats-officedocument." "spreadsheetml.sheet"
        ),
    )

    response["Content-Disposition"] = (
        'attachment; filename="' 'WMS360_Plantilla_Productos.xlsx"'
    )

    return response


@login_required(login_url="/admin/login/")
def job_status(request, pk):
    """
    Endpoint ligero para polling desde el frontend: reporta el avance
    de una importacion en proceso sin necesidad de recargar la pagina
    completa. Usado por el modal de "Procesando tu importacion".
    """
    company = require_company(request)
    if not company:
        return JsonResponse({"ok": False, "error": "unauthorized"}, status=403)

    job = get_company_job(company=company, pk=pk)

    return JsonResponse(
        {
            "ok": True,
            "status": job.status,
            "total_rows": job.total_rows,
            "processed_count": job.processed_count,
            "created_count": job.created_count,
            "updated_count": job.updated_count,
            "skipped_count": job.skipped_count,
            "failed_count": job.failed_count,
        }
    )


@login_required(login_url="/admin/login/")
def job_detail(
    request,
    pk,
):
    company = require_company(request)

    if not company:
        return redirect("data_imports:dashboard")

    job = get_company_job(
        company=company,
        pk=pk,
    )

    # ==========================================================
    # FILTRO POR ESTADO
    # ==========================================================

    status_filter = str(
        request.GET.get(
            "status",
            "",
        )
        or ""
    ).strip()

    all_rows_qs = (
        ImportRowResult.objects.for_company(company)
        .filter(
            job=job,
        )
        .order_by("row_number")
    )

    valid_statuses = {choice[0] for choice in ImportRowResult.STATUS_CHOICES}

    filtered_rows_qs = all_rows_qs

    if status_filter in valid_statuses:
        filtered_rows_qs = filtered_rows_qs.filter(
            status=status_filter,
        )

    row_count = all_rows_qs.count()

    # ==========================================================
    # MAPEO DEL LOTE
    # ==========================================================

    mapping_rows = list(
        ImportColumnMapping.objects.for_company(company)
        .filter(
            job=job,
        )
        .order_by("source_index")
    )

    # ==========================================================
    # ETIQUETAS SEGÚN TIPO DE IMPORTACIÓN
    # ==========================================================

    if job.entity_type == ImportJob.ENTITY_SUPPLIERS:
        target_labels = dict(SUPPLIER_TARGET_CHOICES)

    elif job.entity_type == ImportJob.ENTITY_PRODUCTS:
        target_labels = dict(PRODUCT_TARGET_CHOICES)

    elif job.entity_type == ImportJob.ENTITY_INVENTORY:
        target_labels = dict(INVENTORY_TARGET_CHOICES)

    else:
        target_labels = dict(CUSTOMER_TARGET_CHOICES)

    # ==========================================================
    # RESUMEN DEL MAPEO
    # ==========================================================

    mapping_summary = []

    for mapping in mapping_rows:
        mapping_summary.append(
            {
                "source_column": (mapping.source_column),
                "source_index": (mapping.source_index),
                "target_field": (mapping.target_field),
                "target_label": (
                    target_labels.get(
                        mapping.target_field,
                        "Ignorar esta columna",
                    )
                    if mapping.target_field
                    else "Ignorar esta columna"
                ),
                "is_mapped": bool(mapping.target_field),
            }
        )

    # ==========================================================
    # PREPARAR FILAS PARA EL SOLUCIONADOR
    # ==========================================================

    display_rows = list(filtered_rows_qs[:100])

    for row in display_rows:

        # ======================================================
        # 1. OBTENER ERRORES Y ADVERTENCIAS
        # ======================================================

        issue_messages = []

        if isinstance(
            row.errors,
            list,
        ):
            issue_messages.extend(row.errors)

        if isinstance(
            row.warnings,
            list,
        ):
            issue_messages.extend(row.warnings)

        # ======================================================
        # 2. PROBLEMA PRINCIPAL
        # ======================================================

        primary_issue = ""

        for message in issue_messages:
            clean_message = str(message or "").strip()

            if clean_message:
                primary_issue = clean_message
                break

        # ======================================================
        # 3. LEER DIAGNÓSTICO ESTRUCTURADO
        # ======================================================

        normalized_data = (
            row.normalized_data
            if isinstance(
                row.normalized_data,
                dict,
            )
            else {}
        )

        diagnostics = normalized_data.get(
            "import_diagnostics",
            {},
        )

        if not isinstance(
            diagnostics,
            dict,
        ):
            diagnostics = {}

        error_details = diagnostics.get(
            "error_details",
            [],
        )

        warning_details = diagnostics.get(
            "warning_details",
            [],
        )

        if not isinstance(
            error_details,
            list,
        ):
            error_details = []

        if not isinstance(
            warning_details,
            list,
        ):
            warning_details = []

        # ======================================================
        # 4. DETERMINAR TARGET_FIELD EXACTO
        #
        # Ejemplos:
        #
        # product.sale_price
        # variant.sku
        # customer.email
        # supplier.rfc
        # inventory.sku
        # inventory.quantity
        # ======================================================

        resolver_target_field = ""

        structured_issues = error_details if error_details else warning_details

        # ------------------------------------------------------
        # Buscar diagnóstico cuyo mensaje coincida
        # exactamente con el problema mostrado.
        # ------------------------------------------------------

        for detail in structured_issues:

            if not isinstance(
                detail,
                dict,
            ):
                continue

            detail_message = str(
                detail.get(
                    "message",
                    "",
                )
                or ""
            ).strip()

            detail_target = str(
                detail.get(
                    "target_field",
                    "",
                )
                or ""
            ).strip()

            if primary_issue and detail_message and detail_message == primary_issue:
                resolver_target_field = detail_target

                break

        # ------------------------------------------------------
        # Si solo hay un diagnóstico usamos su target.
        # ------------------------------------------------------

        if (
            not resolver_target_field
            and len(structured_issues) == 1
            and isinstance(
                structured_issues[0],
                dict,
            )
        ):
            resolver_target_field = str(
                structured_issues[0].get(
                    "target_field",
                    "",
                )
                or ""
            ).strip()

        # ======================================================
        # 5. TARGET_FIELD -> COLUMNA REAL DEL EXCEL
        # ======================================================

        resolver_source_column = ""

        if resolver_target_field or primary_issue:
            resolver_source_column = _resolver_source_column(
                job=job,
                mapping_rows=mapping_rows,
                message=primary_issue,
                target_field=(resolver_target_field),
            )

        # ======================================================
        # 6. VALOR ACTUAL DE LA COLUMNA
        # ======================================================

        raw_data = (
            row.raw_data
            if isinstance(
                row.raw_data,
                dict,
            )
            else {}
        )

        resolver_current_value = ""

        if resolver_source_column:
            resolver_current_value = raw_data.get(
                resolver_source_column,
                "",
            )

        # ======================================================
        # 7. VARIABLES AUXILIARES PARA job_detail.html
        # ======================================================

        row.resolver_issue = primary_issue

        row.resolver_target_field = resolver_target_field

        row.resolver_source_column = resolver_source_column

        row.resolver_current_value = resolver_current_value

    # ==========================================================
    # RESUMEN DE OBSERVACIONES
    # ==========================================================

    issue_counter = Counter()

    coverage = {
        "rfc": 0,
        "email": 0,
        "phone": 0,
        "whatsapp": 0,
        "address": 0,
    }

    rows_with_contact = 0

    for row_data in all_rows_qs.values(
        "status",
        "normalized_data",
        "errors",
        "warnings",
    ).iterator(chunk_size=500):

        # ======================================================
        # CONTAR ERRORES
        # ======================================================

        for error_message in row_data.get("errors") or []:
            clean_message = str(error_message or "").strip()

            if clean_message:
                issue_counter[clean_message] += 1

        # ======================================================
        # CONTAR ADVERTENCIAS
        # ======================================================

        for warning_message in row_data.get("warnings") or []:
            clean_message = str(warning_message or "").strip()

            if clean_message:
                issue_counter[clean_message] += 1

        normalized_data = row_data.get("normalized_data") or {}

        # ======================================================
        # COBERTURA SOLO CLIENTES / PROVEEDORES
        # ======================================================

        if job.entity_type == ImportJob.ENTITY_SUPPLIERS:
            customer_data = normalized_data.get("supplier") or {}

        elif job.entity_type == ImportJob.ENTITY_CUSTOMERS:
            customer_data = normalized_data.get("customer") or {}

        else:
            customer_data = {}

        address_data = normalized_data.get("address") or {}

        if customer_data.get("rfc"):
            coverage["rfc"] += 1

        if customer_data.get("email"):
            coverage["email"] += 1

        if customer_data.get("phone"):
            coverage["phone"] += 1

        if customer_data.get("whatsapp"):
            coverage["whatsapp"] += 1

        if address_data or (
            job.entity_type == ImportJob.ENTITY_SUPPLIERS
            and customer_data.get("address")
        ):
            coverage["address"] += 1

        if any(
            (
                customer_data.get("email"),
                customer_data.get("phone"),
                customer_data.get("whatsapp"),
            )
        ):
            rows_with_contact += 1

    # ==========================================================
    # PROBLEMAS MÁS FRECUENTES
    # ==========================================================

    issue_summary = [
        {
            "message": message,
            "count": count,
        }
        for message, count in issue_counter.most_common(10)
    ]

    # ==========================================================
    # MÉTRICAS DEL LOTE
    # ==========================================================

    analyzed_rows = (
        job.valid_rows + job.warning_rows + job.duplicate_rows + job.error_rows
    )

    duplicate_importable = 0

    if job.duplicate_policy in {
        ImportJob.DUPLICATE_UPDATE,
        ImportJob.DUPLICATE_CREATE,
    }:
        duplicate_importable = job.duplicate_rows

    elif job.duplicate_policy == ImportJob.DUPLICATE_REVIEW:
        duplicate_importable = (
            duplicate_rows_queryset(job)
            .exclude(duplicate_resolution="")
            .exclude(duplicate_resolution=(ImportRowResult.RESOLUTION_SKIP))
            .count()
        )

    importable_rows = job.valid_rows + job.warning_rows + duplicate_importable

    # ==========================================================
    # DUPLICADOS PENDIENTES / RESUELTOS
    # ==========================================================

    duplicate_qs = duplicate_rows_queryset(job)

    duplicate_pending_count = duplicate_qs.filter(duplicate_resolution="").count()

    duplicate_resolved_count = duplicate_qs.exclude(duplicate_resolution="").count()

    # ==========================================================
    # PORCENTAJES
    # ==========================================================

    def percentage(
        value,
        total,
    ):
        if not total:
            return 0

        return round(
            (value / total) * 100,
            1,
        )

    # ==========================================================
    # COBERTURA
    # ==========================================================

    coverage_summary = {
        "rfc": percentage(
            coverage["rfc"],
            row_count,
        ),
        "email": percentage(
            coverage["email"],
            row_count,
        ),
        "phone": percentage(
            coverage["phone"],
            row_count,
        ),
        "whatsapp": percentage(
            coverage["whatsapp"],
            row_count,
        ),
        "address": percentage(
            coverage["address"],
            row_count,
        ),
    }

    # ==========================================================
    # DATOS ESPECÍFICOS DEL LOTE DE INVENTARIO
    # ==========================================================

    inventory_warehouse_id = None
    inventory_warehouse_name = ""

    if job.entity_type == ImportJob.ENTITY_INVENTORY:
        job_options = (
            job.options
            if isinstance(
                job.options,
                dict,
            )
            else {}
        )

        inventory_warehouse_id = job_options.get("inventory_warehouse_id")

        inventory_warehouse_name = str(
            job_options.get(
                "inventory_warehouse_name",
                "",
            )
            or ""
        ).strip()

    # ==========================================================
    # CONTEXTO
    # ==========================================================

    context = {
        "company": company,
        "job": job,
        # ------------------------------------------------------
        # Filas preparadas para solucionador
        # ------------------------------------------------------
        "rows": display_rows,
        "status_filter": (status_filter),
        "row_status_choices": (ImportRowResult.STATUS_CHOICES),
        "has_more_rows": (filtered_rows_qs.count() > 100),
        # ------------------------------------------------------
        # Mapping
        # ------------------------------------------------------
        "mapping_summary": (mapping_summary),
        "mapped_columns_count": sum(1 for item in mapping_summary if item["is_mapped"]),
        "ignored_columns_count": sum(
            1 for item in mapping_summary if not item["is_mapped"]
        ),
        # ------------------------------------------------------
        # Resumen de análisis
        # ------------------------------------------------------
        "issue_summary": (issue_summary),
        "coverage_summary": (coverage_summary),
        "rows_with_contact": (rows_with_contact),
        "analyzed_rows": (analyzed_rows),
        "importable_rows": (importable_rows),
        "row_count": (row_count),
        # ------------------------------------------------------
        # Duplicados
        # ------------------------------------------------------
        "duplicate_pending_count": (duplicate_pending_count),
        "duplicate_resolved_count": (duplicate_resolved_count),
        "has_duplicate_rows": bool(duplicate_pending_count or duplicate_resolved_count),
        # ------------------------------------------------------
        # Tipo de importación
        # ------------------------------------------------------
        "is_supplier_import": (job.entity_type == ImportJob.ENTITY_SUPPLIERS),
        "is_product_import": (job.entity_type == ImportJob.ENTITY_PRODUCTS),
        "is_customer_import": (job.entity_type == ImportJob.ENTITY_CUSTOMERS),
        "is_inventory_import": (job.entity_type == ImportJob.ENTITY_INVENTORY),
        # ------------------------------------------------------
        # Inventario / bodega
        # ------------------------------------------------------
        "inventory_warehouse_id": (inventory_warehouse_id),
        "inventory_warehouse_name": (inventory_warehouse_name),
        # ------------------------------------------------------
        # Porcentajes
        # ------------------------------------------------------
        "quality_percentage": percentage(
            importable_rows,
            analyzed_rows,
        ),
        "duplicate_percentage": percentage(
            job.duplicate_rows,
            analyzed_rows,
        ),
        "error_percentage": percentage(
            job.error_rows,
            analyzed_rows,
        ),
    }

    # ==========================================================
    # RENDER
    # ==========================================================

    return render(
        request,
        "data_imports/job_detail.html",
        context,
    )


@login_required(login_url="accounts:login")
def upload_inventory(request):
    company = require_company(request)

    if not company:
        return redirect("data_imports:dashboard")

    try:
        warehouse = require_active_warehouse(
            request=request,
            required_level=ACCESS_OPERATE,
        )
    except Exception as error:
        messages.error(request, str(error))
        return redirect("data_imports:dashboard")

    form = InventoryImportUploadForm(
        request.POST or None,
        request.FILES or None,
    )

    if request.method == "POST" and form.is_valid():
        uploaded_file = form.cleaned_data["original_file"]
        extension = Path(uploaded_file.name).suffix.lower()

        with transaction.atomic():
            job = ImportJob.objects.create(
                company=company,
                created_by=request.user,
                entity_type=ImportJob.ENTITY_INVENTORY,
                status=ImportJob.STATUS_UPLOADED,
                original_file=uploaded_file,
                original_filename=uploaded_file.name,
                file_extension=extension,
                file_size=uploaded_file.size,
                duplicate_policy=form.cleaned_data["duplicate_policy"],
                options={
                    "inventory_warehouse_id": warehouse.pk,
                    "inventory_warehouse_name": warehouse.name,
                },
            )

            try:
                sheet_names = list_sheet_names(
                    job.original_file,
                    extension,
                )

                selected_sheet = sheet_names[0] if sheet_names else ""

                file_data = read_tabular_file(
                    job.original_file,
                    extension,
                    sheet_name=selected_sheet,
                )

                proposed_mapping = suggest_inventory_mapping(file_data["headers"])

                job.sheet_name = file_data["sheet_name"]
                job.detected_headers = file_data["headers"]
                job.proposed_mapping = proposed_mapping
                job.total_rows = len(file_data["rows"])
                job.status = ImportJob.STATUS_MAPPING
                job.error_summary = ""
                job.save()

                persist_inventory_mapping(
                    job=job,
                    headers=file_data["headers"],
                    mapping=proposed_mapping,
                )

            except ImportFileError as error:
                job.status = ImportJob.STATUS_FAILED
                job.error_summary = str(error)

                job.save(
                    update_fields=[
                        "status",
                        "error_summary",
                        "updated_at",
                    ]
                )

                messages.error(request, str(error))

                return redirect(
                    "data_imports:job_detail",
                    pk=job.pk,
                )

        messages.success(
            request,
            (
                f"Archivo de inventario cargado para {warehouse.name}. "
                "Revisa la relación de columnas."
            ),
        )

        return redirect(
            "data_imports:inventory_mapping",
            pk=job.pk,
        )

    return render(
        request,
        "data_imports/upload_inventory.html",
        {
            "company": company,
            "warehouse": warehouse,
            "form": form,
        },
    )


@login_required(login_url="accounts:login")
def inventory_mapping(request, pk):
    company = require_company(request)

    if not company:
        return redirect("data_imports:dashboard")

    job = get_company_job(
        company=company,
        pk=pk,
    )

    if job.entity_type != ImportJob.ENTITY_INVENTORY:
        messages.error(
            request,
            "Esta importación no corresponde a inventario.",
        )

        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )

    if job.status == ImportJob.STATUS_COMPLETED:
        messages.warning(
            request,
            "Este lote ya fue importado.",
        )

        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )

    try:
        warehouse = _get_inventory_job_warehouse(
            request=request,
            job=job,
        )

        sheet_names = list_sheet_names(
            job.original_file,
            job.file_extension,
        )

    except (
        ImportFileError,
        ValidationError,
    ) as error:
        messages.error(request, str(error))

        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )

    if request.method == "POST" and request.POST.get("action") == "change_sheet":
        sheet_form = ImportSheetForm(
            request.POST,
            sheet_names=sheet_names,
        )

        if sheet_form.is_valid():
            selected_sheet = sheet_form.cleaned_data["sheet_name"]

            try:
                file_data = read_tabular_file(
                    job.original_file,
                    job.file_extension,
                    sheet_name=selected_sheet,
                )

            except ImportFileError as error:
                messages.error(request, str(error))

            else:
                proposed_mapping = suggest_inventory_mapping(file_data["headers"])

                job.sheet_name = file_data["sheet_name"]
                job.detected_headers = file_data["headers"]
                job.proposed_mapping = proposed_mapping
                job.total_rows = len(file_data["rows"])
                job.status = ImportJob.STATUS_MAPPING
                job.error_summary = ""
                job.save()

                persist_inventory_mapping(
                    job=job,
                    headers=file_data["headers"],
                    mapping=proposed_mapping,
                )

                messages.success(
                    request,
                    f'Hoja "{selected_sheet}" seleccionada.',
                )

        return redirect(
            "data_imports:inventory_mapping",
            pk=job.pk,
        )

    mapping_form = InventoryMappingForm(
        request.POST or None,
        headers=job.detected_headers,
        initial_mapping=get_inventory_mapping(job),
    )

    if request.method == "POST" and mapping_form.is_valid():
        mapping = mapping_form.get_mapping()

        persist_inventory_mapping(
            job=job,
            headers=job.detected_headers,
            mapping=mapping,
        )

        job.status = ImportJob.STATUS_VALIDATING
        job.error_summary = ""

        job.save(
            update_fields=[
                "status",
                "error_summary",
                "updated_at",
            ]
        )

        messages.success(
            request,
            "Relación de inventario guardada. Ahora valida el archivo.",
        )

        return redirect(
            "data_imports:inventory_mapping",
            pk=job.pk,
        )

    sheet_form = ImportSheetForm(
        initial={
            "sheet_name": job.sheet_name,
        },
        sheet_names=sheet_names,
    )

    mapping_rows = [
        {
            "index": index,
            "header": header,
            "field": mapping_form[f"column_{index}"],
        }
        for index, header in enumerate(job.detected_headers)
    ]

    return render(
        request,
        "data_imports/inventory_mapping.html",
        {
            "company": company,
            "warehouse": warehouse,
            "job": job,
            "sheet_form": sheet_form,
            "mapping_form": mapping_form,
            "mapping_rows": mapping_rows,
            "target_choices": mapping_form.target_choices,
        },
    )


@login_required(login_url="accounts:login")
@require_POST
def validate_inventory(request, pk):
    company = require_company(request)

    if not company:
        return redirect("data_imports:dashboard")

    job = get_company_job(
        company=company,
        pk=pk,
    )

    if job.entity_type != ImportJob.ENTITY_INVENTORY:
        messages.error(
            request,
            "Esta importación no corresponde a inventario.",
        )

        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )

    mapping = get_inventory_mapping(job)

    if "inventory.sku" not in mapping.values():
        messages.error(
            request,
            "Relaciona primero la columna Inventario · SKU.",
        )

        return redirect(
            "data_imports:inventory_mapping",
            pk=job.pk,
        )

    if "inventory.quantity" not in mapping.values():
        messages.error(
            request,
            "Relaciona primero la columna Inventario · Cantidad.",
        )

        return redirect(
            "data_imports:inventory_mapping",
            pk=job.pk,
        )

    try:
        warehouse = _get_inventory_job_warehouse(
            request=request,
            job=job,
        )

        file_data = load_job_file(job)

        rows = _rows_with_saved_corrections(
            job,
            file_data,
        )

        counters = validate_inventory_rows(
            job=job,
            warehouse=warehouse,
            headers=file_data["headers"],
            rows=rows,
            mapping=mapping,
        )

        _apply_saved_skips(
            job,
            counters,
        )

        _set_job_validation_result(
            job,
            counters,
        )

    except (
        ImportFileError,
        ValidationError,
    ) as error:
        job.status = ImportJob.STATUS_FAILED
        job.error_summary = str(error)

        job.save(
            update_fields=[
                "status",
                "error_summary",
                "updated_at",
            ]
        )

        messages.error(
            request,
            str(error),
        )

        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )

    if counters["error_rows"]:
        messages.warning(
            request,
            "El análisis encontró filas de inventario que requieren atención.",
        )
    else:
        messages.success(
            request,
            "Análisis de inventario terminado.",
        )

    return redirect(
        "data_imports:job_detail",
        pk=job.pk,
    )


def confirm_inventory_import(request, pk):
    company = require_company(request)
    if not company:
        return redirect("data_imports:dashboard")
    job = get_company_job(
        company=company,
        pk=pk,
    )
    if job.entity_type != ImportJob.ENTITY_INVENTORY:
        messages.error(
            request,
            "Esta importación no corresponde a inventario.",
        )
        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )
    if job.status != ImportJob.STATUS_READY:
        messages.error(
            request,
            "Debes validar correctamente el archivo antes de importarlo.",
        )
        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )

    try:
        warehouse = _get_inventory_job_warehouse(
            request=request,
            job=job,
        )
    except ValidationError as error:
        messages.error(request, str(error))
        return redirect(
            "data_imports:job_detail",
            pk=job.pk,
        )

    from .tasks import process_inventory_import_task

    ImportJob.objects.filter(company=company, pk=job.pk).update(
        status=ImportJob.STATUS_PROCESSING
    )
    process_inventory_import_task.delay(job.pk, request.user.pk, warehouse.pk)

    messages.success(
        request,
        f"Tu importación de inventario para {warehouse.name} se está "
        "procesando en segundo plano. Esta página se actualizará con "
        "el resultado en unos momentos.",
    )
    return redirect(
        "data_imports:job_detail",
        pk=job.pk,
    )


@login_required(login_url="accounts:login")
def download_inventory_template(request):
    company = require_company(request)

    if not company:
        return redirect("data_imports:dashboard")

    workbook = Workbook()

    sheet = workbook.active
    sheet.title = "INVENTARIO"

    headers = [
        "SKU",
        "Cantidad",
        "Referencia",
        "Notas",
    ]

    sheet.append(headers)

    sheet.append(
        [
            "SKU-EJEMPLO-001",
            50,
            "Inventario inicial",
            "Conteo físico inicial",
        ]
    )

    for cell in sheet[1]:
        cell.font = Font(
            bold=True,
            color="FFFFFF",
        )

        cell.fill = PatternFill(
            "solid",
            fgColor="0EA5E9",
        )

        cell.alignment = Alignment(
            horizontal="center",
        )

    sheet.freeze_panes = "A2"

    widths = {
        "A": 28,
        "B": 16,
        "C": 28,
        "D": 45,
    }

    for column_letter, width in widths.items():
        sheet.column_dimensions[column_letter].width = width

    output = BytesIO()

    workbook.save(output)

    response = HttpResponse(
        output.getvalue(),
        content_type=(
            "application/vnd.openxmlformats-officedocument." "spreadsheetml.sheet"
        ),
    )

    response["Content-Disposition"] = (
        "attachment; " 'filename="WMS360_PLANTILLA_INVENTARIO_INICIAL.xlsx"'
    )

    return response
