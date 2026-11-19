from django import forms
from django.contrib.auth import get_user_model

from .models import Customer, Deal, PipelineStage


class DealCreateForm(forms.ModelForm):
    new_customer_name = forms.CharField(
        required=False,
        label="Nuevo cliente",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Ej. Distribuidora El Norte",
            }
        ),
    )

    new_customer_phone = forms.CharField(
        required=False,
        label="Teléfono",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Ej. 5512345678",
            }
        ),
    )

    new_customer_email = forms.EmailField(
        required=False,
        label="Correo",
        widget=forms.EmailInput(
            attrs={
                "class": "form-control",
                "placeholder": "cliente@empresa.com",
            }
        ),
    )

    class Meta:
        model = Deal
        fields = [
            "customer",
            "stage",
            "assigned_to",
            "title",
            "amount",
            "probability",
            "expected_close_date",
            "notes",
        ]

        widgets = {
            "customer": forms.Select(attrs={"class": "form-control"}),
            "stage": forms.Select(attrs={"class": "form-control"}),
            "assigned_to": forms.Select(attrs={"class": "form-control"}),
            "title": forms.TextInput(attrs={"class": "form-control"}),
            "amount": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "step": "0.01"}
            ),
            "probability": forms.NumberInput(
                attrs={"class": "form-control", "min": "0", "max": "100"}
            ),
            "expected_close_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}
            ),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.company = company
        User = get_user_model()

        if company:
            self.fields["customer"].queryset = Customer.objects.filter(
                company=company,
                is_active=True,
            ).order_by("full_name")

            self.fields["stage"].queryset = PipelineStage.objects.filter(
                company=company,
                is_active=True,
            ).order_by("order")

            self.fields["assigned_to"].queryset = User.objects.filter(
                profile__company=company,
                profile__is_active=True,
                is_active=True,
            ).order_by("username")
        else:
            self.fields["customer"].queryset = Customer.objects.none()
            self.fields["stage"].queryset = PipelineStage.objects.none()
            self.fields["assigned_to"].queryset = User.objects.none()

        self.fields["customer"].required = False

    def clean(self):
        cleaned_data = super().clean()
        customer = cleaned_data.get("customer")
        stage = cleaned_data.get("stage")
        assigned_to = cleaned_data.get("assigned_to")
        new_customer_name = cleaned_data.get("new_customer_name")

        if self.company is None:
            raise forms.ValidationError(
                "No se detectó una empresa asignada al usuario."
            )

        if customer and customer.company_id != self.company.id:
            raise forms.ValidationError(
                "El cliente seleccionado no pertenece a tu empresa."
            )

        if stage and stage.company_id != self.company.id:
            raise forms.ValidationError(
                "La etapa seleccionada no pertenece a tu empresa."
            )

        if assigned_to:
            profile = getattr(assigned_to, "profile", None)
            if (
                profile is None
                or profile.company_id != self.company.id
                or not profile.is_active
                or not assigned_to.is_active
            ):
                raise forms.ValidationError(
                    "El ejecutivo seleccionado no pertenece a tu empresa."
                )

        if not customer and not new_customer_name:
            raise forms.ValidationError(
                "Selecciona un cliente existente o escribe un nuevo cliente."
            )

        return cleaned_data
