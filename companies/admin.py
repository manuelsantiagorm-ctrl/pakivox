from django.contrib import admin
from companies.scoping import CompanyScopedAdmin
from .models import BusinessLine, Company, CompanyBusinessLine


@admin.register(BusinessLine)
class BusinessLineAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "is_active",
        "created_at",
    )
    list_filter = (
        "is_active",
        "created_at",
    )
    search_fields = (
        "name",
        "slug",
        "description",
    )
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("name",)


class CompanyBusinessLineInline(admin.TabularInline):
    model = CompanyBusinessLine
    extra = 1
    fields = (
        "business_line",
        "is_primary",
        "created_by",
        "created_at",
    )
    readonly_fields = ("created_at",)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "rfc",
        "email",
        "phone",
        "is_active",
        "created_at",
    )
    list_filter = (
        "is_active",
        "created_at",
    )
    search_fields = (
        "name",
        "slug",
        "rfc",
        "email",
        "phone",
    )
    prepopulated_fields = {"slug": ("name",)}
    inlines = [
        CompanyBusinessLineInline,
    ]
    ordering = ("name",)


@admin.register(CompanyBusinessLine)
class CompanyBusinessLineAdmin(CompanyScopedAdmin): 
    readonly_fields = ()
    list_display = (
        "company",
        "business_line",
        "is_primary",
        "created_by",
        "created_at",
    )
    list_filter = (
        "business_line",
        "is_primary",
        "created_at",
    )
    search_fields = (
        "company__name",
        "business_line__name",
    )
    ordering = (
        "company__name",
        "business_line__name",
    )
