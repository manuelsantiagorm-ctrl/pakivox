import re

from django import forms
from django.core.exceptions import ValidationError

from .models import CustomerAddress


class CustomerAddressForm(forms.ModelForm):
    class Meta:
        model = CustomerAddress
        fields = [
            "address_type",
            "label",
            "contact_name",
            "phone",
            "email",
            "street",
            "exterior_number",
            "interior_number",
            "neighborhood",
            "city",
            "municipality",
            "state",
            "country",
            "country_code",
            "zip_code",
            "reference",
            "is_default",
        ]
        widgets = {
            "address_type": forms.Select(
                attrs={"class": "profile360-input"}
            ),
            "label": forms.TextInput(
                attrs={
                    "class": "profile360-input",
                    "placeholder": "Ej. Matriz, Sucursal Centro o Bodega Norte",
                }
            ),
            "contact_name": forms.TextInput(
                attrs={
                    "class": "profile360-input",
                    "placeholder": "Persona que recibe la mercancía",
                }
            ),
            "phone": forms.TextInput(
                attrs={
                    "class": "profile360-input",
                    "inputmode": "tel",
                    "placeholder": "525512345678",
                }
            ),
            "email": forms.EmailInput(
                attrs={
                    "class": "profile360-input",
                    "placeholder": "recepcion@cliente.com",
                }
            ),
            "street": forms.TextInput(
                attrs={
                    "class": "profile360-input",
                    "placeholder": "Calle o avenida",
                }
            ),
            "exterior_number": forms.TextInput(
                attrs={
                    "class": "profile360-input",
                    "placeholder": "Número exterior",
                }
            ),
            "interior_number": forms.TextInput(
                attrs={
                    "class": "profile360-input",
                    "placeholder": "Número interior",
                }
            ),
            "neighborhood": forms.TextInput(
                attrs={
                    "class": "profile360-input",
                    "placeholder": "Colonia",
                }
            ),
            "city": forms.TextInput(
                attrs={
                    "class": "profile360-input",
                    "placeholder": "Ciudad",
                }
            ),
            "municipality": forms.TextInput(
                attrs={
                    "class": "profile360-input",
                    "placeholder": "Municipio o alcaldía",
                }
            ),
            "state": forms.TextInput(
                attrs={
                    "class": "profile360-input",
                    "placeholder": "Estado",
                }
            ),
            "country": forms.TextInput(
                attrs={
                    "class": "profile360-input",
                    "placeholder": "México",
                }
            ),
            "country_code": forms.TextInput(
                attrs={
                    "class": "profile360-input",
                    "maxlength": "2",
                    "placeholder": "MX",
                }
            ),
            "zip_code": forms.TextInput(
                attrs={
                    "class": "profile360-input",
                    "inputmode": "numeric",
                    "placeholder": "Código postal",
                }
            ),
            "reference": forms.Textarea(
                attrs={
                    "class": "profile360-input",
                    "rows": 4,
                    "placeholder": (
                        "Horario de recepción, acceso para unidades, "
                        "entre calles o instrucciones para el operador."
                    ),
                }
            ),
            "is_default": forms.CheckboxInput(
                attrs={"class": "profile360-checkbox"}
            ),
        }

    def clean_phone(self):
        value = str(
            self.cleaned_data.get("phone") or ""
        ).strip()

        if not value:
            return ""

        normalized = re.sub(r"\D+", "", value)

        if len(normalized) < 10:
            raise ValidationError(
                "El teléfono debe contener al menos 10 dígitos."
            )

        return normalized

    def clean_country_code(self):
        value = str(
            self.cleaned_data.get("country_code") or "MX"
        ).strip().upper()

        if len(value) != 2 or not value.isalpha():
            raise ValidationError(
                "Usa un código de país de dos letras. Ejemplo: MX."
            )

        return value

    def clean_zip_code(self):
        value = str(
            self.cleaned_data.get("zip_code") or ""
        ).strip()

        country_code = str(
            self.cleaned_data.get("country_code") or "MX"
        ).strip().upper()

        if country_code == "MX" and (
            not value.isdigit() or len(value) != 5
        ):
            raise ValidationError(
                "El código postal de México debe tener exactamente 5 números."
            )

        return value
