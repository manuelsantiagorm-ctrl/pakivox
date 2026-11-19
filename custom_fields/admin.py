from django.contrib import admin
from companies.scoping import CompanyScopedAdmin

from .models import (
    CustomFieldDefinition,
    CustomerCustomFieldValue,
    SupplierCustomFieldValue,
)


@admin.register(CustomFieldDefinition)
class CustomFieldDefinitionAdmin(CompanyScopedAdmin):
    list_display = (
        "name",
        "company",
        "module",
        "field_type",
        "required",
        "allow_import",
        "is_active",
    )
    list_filter = ("company", "module", "field_type", "is_active")
    search_fields = ("name", "slug", "company__name")


@admin.register(CustomerCustomFieldValue)
class CustomerCustomFieldValueAdmin(CompanyScopedAdmin):
    list_display = ("customer", "definition", "company", "updated_at")
    list_filter = ("company", "definition")
    search_fields = ("customer__full_name", "definition__name")


@admin.register(SupplierCustomFieldValue)
class SupplierCustomFieldValueAdmin(CompanyScopedAdmin):
    list_display = ("supplier", "definition", "company", "updated_at")
    list_filter = ("company", "definition")
    search_fields = ("supplier__name", "definition__name")
