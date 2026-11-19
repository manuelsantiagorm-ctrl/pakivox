from django.contrib import admin
from companies.scoping import CompanyScopedAdmin

from .models import (
    ImportChangeLog,
    ImportColumnMapping,
    ImportJob,
    ImportRowResult,
)


class ImportColumnMappingInline(admin.TabularInline):
    model = ImportColumnMapping
    extra = 0
    fields = (
        "source_index",
        "source_column",
        "target_field",
        "is_required",
        "confidence",
        "confirmed_by_user",
    )
    readonly_fields = fields
    can_delete = False


@admin.register(ImportJob)
class ImportJobAdmin(CompanyScopedAdmin):
    list_display = (
        "folio",
        "company",
        "entity_type",
        "status",
        "total_rows",
        "valid_rows",
        "error_rows",
        "created_count",
        "updated_count",
        "created_by",
        "created_at",
    )
    list_filter = (
        "company",
        "entity_type",
        "status",
        "duplicate_policy",
        "created_at",
    )
    search_fields = (
        "folio",
        "original_filename",
        "company__name",
        "created_by__username",
        "created_by__email",
    )
    readonly_fields = (
        "folio",
        "original_filename",
        "file_extension",
        "file_size",
        "detected_headers",
        "proposed_mapping",
        "total_rows",
        "valid_rows",
        "warning_rows",
        "error_rows",
        "duplicate_rows",
        "created_count",
        "updated_count",
        "skipped_count",
        "started_at",
        "finished_at",
        "reverted_at",
        "created_at",
        "updated_at",
    )
    autocomplete_fields = (
        "company",
        "created_by",
        "reverted_by",
    )
    inlines = [ImportColumnMappingInline]


@admin.register(ImportColumnMapping)
class ImportColumnMappingAdmin(CompanyScopedAdmin):
    list_display = (
        "job",
        "source_index",
        "source_column",
        "target_field",
        "confidence",
        "confirmed_by_user",
    )
    list_filter = (
        "company",
        "confirmed_by_user",
        "is_required",
    )
    search_fields = (
        "job__folio",
        "source_column",
        "target_field",
    )


@admin.register(ImportRowResult)
class ImportRowResultAdmin(CompanyScopedAdmin):
    list_display = (
        "job",
        "row_number",
        "status",
        "matched_model",
        "matched_object_id",
        "action_taken",
    )
    list_filter = ("company", "status")
    search_fields = (
        "job__folio",
        "matched_model",
        "matched_object_id",
    )
    readonly_fields = (
        "raw_data",
        "normalized_data",
        "errors",
        "warnings",
        "created_at",
        "updated_at",
    )


@admin.register(ImportChangeLog)
class ImportChangeLogAdmin(CompanyScopedAdmin):
    list_display = (
        "job",
        "action",
        "model_label",
        "object_pk",
        "reverted",
        "created_at",
    )
    list_filter = ("company", "action", "reverted")
    search_fields = (
        "job__folio",
        "model_label",
        "object_pk",
    )
    readonly_fields = (
        "before_data",
        "after_data",
        "created_at",
    )
