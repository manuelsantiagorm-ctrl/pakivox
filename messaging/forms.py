from django import forms
import re
from django.contrib.auth import get_user_model

from accounts.models import UserProfile

from .models import WhatsAppConfig, WhatsAppNumber, ChannelAgentAssignment

User = get_user_model()


class WhatsAppNumberForm(forms.ModelForm):
    confirm_number_ownership = forms.BooleanField(
        label="Confirmo que la empresa controla este número",
        required=True,
        error_messages={"required": "Debes confirmar que tu empresa controla el número."},
    )
    confirm_business_app = forms.BooleanField(
        label="Confirmo que el número utiliza WhatsApp Business",
        required=False,
    )

    class Meta:
        model = WhatsAppNumber
        fields = [
            "channel_name",
            "display_number",
            "warehouse",
            "connection_mode",
        ]

        labels = {
            "channel_name": "Nombre del canal",
            "display_number": "Número de WhatsApp",
            "warehouse": "Bodega",
            "connection_mode": "¿Cómo quieres utilizar el número?",
        }

        help_texts = {
            "channel_name": (
                "Usa un nombre que permita identificar fácilmente este canal."
            ),
            "display_number": (
                "Captura el número con código de país. "
                "Ejemplo para México: +525512345678."
            ),
            "warehouse": ("El canal quedará asociado exclusivamente a esta bodega."),
            "connection_mode": (
                "Coexistence permite conservar WhatsApp Business en el celular y atender también desde WMS360."
            ),
        }

        widgets = {
            "channel_name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Ejemplo: Ventas Mayoreo",
                    "autocomplete": "off",
                }
            ),
            "display_number": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Ejemplo: +525512345678",
                    "autocomplete": "tel",
                    "inputmode": "tel",
                }
            ),
            "warehouse": forms.Select(
                attrs={
                    "class": "form-select",
                }
            ),
            "connection_mode": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.company = company

        warehouse_model = WhatsAppNumber._meta.get_field("warehouse").remote_field.model

        if company:
            self.fields["warehouse"].queryset = warehouse_model.objects.filter(
                company=company, is_active=True
            ).order_by("name")
        else:
            self.fields["warehouse"].queryset = warehouse_model.objects.none()

        self.fields["warehouse"].required = True

    def clean_channel_name(self):
        channel_name = self.cleaned_data.get("channel_name", "").strip()

        if not channel_name:
            raise forms.ValidationError("Escribe un nombre para identificar el canal.")

        return channel_name

    def clean_display_number(self):
        number = self.cleaned_data.get("display_number", "").strip()
        had_international_prefix = number.startswith("+") or number.startswith("00")

        number = (
            number.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        )

        if number.startswith("00"):
            number = f"+{number[2:]}"

        digits = number[1:] if number.startswith("+") else number

        if not digits.isdigit():
            raise forms.ValidationError(
                "El número solo puede contener dígitos y el signo + al inicio."
            )

        if len(digits) < 10 or len(digits) > 15:
            raise forms.ValidationError(
                "Captura un número internacional válido de entre 10 y 15 dígitos."
            )

        # WMS360 opera inicialmente en México: si el cliente captura sus
        # 10 dígitos nacionales sin prefijo internacional, agrega +52.
        # Los números que ya llegan con + o 00 conservan su código de país.
        if not had_international_prefix and len(digits) == 10:
            digits = f"52{digits}"

        # El prefijo móvil mexicano +521 dejó de formar parte del formato
        # E.164 vigente; YCloud/WhatsApp esperan +52 seguido de 10 dígitos.
        if digits.startswith("521") and len(digits) == 13:
            digits = f"52{digits[3:]}"

        return f"+{digits}"

    def clean_warehouse(self):
        warehouse = self.cleaned_data.get("warehouse")

        if not warehouse:
            raise forms.ValidationError(
                "Selecciona la bodega que utilizará este canal."
            )

        if not self.company:
            raise forms.ValidationError("No fue posible identificar la empresa actual.")

        if warehouse.company_id != self.company.id:
            raise forms.ValidationError(
                "La bodega seleccionada no pertenece a tu empresa."
            )

        return warehouse

    def clean(self):
        cleaned_data = super().clean()

        if not self.company:
            raise forms.ValidationError("No fue posible identificar la empresa actual.")

        warehouse = cleaned_data.get("warehouse")

        if (
            cleaned_data.get("connection_mode")
            == WhatsAppNumber.CONNECTION_COEXISTENCE
            and not cleaned_data.get("confirm_business_app")
        ):
            self.add_error(
                "confirm_business_app",
                "Para conservarlo en el celular, confirma que utilizas WhatsApp Business.",
            )

        if warehouse and warehouse.company_id != self.company.id:
            self.add_error(
                "warehouse",
                "La bodega seleccionada no pertenece a tu empresa.",
            )

        return cleaned_data


class ChannelAgentAssignmentForm(forms.ModelForm):
    class Meta:
        model = ChannelAgentAssignment
        fields = [
            "agent",
            "can_view",
            "can_reply",
            "can_create_order",
            "is_supervisor",
            "is_active",
        ]

        widgets = {
            "agent": forms.Select(
                attrs={
                    "class": "form-control",
                }
            ),
            "can_view": forms.CheckboxInput(
                attrs={
                    "class": "form-check-input",
                }
            ),
            "can_reply": forms.CheckboxInput(
                attrs={
                    "class": "form-check-input",
                }
            ),
            "can_create_order": forms.CheckboxInput(
                attrs={
                    "class": "form-check-input",
                }
            ),
            "is_supervisor": forms.CheckboxInput(
                attrs={
                    "class": "form-check-input",
                }
            ),
            "is_active": forms.CheckboxInput(
                attrs={
                    "class": "form-check-input",
                }
            ),
        }

    def __init__(self, *args, company=None, whatsapp_number=None, **kwargs):
        super().__init__(*args, **kwargs)

        self.company = company
        self.whatsapp_number = whatsapp_number

        if company:
            assigned_user_ids = UserProfile.objects.filter(
                company=company,
                is_active=True,
            ).values_list("user_id", flat=True)

            self.fields["agent"].queryset = User.objects.filter(
                id__in=assigned_user_ids,
                is_active=True,
            ).order_by("username")
        else:
            self.fields["agent"].queryset = User.objects.none()


class MessageReplyForm(forms.Form):
    body = forms.CharField(
        label="Mensaje",
        widget=forms.Textarea(
            attrs={
                "class": "chat-textarea",
                "placeholder": "Escribe una respuesta...",
                "rows": 3,
            }
        ),
    )

    def clean_body(self):
        body = self.cleaned_data.get("body", "").strip()

        if not body:
            raise forms.ValidationError("El mensaje no puede estar vacío.")

        return body


from decimal import Decimal

from django import forms

from companies.models import Company
from subscriptions.models import Plan

from .models import WhatsAppNumber


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
        initial=True,
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        apply_owner_form_styles(self)


class OwnerWhatsAppNumberForm(forms.ModelForm):
    class Meta:
        model = WhatsAppNumber
        fields = [
            "company",
            "warehouse",
            "channel_name",
            "display_number",
            "activation_status",
            "provider",
            # Meta WhatsApp Cloud API
            "phone_number_id",
            "meta_access_token",
            "meta_enabled",
            # YCloud (credencial central del servidor)
            "ycloud_enabled",
            # Twilio
            "twilio_account_sid",
            "twilio_auth_token",
            "twilio_whatsapp_from",
            "twilio_enabled",
            "twilio_sandbox_mode",
            # Créditos
            "creditos_disponibles",
            "precio_por_mensaje",
        ]

        widgets = {
            "meta_access_token": forms.PasswordInput(
                render_value=False,
                attrs={
                    "autocomplete": "new-password",
                },
            ),
            "twilio_auth_token": forms.PasswordInput(
                render_value=False,
                attrs={
                    "autocomplete": "new-password",
                },
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["company"].disabled = bool(self.instance and self.instance.pk)

        self.fields["meta_access_token"].required = False
        self.fields["meta_access_token"].help_text = (
            "Escribe un token solo para cambiarlo. "
            "Si lo dejas vacío se conservará el token actual."
        )

        self.fields["twilio_auth_token"].required = False
        self.fields["twilio_auth_token"].help_text = (
            "Escribe un token solo para cambiarlo. "
            "Si lo dejas vacío se conservará el token actual."
        )

        self.fields["activation_status"].help_text = (
            "El canal solamente podrá quedar activo cuando la configuración "
            "del proveedor esté completa."
        )

        self.fields["provider"].help_text = (
            "Selecciona YCloud, Meta WhatsApp Cloud API o Twilio."
        )

        # Migracion a Twilio (25-sep-2026): Meta directo no se ofrece para
        # numeros nuevos. Un numero que YA es de Meta conserva la opcion
        # para poder seguir editandolo hasta su migracion.
        from .provider_policy import meta_direct_new_connections_enabled

        instance_is_meta = bool(
            self.instance
            and self.instance.pk
            and self.instance.provider == WhatsAppNumber.PROVIDER_META
        )

        if not meta_direct_new_connections_enabled() and not instance_is_meta:
            self.fields["provider"].choices = [
                choice
                for choice in self.fields["provider"].choices
                if choice[0] != WhatsAppNumber.PROVIDER_META
            ]
            self.fields["provider"].help_text = (
                "Selecciona Twilio o YCloud. La conexión directa con Meta "
                "está deshabilitada para números nuevos."
            )

        apply_owner_form_styles(self)

    def _preserve_secret(self, field_name):
        value = self.cleaned_data.get(field_name, "")

        if value:
            return value

        if self.instance and self.instance.pk:
            return getattr(self.instance, field_name, "")

        return ""

    def clean_meta_access_token(self):
        return self._preserve_secret("meta_access_token")

    def clean_twilio_auth_token(self):
        return self._preserve_secret("twilio_auth_token")

    def clean_company(self):
        if self.instance and self.instance.pk:
            return self.instance.company

        return self.cleaned_data.get("company")

    def clean(self):
        cleaned_data = super().clean()

        company = cleaned_data.get("company")
        warehouse = cleaned_data.get("warehouse")
        provider = cleaned_data.get("provider")
        activation_status = cleaned_data.get("activation_status")

        if warehouse and company and warehouse.company_id != company.id:
            self.add_error(
                "warehouse",
                "La bodega seleccionada no pertenece a esta empresa.",
            )

        if activation_status == WhatsAppNumber.STATUS_ACTIVE:
            if provider == WhatsAppNumber.PROVIDER_META:
                if not cleaned_data.get("phone_number_id"):
                    self.add_error(
                        "phone_number_id",
                        (
                            "Captura el Phone Number ID antes de activar "
                            "el canal de Meta."
                        ),
                    )

                if not cleaned_data.get("meta_access_token"):
                    self.add_error(
                        "meta_access_token",
                        ("Captura el Meta Access Token antes de activar " "el canal."),
                    )

                if not cleaned_data.get("meta_enabled"):
                    self.add_error(
                        "meta_enabled",
                        ("Debes habilitar Meta antes de activar " "el canal."),
                    )

            elif provider == WhatsAppNumber.PROVIDER_TWILIO:
                if not cleaned_data.get("twilio_account_sid"):
                    self.add_error(
                        "twilio_account_sid",
                        ("Captura el Twilio Account SID antes de activar " "el canal."),
                    )

                if not cleaned_data.get("twilio_auth_token"):
                    self.add_error(
                        "twilio_auth_token",
                        ("Captura el Twilio Auth Token antes de activar " "el canal."),
                    )

                if not cleaned_data.get("twilio_whatsapp_from"):
                    self.add_error(
                        "twilio_whatsapp_from",
                        (
                            "Captura el número WhatsApp From antes de activar "
                            "el canal."
                        ),
                    )

                if not cleaned_data.get("twilio_enabled"):
                    self.add_error(
                        "twilio_enabled",
                        ("Debes habilitar Twilio antes de activar " "el canal."),
                    )

            elif provider == WhatsAppNumber.PROVIDER_YCLOUD:
                if not cleaned_data.get("display_number"):
                    self.add_error(
                        "display_number",
                        "Captura el número registrado en YCloud antes de activar el canal.",
                    )

                if not cleaned_data.get("ycloud_enabled"):
                    self.add_error(
                        "ycloud_enabled",
                        "Debes habilitar YCloud antes de activar el canal.",
                    )

        return cleaned_data


class OwnerYCloudVerificationForm(forms.Form):
    waba_id = forms.CharField(
        label="YCloud WABA ID", max_length=120,
        widget=forms.TextInput(attrs={"class": "owner-input", "autocomplete": "off"}),
        help_text="Se valida únicamente desde el servidor; la API Key nunca se muestra.",
    )

    def clean_waba_id(self):
        value = self.cleaned_data["waba_id"].strip()
        if not re.fullmatch(r"[A-Za-z0-9._:-]{3,120}", value):
            raise forms.ValidationError("Escribe un WABA ID válido.")
        return value


class WhatsAppNumberAdminForm(forms.ModelForm):
    class Meta:
        model = WhatsAppNumber
        fields = "__all__"
        widgets = {
            "twilio_auth_token": forms.PasswordInput(render_value=False),
            "meta_access_token": forms.PasswordInput(render_value=False),
        }

    def _preserve(self, field_name):
        value = self.cleaned_data.get(field_name, "")
        if value:
            return value
        if self.instance and self.instance.pk:
            return getattr(self.instance, field_name)
        return ""

    def clean_twilio_auth_token(self):
        return self._preserve("twilio_auth_token")

    def clean_meta_access_token(self):
        return self._preserve("meta_access_token")


class WhatsAppConfigAdminForm(forms.ModelForm):
    class Meta:
        model = WhatsAppConfig
        fields = "__all__"
        widgets = {
            "permanent_access_token": forms.PasswordInput(render_value=False),
            "webhook_verify_token": forms.PasswordInput(render_value=False),
        }

    def _preserve(self, field_name):
        value = self.cleaned_data.get(field_name, "")
        if value:
            return value
        if self.instance and self.instance.pk:
            return getattr(self.instance, field_name)
        return ""

    def clean_permanent_access_token(self):
        return self._preserve("permanent_access_token")

    def clean_webhook_verify_token(self):
        return self._preserve("webhook_verify_token")


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
