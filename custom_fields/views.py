from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounts.capability_access import capability_required
from .forms import CustomFieldDefinitionForm
from .models import CustomFieldDefinition


def _company(request):
    profile = getattr(request.user, "profile", None)
    company = getattr(profile, "company", None) or getattr(
        request.user,
        "company",
        None,
    )
    if company is None:
        raise PermissionDenied("Tu usuario no tiene una empresa asignada.")
    return company


@login_required
@capability_required("custom_fields.view")
def field_list(request):
    company = _company(request)
    fields = CustomFieldDefinition.objects.filter(company=company)
    return render(
        request,
        "custom_fields/field_list.html",
        {"company": company, "fields": fields},
    )


@login_required
@capability_required("custom_fields.manage")
@transaction.atomic
def field_create(request):
    company = _company(request)
    form = CustomFieldDefinitionForm(
        request.POST or None,
        company=company,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Campo personalizado creado.")
        return redirect("custom_fields:field_list")
    return render(
        request,
        "custom_fields/field_form.html",
        {"company": company, "form": form, "editing": False},
    )


@login_required
@capability_required("custom_fields.manage")
@require_POST
@transaction.atomic
def field_create_inline(request):
    """
    Crea un campo personalizado desde el importador
    sin abandonar la pantalla de mapeo.
    """
    company = _company(request)

    module = str(
        request.POST.get(
            "module",
            "customer",
        )
        or "customer"
    ).strip()

    allowed_modules = {
        CustomFieldDefinition.MODULE_CUSTOMER,
        CustomFieldDefinition.MODULE_SUPPLIER,
        CustomFieldDefinition.MODULE_PRODUCT,
    }

    if module not in allowed_modules:
        return JsonResponse(
            {
                "ok": False,
                "message": ("El módulo indicado no es válido."),
                "errors": {
                    "module": ["Módulo de campos personalizados no permitido."],
                },
            },
            status=400,
        )

    form = CustomFieldDefinitionForm(
        request.POST,
        company=company,
        module=module,
    )

    if not form.is_valid():
        errors = {
            field: [str(message) for message in messages_list]
            for field, messages_list in form.errors.items()
        }

        return JsonResponse(
            {
                "ok": False,
                "message": ("Revisa los datos del campo."),
                "errors": errors,
            },
            status=400,
        )

    definition = form.save(commit=False)

    # Seguridad:
    # company debe salir exclusivamente
    # del usuario autenticado.
    definition.company = company
    definition.module = module

    # Un campo creado desde el importador
    # debe quedar disponible ahí.
    definition.allow_import = True
    definition.is_active = True

    definition.save()

    return JsonResponse(
        {
            "ok": True,
            "id": definition.pk,
            "value": (f"custom.{definition.pk}"),
            "label": ("Campo personalizado · " f"{definition.name}"),
            "module": definition.module,
            "message": ("Campo personalizado creado."),
        },
        status=201,
    )


@login_required
@capability_required("custom_fields.manage")
@transaction.atomic
def field_edit(request, pk):
    company = _company(request)
    definition = get_object_or_404(
        CustomFieldDefinition.objects.select_for_update(),
        pk=pk,
        company=company,
    )
    form = CustomFieldDefinitionForm(
        request.POST or None,
        instance=definition,
        company=company,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Campo personalizado actualizado.")
        return redirect("custom_fields:field_list")
    return render(
        request,
        "custom_fields/field_form.html",
        {
            "company": company,
            "form": form,
            "definition": definition,
            "editing": True,
        },
    )


@login_required
@capability_required("custom_fields.manage")
@transaction.atomic
def field_toggle(request, pk):
    if request.method != "POST":
        raise PermissionDenied("Esta acción requiere POST.")
    company = _company(request)
    definition = get_object_or_404(
        CustomFieldDefinition.objects.select_for_update(),
        pk=pk,
        company=company,
    )
    definition.is_active = not definition.is_active
    definition.save(update_fields=["is_active", "updated_at"])
    messages.success(request, "Estado del campo actualizado.")
    return redirect("custom_fields:field_list")
