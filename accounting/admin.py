from django.contrib import admin
from companies.scoping import CompanyScopedAdmin

from .models import (
    AccountingAccount,
    Expense,
    ExpenseCategory,
    JournalEntry,
    JournalLine,
)


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 0
    fields = [
        "account",
        "description",
        "debit",
        "credit",
    ]


@admin.register(AccountingAccount)
class AccountingAccountAdmin(CompanyScopedAdmin):
    list_display = [
        "code",
        "name",
        "company",
        "account_type",
        "is_system",
        "is_active",
    ]

    list_filter = [
        "company",
        "account_type",
        "is_system",
        "is_active",
    ]

    search_fields = [
        "code",
        "name",
        "company__name",
    ]


@admin.register(JournalEntry)
class JournalEntryAdmin(CompanyScopedAdmin):
    list_display = [
        "entry_date",
        "description",
        "company",
        "source",
        "status",
        "debit_total",
        "credit_total",
    ]

    list_filter = [
        "company",
        "source",
        "status",
        "entry_date",
    ]

    search_fields = [
        "description",
        "reference",
        "idempotency_key",
        "company__name",
    ]

    readonly_fields = [
        "posted_at",
        "created_at",
        "updated_at",
    ]

    inlines = [
        JournalLineInline,
    ]


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(CompanyScopedAdmin):
    list_display = [
        "name",
        "company",
        "expense_account",
        "is_active",
    ]

    list_filter = [
        "company",
        "is_active",
    ]

    search_fields = [
        "name",
        "company__name",
    ]


@admin.register(Expense)
class ExpenseAdmin(CompanyScopedAdmin):
    list_display = [
        "expense_date",
        "description",
        "company",
        "category",
        "amount",
        "status",
    ]

    list_filter = [
        "company",
        "category",
        "status",
        "expense_date",
    ]

    search_fields = [
        "description",
        "supplier_name",
        "reference",
        "company__name",
    ]

    readonly_fields = [
        "journal_entry",
        "reversal_entry",
        "posted_at",
        "cancelled_at",
        "created_at",
        "updated_at",
    ]
