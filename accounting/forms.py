from django import forms
from django.utils import timezone

from .models import (
    AccountingAccount,
    Expense,
    ExpenseCategory,
)

RECONCILIATION_TARGET_CODES = ("3101", "3201")


class BalanceReconciliationForm(forms.Form):
    as_of_date = forms.DateField(
        label="Fecha de corte",
        widget=forms.DateInput(
            attrs={"type": "date", "class": "form-control"}
        ),
    )
    target_account = forms.ModelChoiceField(
        label="Cuenta de capital para conciliar",
        queryset=AccountingAccount.objects.none(),
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    explanation = forms.CharField(
        label="Justificación",
        min_length=10,
        max_length=255,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": (
                    "Explica el origen del saldo inicial o ajuste aprobado."
                ),
            }
        ),
    )

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company
        if company:
            self.fields["target_account"].queryset = (
                AccountingAccount.objects.filter(
                    company=company,
                    code__in=RECONCILIATION_TARGET_CODES,
                    account_type=AccountingAccount.TYPE_EQUITY,
                    is_active=True,
                ).order_by("code")
            )

    def clean_as_of_date(self):
        value = self.cleaned_data["as_of_date"]
        if value > timezone.localdate():
            raise forms.ValidationError(
                "La fecha de conciliación no puede estar en el futuro."
            )
        return value

    def clean_target_account(self):
        account = self.cleaned_data["target_account"]
        if not self.company or account.company_id != self.company.pk:
            raise forms.ValidationError(
                "La cuenta seleccionada pertenece a otra empresa."
            )
        return account


class ExpenseForm(forms.ModelForm):
    class Meta:
        model = Expense

        fields = [
            "expense_date",
            "category",
            "payment_account",
            "description",
            "supplier_name",
            "amount",
            "reference",
            "notes",
            "receipt_file",
        ]

        widgets = {
            "expense_date": forms.DateInput(
                attrs={
                    "type": "date",
                    "class": "form-control",
                }
            ),
            "category": forms.Select(
                attrs={
                    "class": "form-select",
                }
            ),
            "payment_account": forms.Select(
                attrs={
                    "class": "form-select",
                }
            ),
            "description": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Ejemplo: Renta de bodega",
                }
            ),
            "supplier_name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Proveedor o beneficiario",
                }
            ),
            "amount": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "step": "0.01",
                    "min": "0.01",
                }
            ),
            "reference": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Factura, transferencia o referencia",
                }
            ),
            "notes": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 4,
                }
            ),
            "receipt_file": forms.ClearableFileInput(
                attrs={
                    "class": "form-control",
                }
            ),
        }

    def __init__(
        self,
        *args,
        company=None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self.company = company

        self.fields["category"].queryset = ExpenseCategory.objects.none()

        self.fields["payment_account"].queryset = AccountingAccount.objects.none()

        if company:
            self.fields["category"].queryset = ExpenseCategory.objects.filter(
                company=company,
                is_active=True,
            ).select_related(
                "expense_account",
            )

            self.fields["payment_account"].queryset = AccountingAccount.objects.filter(
                company=company,
                account_type=(AccountingAccount.TYPE_ASSET),
                is_active=True,
                code__in=[
                    "1101",
                    "1102",
                ],
            ).order_by("code")

    def clean(self):
        cleaned_data = super().clean()

        category = cleaned_data.get("category")
        payment_account = cleaned_data.get("payment_account")

        if self.company and category:
            if category.company_id != self.company.id:
                self.add_error(
                    "category",
                    "La categoría pertenece a otra empresa.",
                )

        if self.company and payment_account:
            if payment_account.company_id != self.company.id:
                self.add_error(
                    "payment_account",
                    ("La cuenta de pago pertenece " "a otra empresa."),
                )

        return cleaned_data
