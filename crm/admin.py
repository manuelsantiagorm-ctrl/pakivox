from django.contrib import admin

from companies.scoping import CompanyScopedAdmin

from .models import (
    Activity,
    Customer,
    CustomerAddress,
    CustomerTag,
    Deal,
    DealActivity,
    DealComment,
    DealNote,
    PipelineStage,
    Tag,
)


@admin.register(Customer)
class CustomerAdmin(CompanyScopedAdmin):
    list_display = (
        "full_name",
        "business_name",
        "company",
        "customer_type",
        "source",
        "phone",
        "whatsapp",
        "assigned_to",
        "is_active",
    )
    search_fields = (
        "full_name",
        "business_name",
        "email",
        "phone",
        "whatsapp",
        "company__name",
    )
    list_filter = (
        "company",
        "customer_type",
        "source",
        "is_active",
    )


@admin.register(PipelineStage)
class PipelineStageAdmin(CompanyScopedAdmin):
    readonly_fields = ()
    list_display = (
        "name",
        "company",
        "order",
        "is_won",
        "is_lost",
        "is_active",
    )
    search_fields = (
        "name",
        "company__name",
    )
    list_filter = (
        "company",
        "is_active",
    )
    ordering = (
        "company",
        "order",
    )


@admin.register(Deal)
class DealAdmin(CompanyScopedAdmin):
    list_display = (
        "title",
        "customer",
        "company",
        "stage",
        "amount",
        "probability",
        "status",
        "expected_close_date",
        "assigned_to",
        "created_at",
    )
    search_fields = (
        "title",
        "customer__full_name",
        "company__name",
    )
    list_filter = (
        "company",
        "stage",
        "status",
        "assigned_to",
    )
    readonly_fields = (
        "created_at",
        "updated_at",
    )


@admin.register(CustomerAddress)
class CustomerAddressAdmin(CompanyScopedAdmin):
    list_display = (
        "customer",
        "company",
        "address_type",
        "city",
        "state",
        "zip_code",
        "is_default",
    )
    search_fields = (
        "customer__full_name",
        "city",
        "state",
        "zip_code",
    )
    list_filter = (
        "company",
        "address_type",
        "state",
        "is_default",
    )


@admin.register(Activity)
class ActivityAdmin(CompanyScopedAdmin):
    list_display = (
        "title",
        "company",
        "customer",
        "deal",
        "activity_type",
        "assigned_to",
        "due_date",
        "completed_at",
        "created_at",
    )
    search_fields = (
        "title",
        "customer__full_name",
        "company__name",
    )
    list_filter = (
        "company",
        "activity_type",
        "assigned_to",
        "completed_at",
    )
    readonly_fields = ("created_at",)


@admin.register(Tag)
class TagAdmin(CompanyScopedAdmin):
    readonly_fields = ()
    list_display = (
        "name",
        "company",
        "color",
    )
    search_fields = (
        "name",
        "company__name",
    )
    list_filter = ("company",)


@admin.register(CustomerTag)
class CustomerTagAdmin(CompanyScopedAdmin):
    readonly_fields = ()
    list_display = (
        "customer",
        "tag",
        "company",
    )
    search_fields = (
        "customer__full_name",
        "tag__name",
        "company__name",
    )
    list_filter = (
        "company",
        "tag",
    )


@admin.register(DealActivity)
class DealActivityAdmin(CompanyScopedAdmin):
    list_display = (
        "deal",
        "company",
        "activity_type",
        "user",
        "created_at",
    )
    search_fields = (
        "deal__title",
        "description",
        "company__name",
    )
    list_filter = (
        "company",
        "activity_type",
        "created_at",
    )
    readonly_fields = ("created_at",)


@admin.register(DealNote)
class DealNoteAdmin(CompanyScopedAdmin):
    list_display = (
        "deal",
        "company",
        "author",
        "created_at",
    )
    search_fields = (
        "deal__title",
        "content",
        "company__name",
    )
    list_filter = (
        "company",
        "created_at",
    )
    readonly_fields = ("created_at",)


@admin.register(DealComment)
class DealCommentAdmin(CompanyScopedAdmin):
    list_display = (
        "deal",
        "company",
        "user",
        "created_at",
    )
    search_fields = (
        "deal__title",
        "content",
        "company__name",
    )
    list_filter = (
        "company",
        "created_at",
    )
    readonly_fields = ("created_at",)
