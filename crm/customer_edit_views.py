from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import (
    get_object_or_404,
    redirect,
    render,
)

from .customer_forms import CustomerEditForm
from .models import Customer
from custom_fields.services import save_customer_custom_values


def get_request_company(request):
    profile = getattr(
        request.user,
        "profile",
        None,
    )

    company = getattr(
        profile,
        "company",
        None,
    )

    if company:
        return company

    return getattr(
        request.user,
        "company",
        None,
    )


@login_required
@transaction.atomic
def customer_edit(
    request,
    customer_id,
):
    company = get_request_company(request)

    if not company:
        return HttpResponseForbidden(
            "Tu usuario no tiene una empresa asignada."
        )

    customer = get_object_or_404(
        Customer.objects.select_for_update(),
        pk=customer_id,
        company=company,
    )

    if request.method == "POST":
        form = CustomerEditForm(
            request.POST,
            instance=customer,
            company=company,
        )

        if form.is_valid():
            try:
                updated_customer = form.save(
                    commit=False
                )

                updated_customer.company = company
                updated_customer.full_clean()
                updated_customer.save()

                custom_values = {
                    field_name.replace("custom_field_", "", 1): value
                    for field_name, value in form.cleaned_data.items()
                    if field_name.startswith("custom_field_")
                }
                save_customer_custom_values(
                    company=company,
                    customer=updated_customer,
                    values=custom_values,
                )

            except ValidationError as error:
                for message in error.messages:
                    form.add_error(
                        None,
                        message,
                    )

            else:
                messages.success(
                    request,
                    (
                        f'Cliente "{updated_customer.full_name}" '
                        "actualizado correctamente."
                    ),
                )

                return redirect(
                    "customer_smart_profile",
                    customer_id=updated_customer.pk,
                )

        messages.error(
            request,
            "Revisa los campos marcados antes de guardar.",
        )

    else:
        form = CustomerEditForm(
            instance=customer,
            company=company,
        )

    return render(
        request,
        "crm/customer_edit.html",
        {
            "company": company,
            "customer": customer,
            "form": form,
        },
    )


@login_required
@transaction.atomic
def customer_create(request):
    """
    Alta de cliente desde el CRM (antes el botón mandaba al admin de Django,
    que el dueño de una empresa no puede usar).

    Reutiliza el mismo formulario y las mismas validaciones que la edición:
    nombre real, teléfono de 10+ dígitos y sin duplicar RFC, correo, teléfono
    ni WhatsApp dentro de la misma empresa.
    """
    company = get_request_company(request)

    if not company:
        return HttpResponseForbidden(
            "Tu usuario no tiene una empresa asignada."
        )

    customer = Customer(company=company)

    if request.method == "POST":
        form = CustomerEditForm(
            request.POST,
            instance=customer,
            company=company,
        )

        if form.is_valid():
            try:
                new_customer = form.save(commit=False)
                new_customer.company = company
                new_customer.full_clean()
                new_customer.save()

                custom_values = {
                    field_name.replace("custom_field_", "", 1): value
                    for field_name, value in form.cleaned_data.items()
                    if field_name.startswith("custom_field_")
                }
                save_customer_custom_values(
                    company=company,
                    customer=new_customer,
                    values=custom_values,
                )

            except ValidationError as error:
                for message in error.messages:
                    form.add_error(None, message)

            else:
                messages.success(
                    request,
                    f'Cliente "{new_customer.full_name}" creado correctamente.',
                )
                return redirect(
                    "customer_smart_profile",
                    customer_id=new_customer.pk,
                )

        messages.error(
            request,
            "Revisa los campos marcados antes de guardar.",
        )

    else:
        form = CustomerEditForm(
            instance=customer,
            company=company,
            initial={"is_active": True},
        )

    return render(
        request,
        "crm/customer_edit.html",
        {
            "company": company,
            "customer": customer,
            "form": form,
            "es_nuevo": True,
        },
    )
