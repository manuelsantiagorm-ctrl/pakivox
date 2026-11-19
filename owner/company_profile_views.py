from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.db import transaction
from django.shortcuts import (
    get_object_or_404,
    redirect,
    render,
)

from companies.models import Company
from companies.profile_forms import CompanyProfileForm
from companies.profile_services import get_company_profile_context


def owner_required(user):
    return user.is_authenticated and user.is_superuser


@user_passes_test(owner_required)
def owner_company_profile(
    request,
    company_id,
):
    company = get_object_or_404(
        Company,
        pk=company_id,
    )

    context = get_company_profile_context(
        company
    )

    context["owner_mode"] = True

    return render(
        request,
        "owner/company_profile.html",
        context,
    )


@user_passes_test(owner_required)
@transaction.atomic
def owner_company_edit(
    request,
    company_id,
):
    company = get_object_or_404(
        Company.objects.select_for_update(),
        pk=company_id,
    )

    if request.method == "POST":
        form = CompanyProfileForm(
            request.POST,
            instance=company,
            actor=request.user,
        )

        if form.is_valid():
            form.save()

            messages.success(
                request,
                (
                    f'La empresa "{company.name}" '
                    "se actualizó correctamente."
                ),
            )

            return redirect(
                "owner:company_profile",
                company_id=company.pk,
            )

        messages.error(
            request,
            "Revisa los campos marcados antes de guardar.",
        )

    else:
        form = CompanyProfileForm(
            instance=company,
            actor=request.user,
        )

    return render(
        request,
        "owner/company_profile_edit.html",
        {
            "company": company,
            "form": form,
        },
    )
