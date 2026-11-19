from django import forms

from companies.models import (
    BusinessLine,
    Company,
    CompanyBusinessLine,
)

from .media_models import CompanyBranding


class CompanyProfileForm(forms.ModelForm):
    business_lines = forms.ModelMultipleChoiceField(
        label="Giros comerciales",
        queryset=BusinessLine.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text=(
            "Selecciona los sectores en los que opera la empresa."
        ),
    )

    latitude = forms.DecimalField(
        label="Latitud",
        required=False,
        max_digits=9,
        decimal_places=6,
        widget=forms.NumberInput(
            attrs={
                "class": "company-form-control",
                "step": "0.000001",
                "placeholder": "19.432608",
            }
        ),
        help_text="Coordenada opcional para Street View.",
    )
    longitude = forms.DecimalField(
        label="Longitud",
        required=False,
        max_digits=9,
        decimal_places=6,
        widget=forms.NumberInput(
            attrs={
                "class": "company-form-control",
                "step": "0.000001",
                "placeholder": "-99.133209",
            }
        ),
        help_text="Coordenada opcional para Street View.",
    )

    class Meta:
        model = Company
        fields = [
            "name",
            "rfc",
            "email",
            "phone",
            "address",
            "business_lines",
        ]
        widgets = {
            "name": forms.TextInput(
                attrs={
                    "class": "company-form-control",
                    "placeholder": "Nombre comercial de la empresa",
                }
            ),
            "rfc": forms.TextInput(
                attrs={
                    "class": "company-form-control",
                    "placeholder": "RFC",
                }
            ),
            "email": forms.EmailInput(
                attrs={
                    "class": "company-form-control",
                    "placeholder": "contacto@empresa.com",
                }
            ),
            "phone": forms.TextInput(
                attrs={
                    "class": "company-form-control",
                    "placeholder": "Teléfono corporativo",
                }
            ),
            "address": forms.Textarea(
                attrs={
                    "class": "company-form-control",
                    "rows": 4,
                    "placeholder": "Dirección fiscal o corporativa",
                }
            ),
        }

    def __init__(
        self,
        *args,
        actor=None,
        **kwargs,
    ):
        self.actor = actor

        super().__init__(
            *args,
            **kwargs,
        )

        branding = None

        if self.instance and self.instance.pk:
            branding = CompanyBranding.objects.filter(
                company=self.instance,
            ).first()

        if branding:
            self.fields["latitude"].initial = branding.latitude
            self.fields["longitude"].initial = branding.longitude

        self.fields[
            "business_lines"
        ].queryset = BusinessLine.objects.filter(
            is_active=True,
        ).order_by(
            "name",
        )

        if self.instance and self.instance.pk:
            self.fields[
                "business_lines"
            ].initial = self.instance.business_categories.all()

    def clean_rfc(self):
        return str(
            self.cleaned_data.get(
                "rfc"
            )
            or ""
        ).strip().upper()

    def clean_email(self):
        return str(
            self.cleaned_data.get(
                "email"
            )
            or ""
        ).strip().lower()

    def clean_phone(self):
        return str(
            self.cleaned_data.get(
                "phone"
            )
            or ""
        ).strip()

    def save(
        self,
        commit=True,
    ):
        company = super().save(
            commit=commit,
        )

        if not commit:
            return company

        selected_lines = list(
            self.cleaned_data.get(
                "business_lines"
            )
            or []
        )

        selected_ids = {
            line.pk
            for line in selected_lines
        }

        current_links = CompanyBusinessLine.objects.filter(
            company=company,
        )

        current_primary = current_links.filter(
            is_primary=True,
        ).first()

        primary_id = None

        if (
            current_primary
            and current_primary.business_line_id
            in selected_ids
        ):
            primary_id = (
                current_primary.business_line_id
            )
        elif selected_lines:
            primary_id = selected_lines[0].pk

        current_links.exclude(
            business_line_id__in=selected_ids,
        ).delete()

        for business_line in selected_lines:
            link, created = (
                CompanyBusinessLine.objects.get_or_create(
                    company=company,
                    business_line=business_line,
                    defaults={
                        "is_primary": (
                            business_line.pk
                            == primary_id
                        ),
                        "created_by": self.actor,
                    },
                )
            )

            should_be_primary = (
                business_line.pk
                == primary_id
            )

            if link.is_primary != should_be_primary:
                link.is_primary = should_be_primary
                link.save(
                    update_fields=[
                        "is_primary",
                    ]
                )

        branding, _ = CompanyBranding.objects.get_or_create(
            company=company,
        )
        branding.latitude = self.cleaned_data.get("latitude")
        branding.longitude = self.cleaned_data.get("longitude")
        branding.updated_by = self.actor
        branding.save()

        return company
