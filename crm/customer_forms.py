import re

from django import forms
from django.core.exceptions import ValidationError

from .models import Customer
from custom_fields.forms import add_custom_fields_to_form


INVALID_CUSTOMER_NAMES = {
    "", "-", "[vacío]", "[vacio]", "vacío", "vacio",
    "n/a", "na", "null", "none", "sin nombre",
}

SAFE_FIELDS = [
    "full_name",
    "business_name",
    "rfc",
    "email",
    "phone",
    "whatsapp",
    "customer_type",
    "source",
    "estimated_monthly_purchase",
    "notes",
    "is_active",
]

MODEL_FIELDS = {
    field.name
    for field in Customer._meta.fields
}

FORM_FIELDS = [
    field_name
    for field_name in SAFE_FIELDS
    if field_name in MODEL_FIELDS
]


class CustomerEditForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = FORM_FIELDS
        widgets = {
            "full_name": forms.TextInput(
                attrs={
                    "class": "crm-edit-input",
                    "placeholder": "Nombre completo o razón social",
                }
            ),
            "business_name": forms.TextInput(
                attrs={
                    "class": "crm-edit-input",
                    "placeholder": "Nombre comercial",
                }
            ),
            "rfc": forms.TextInput(
                attrs={
                    "class": "crm-edit-input",
                    "placeholder": "RFC",
                }
            ),
            "email": forms.EmailInput(
                attrs={
                    "class": "crm-edit-input",
                    "placeholder": "correo@empresa.com",
                }
            ),
            "phone": forms.TextInput(
                attrs={
                    "class": "crm-edit-input",
                    "inputmode": "tel",
                    "placeholder": "525512345678",
                }
            ),
            "whatsapp": forms.TextInput(
                attrs={
                    "class": "crm-edit-input",
                    "inputmode": "tel",
                    "placeholder": "525512345678",
                }
            ),
            "customer_type": forms.Select(
                attrs={
                    "class": "crm-edit-input",
                }
            ),
            "source": forms.Select(
                attrs={
                    "class": "crm-edit-input",
                }
            ),
            "estimated_monthly_purchase": forms.NumberInput(
                attrs={
                    "class": "crm-edit-input",
                    "step": "0.01",
                    "min": "0",
                }
            ),
            "notes": forms.Textarea(
                attrs={
                    "class": "crm-edit-input",
                    "rows": 5,
                }
            ),
            "is_active": forms.CheckboxInput(
                attrs={
                    "class": "crm-edit-checkbox",
                }
            ),
        }

    def __init__(self, *args, company, **kwargs):
        self.company = company
        super().__init__(*args, **kwargs)

        labels = {
            "full_name": "Nombre completo / Razón social",
            "business_name": "Nombre comercial",
            "rfc": "RFC",
            "email": "Correo",
            "phone": "Teléfono",
            "whatsapp": "WhatsApp",
            "customer_type": "Tipo de cliente",
            "source": "Origen",
            "estimated_monthly_purchase": "Compra mensual estimada",
            "notes": "Notas internas",
            "is_active": "Cliente activo",
        }

        for field_name, field in self.fields.items():
            field.label = labels.get(field_name, field.label)

        add_custom_fields_to_form(
            self,
            company=company,
            customer=self.instance,
            posted=bool(self.is_bound),
        )

    def clean_full_name(self):
        value = str(
            self.cleaned_data.get("full_name") or ""
        ).strip()

        if value.lower() in INVALID_CUSTOMER_NAMES:
            raise ValidationError(
                "Escribe un nombre real o una razón social válida."
            )

        return value

    def _clean_phone_field(self, field_name):
        value = str(
            self.cleaned_data.get(field_name) or ""
        ).strip()

        if not value:
            return ""

        cleaned = re.sub(r"\D+", "", value)

        if len(cleaned) < 10:
            raise ValidationError(
                "El número debe contener al menos 10 dígitos."
            )

        return cleaned

    def clean_phone(self):
        return self._clean_phone_field("phone")

    def clean_whatsapp(self):
        return self._clean_phone_field("whatsapp")

    def clean_rfc(self):
        return str(
            self.cleaned_data.get("rfc") or ""
        ).strip().upper()

    def clean_email(self):
        return str(
            self.cleaned_data.get("email") or ""
        ).strip().lower()

    def clean(self):
        cleaned_data = super().clean()

        checks = [
            (
                "rfc",
                "Ese RFC ya pertenece a otro cliente de esta empresa.",
            ),
            (
                "email",
                "Ese correo ya pertenece a otro cliente de esta empresa.",
            ),
            (
                "phone",
                "Ese teléfono ya pertenece a otro cliente de esta empresa.",
            ),
            (
                "whatsapp",
                "Ese WhatsApp ya pertenece a otro cliente de esta empresa.",
            ),
        ]

        for field_name, message in checks:
            if field_name not in self.fields:
                continue

            value = str(
                cleaned_data.get(field_name) or ""
            ).strip()

            if not value:
                continue

            queryset = Customer.objects.filter(
                company=self.company,
                **{
                    field_name: value,
                },
            )

            if self.instance and self.instance.pk:
                queryset = queryset.exclude(
                    pk=self.instance.pk
                )

            if queryset.exists():
                self.add_error(
                    field_name,
                    message,
                )

        return cleaned_data
