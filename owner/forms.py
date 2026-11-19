from decimal import Decimal
from inventory.models import Warehouse

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from subscriptions.models import Plan
from messaging.models import WhatsAppNumber


def apply_owner_form_styles(form):
    for field in form.fields.values():
        current_class = field.widget.attrs.get("class", "")
        field.widget.attrs["class"] = f"{current_class} owner-input".strip()
    return form


class SaaSClientQuickCreateForm(forms.Form):
    company_name = forms.CharField(
        label="Nombre del cliente / empresa",
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Ejemplo: José Distribuciones",
            }
        ),
    )

    admin_name = forms.CharField(
        label="Nombre del administrador",
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Ejemplo: José Manuel",
            }
        ),
    )

    admin_email = forms.EmailField(
        label="Correo de acceso",
        widget=forms.EmailInput(
            attrs={
                "placeholder": "jose@empresa.com",
            }
        ),
    )

    plan = forms.ModelChoiceField(
        label="Plan SaaS",
        queryset=Plan.objects.all(),
        empty_label="Selecciona un plan",
    )

    subscription_days = forms.IntegerField(
        label="Días de suscripción inicial",
        min_value=1,
        initial=30,
    )

    channel_name = forms.CharField(
        label="Nombre del canal WhatsApp",
        max_length=120,
        initial="Ventas WhatsApp",
    )

    phone_number_id = forms.CharField(
        label="ID interno del canal",
        max_length=120,
        help_text="Ejemplo: jose_ventas_001",
        widget=forms.TextInput(
            attrs={
                "placeholder": "jose_ventas_001",
            }
        ),
    )

    display_number = forms.CharField(
        label="Número visible",
        max_length=30,
        widget=forms.TextInput(
            attrs={
                "placeholder": "+5215512345678",
            }
        ),
    )

    twilio_account_sid = forms.CharField(
        label="Twilio Account SID",
        max_length=120,
        required=False,
    )
    twilio_auth_token = forms.CharField(
        label="Twilio Auth Token",
        max_length=255,
        required=False,
        widget=forms.PasswordInput(render_value=False),
    )

    twilio_whatsapp_from = forms.CharField(
        label="Twilio WhatsApp From",
        max_length=40,
        required=False,
        initial="whatsapp:+14155238886",
    )

    twilio_enabled = forms.BooleanField(
        label="Activar Twilio para este canal",
        required=False,
        initial=False,
        help_text="Actívalo solo cuando ya tengas SID, Auth Token y número From.",
    )

    initial_credits = forms.IntegerField(
        label="Créditos iniciales",
        min_value=0,
        initial=0,
    )

    precio_por_mensaje = forms.DecimalField(
        label="Precio por mensaje",
        max_digits=8,
        decimal_places=2,
        initial=Decimal("0.80"),
    )

    # VALIDACION ADMIN CLIENTE SAAS - PASO 8
    def clean_admin_email(self):
        email = str(
            self.cleaned_data.get("admin_email")
            or ""
        ).strip().lower()

        user_model = get_user_model()

        if (
            user_model.objects.filter(
                username__iexact=email,
            ).exists()
            or user_model.objects.filter(
                email__iexact=email,
            ).exists()
        ):
            raise ValidationError(
                "Ya existe un usuario con este correo."
            )

        return email

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_owner_form_styles(self)


class OwnerWhatsAppNumberForm(forms.ModelForm):
    class Meta:
        model = WhatsAppNumber
        fields = [
            "company",
            "warehouse",
            "provider",
            "phone_number_id",
            "display_number",
            "channel_name",
            "twilio_account_sid",
            "twilio_auth_token",
            "twilio_whatsapp_from",
            "twilio_enabled",
            "twilio_sandbox_mode",
            "creditos_disponibles",
            "precio_por_mensaje",
            "activation_status",
            "activation_notes",
        ]
        widgets = {
            "twilio_auth_token": forms.PasswordInput(
                render_value=True,
            ),
            "activation_notes": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": (
                        "Notas privadas del Owner sobre la activación, "
                        "verificación o suspensión del canal."
                    ),
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_owner_form_styles(self)
        # BUG-100: "company" solo debe deshabilitarse al EDITAR un canal
        # existente (evita reasignarlo a otra empresa). Si se deja
        # deshabilitado también al CREAR, Django ignora cualquier valor
        # enviado en el campo y el canal nuevo queda sin empresa.
        if "company" in self.fields and self.instance and self.instance.pk:
            self.fields["company"].disabled = True
        # BUG-100: la bodega solo debe mostrar opciones de la empresa
        # ya asignada a esta instancia, nunca bodegas de otras empresas
        # del SaaS.
        if "warehouse" in self.fields:
            if self.instance and self.instance.company_id:
                self.fields["warehouse"].queryset = Warehouse.objects.filter(
                    company_id=self.instance.company_id
                )
            else:
                self.fields["warehouse"].queryset = Warehouse.objects.none()
        # BUG-098: los créditos no se editan a mano desde este formulario.
        # Cualquier carga de créditos debe pasar por WhatsAppCreditInjectionForm
        # (owner:console, acción inject_credits), que sí deja auditoría en
        # WhatsAppCreditMovement con idempotency_key. Esto evita que alguien
        # escriba un número arbitrario sin ledger ni rastro de quién/cuándo.
        if "creditos_disponibles" in self.fields:
            self.fields["creditos_disponibles"].disabled = True
        if "activation_status" in self.fields:
            self.fields["activation_status"].label = "Estado de activación"
        if "activation_notes" in self.fields:
            self.fields["activation_notes"].label = "Notas privadas del Owner"
            self.fields["activation_notes"].required = False


class WhatsAppCreditInjectionForm(forms.Form):
    whatsapp_number = forms.ModelChoiceField(
        label="Canal WhatsApp",
        queryset=WhatsAppNumber.objects.select_related("company").filter(
            is_active=True
        ),
        empty_label="Selecciona un canal",
    )

    credits = forms.IntegerField(
        label="Créditos a cargar",
        min_value=1,
        widget=forms.NumberInput(
            attrs={
                "placeholder": "Ejemplo: 1000",
            }
        ),
    )

    amount_mxn = forms.DecimalField(
        label="Monto pagado MXN",
        max_digits=10,
        decimal_places=2,
        required=False,
        initial=Decimal("0.00"),
    )
    idempotency_key = forms.CharField(
        widget=forms.HiddenInput(),
        required=False,
    )

    note = forms.CharField(
        label="Nota",
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "placeholder": "Ejemplo: Alberto Plata transfirió $800 para mensajes.",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_owner_form_styles(self)
