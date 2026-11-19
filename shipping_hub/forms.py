from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.forms import formset_factory

from inventory.models import Warehouse

from .models import (
    CompanyShippingIntegration,
    CustomCarrier,
    LocalDeliveryDriver,
    LocalDeliveryRoute,
    LocalDeliveryVehicle,
    ShippingOriginAddress,
)
from .services.credentials import (
    CredentialConfigurationError,
    CredentialDecryptionError,
    CredentialValidationError,
    decrypt_credentials,
    encrypt_credentials,
    is_wms360_encrypted_value,
)

# ============================================================
# CONSTANTES DE INTEGRACIONES
# ============================================================

ACCOUNT_MODE_MASTER = getattr(
    CompanyShippingIntegration,
    "ACCOUNT_MODE_MASTER",
    "master",
)

ACCOUNT_MODE_COMPANY = getattr(
    CompanyShippingIntegration,
    "ACCOUNT_MODE_COMPANY",
    "company",
)


# Proveedores que solamente pueden operar con un contrato
# perteneciente a la empresa cliente.
DIRECT_COMPANY_ONLY_PROVIDERS = {
    "estafeta_direct",
    "fedex_direct",
    "dhl_direct",
}


# Campos que el formulario utilizará para cada proveedor.
PROVIDER_CREDENTIAL_FIELDS = {
    "envia": ("api_token",),
    "skydropx": ("client_id", "client_secret"),
    "estafeta_direct": (
        "api_key",
        "client_id",
        "client_secret",
        "customer_number",
    ),
    "fedex_direct": (
        "client_id",
        "client_secret",
        "account_number",
    ),
    "dhl_direct": (
        "api_key",
        "api_secret",
        "account_number",
    ),
}


ALL_CREDENTIAL_FIELDS = (
    "api_token",
    "api_key",
    "api_secret",
    "client_id",
    "client_secret",
    "account_number",
    "customer_number",
)


# ============================================================
# UTILIDADES
# ============================================================


def _clean_text(
    value: Any,
) -> str:
    return str(value or "").strip()


def _has_configured_value(
    value: Any,
) -> bool:
    if isinstance(
        value,
        Mapping,
    ):
        return any(_has_configured_value(item) for item in value.values())

    if isinstance(
        value,
        (list, tuple, set),
    ):
        return any(_has_configured_value(item) for item in value)

    if value is None:
        return False

    if isinstance(
        value,
        str,
    ):
        return bool(value.strip())

    return True


# ============================================================
# FORMULARIO DE INTEGRACIÓN LOGÍSTICA
# ============================================================


class ShippingIntegrationForm(forms.ModelForm):
    api_token = forms.CharField(
        label="Token API",
        required=False,
        strip=True,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={
                "autocomplete": "new-password",
                "placeholder": "Pega aquí el token API",
                "data-credential-key": "api_token",
            },
        ),
        help_text=(
            "El token nunca vuelve a mostrarse. "
            "Al editar, déjalo vacío para conservarlo."
        ),
    )

    api_key = forms.CharField(
        label="API Key",
        required=False,
        strip=True,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={
                "autocomplete": "new-password",
                "placeholder": "Escribe la API Key",
                "data-credential-key": "api_key",
            },
        ),
        help_text=("La API Key se almacena cifrada y nunca " "vuelve a mostrarse."),
    )

    api_secret = forms.CharField(
        label="API Secret",
        required=False,
        strip=True,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={
                "autocomplete": "new-password",
                "placeholder": "Escribe el API Secret",
                "data-credential-key": "api_secret",
            },
        ),
        help_text=("El secreto se almacena cifrado."),
    )

    client_id = forms.CharField(
        label="Client ID / API Key",
        required=False,
        strip=True,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={
                "autocomplete": "new-password",
                "placeholder": "Escribe el Client ID o API Key",
                "data-credential-key": "client_id",
            },
        ),
        help_text=("Identificador proporcionado por el transportista."),
    )

    client_secret = forms.CharField(
        label="Client Secret",
        required=False,
        strip=True,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={
                "autocomplete": "new-password",
                "placeholder": "Escribe el Client Secret",
                "data-credential-key": "client_secret",
            },
        ),
        help_text=("El Client Secret se almacena cifrado."),
    )

    account_number = forms.CharField(
        label="Número de cuenta",
        required=False,
        strip=True,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={
                "autocomplete": "new-password",
                "placeholder": "Escribe el número de cuenta",
                "data-credential-key": "account_number",
            },
        ),
        help_text=("Número del contrato o cuenta logística."),
    )

    customer_number = forms.CharField(
        label="Número de cliente",
        required=False,
        strip=True,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={
                "autocomplete": "new-password",
                "placeholder": "Escribe el número de cliente",
                "data-credential-key": "customer_number",
            },
        ),
        help_text=("Número de cliente asignado por el proveedor."),
    )

    webhook_secret = forms.CharField(
        label="Secreto del webhook",
        required=False,
        strip=True,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={
                "autocomplete": "new-password",
                "placeholder": "Secreto HMAC SHA-256",
            },
        ),
        help_text="Se cifra y se usa para validar cada actualización automática.",
    )

    class Meta:
        model = CompanyShippingIntegration

        fields = (
            "provider",
            "account_mode",
            "environment",
            "warehouse",
            "account_name",
            "preferred_label_format",
            "fallback_label_format",
            "priority",
            "allow_quote_fallback",
            "is_active",
            "is_primary",
        )

        labels = {
            "provider": "Proveedor",
            "account_mode": "Modo de cuenta",
            "environment": "Entorno",
            "warehouse": "Bodega",
            "account_name": "Nombre de la cuenta",
            "preferred_label_format": "Formato preferido",
            "fallback_label_format": "Formato alternativo",
            "priority": "Prioridad de cotización",
            "allow_quote_fallback": "Permitir respaldo con otro agregador",
            "is_active": "Integración activa",
            "is_primary": "Integración principal",
        }
        help_texts = {
            "warehouse": (
                "Deja vacío para usar esta integración como respaldo "
                "de toda la empresa cuando una bodega no tenga la suya propia."
            ),
            "account_mode": (
                "Selecciona explícitamente si se utilizará "
                "la cuenta maestra de WMS360 o un contrato "
                "propio de la empresa."
            ),
            "is_active": (
                "Una integración modificada debe probarse "
                "antes de volver a activarse."
            ),
            "is_primary": (
                "La integración principal será utilizada "
                "automáticamente en la Mesa de Cubicación."
            ),
        }

        widgets = {
            "provider": forms.Select(
                attrs={
                    "data-provider-select": "true",
                }
            ),
            "account_mode": forms.Select(
                attrs={
                    "data-account-mode-select": "true",
                }
            ),
            "environment": forms.Select(
                attrs={
                    "data-environment-select": "true",
                }
            ),
            "account_name": forms.TextInput(
                attrs={
                    "placeholder": ("Ej. Cuenta logística principal"),
                }
            ),
            "is_active": forms.CheckboxInput(),
            "is_primary": forms.CheckboxInput(),
        }

    def __init__(
        self,
        *args,
        company=None,
        **kwargs,
    ):
        super().__init__(
            *args,
            **kwargs,
        )
        self.company = company

        if "warehouse" in self.fields:
            from warehouse.models import Warehouse

            if company is not None:
                self.fields["warehouse"].queryset = Warehouse.objects.filter(
                    company=company
                )
            else:
                self.fields["warehouse"].queryset = Warehouse.objects.none()
            self.fields["warehouse"].required = False
            self.fields["warehouse"].empty_label = "Toda la empresa (respaldo)"

        self._existing_credentials: dict[str, Any] = {}
        self._existing_credentials_error = ""

        self._credentials_changed = False

        self._clear_credentials = False

        self._initial_provider = _clean_text(
            getattr(
                self.instance,
                "provider",
                "",
            )
        )

        self._initial_environment = _clean_text(
            getattr(
                self.instance,
                "environment",
                "",
            )
        )

        self._initial_account_mode = _clean_text(
            getattr(
                self.instance,
                "account_mode",
                "",
            )
        )

        self._initial_had_credentials = bool(
            _clean_text(
                getattr(
                    self.instance,
                    "credentials_encrypted",
                    "",
                )
            )
        )

        self._initial_had_webhook_secret = bool(
            _clean_text(getattr(self.instance, "webhook_secret_encrypted", ""))
        )

        if company is not None:
            if (
                self.instance
                and self.instance.pk
                and self.instance.company_id != company.id
            ):
                raise ValidationError(
                    ("La integración seleccionada " "no pertenece a la empresa actual.")
                )

            self.instance.company = company

        self._load_existing_credentials()

        self._configure_widget_classes()

        self._configure_credential_placeholders()

    # ========================================================
    # CONFIGURACIÓN VISUAL
    # ========================================================

    def _configure_widget_classes(
        self,
    ) -> None:
        for field_name, field in self.fields.items():
            current_class = field.widget.attrs.get(
                "class",
                "",
            )

            classes = [
                current_class,
                "shipping-input",
            ]

            if field_name in ALL_CREDENTIAL_FIELDS:
                classes.append("shipping-credential-input")

            if isinstance(
                field.widget,
                forms.CheckboxInput,
            ):
                classes.append("shipping-checkbox")

            field.widget.attrs["class"] = " ".join(
                class_name for class_name in classes if class_name
            )

    def _configure_credential_placeholders(
        self,
    ) -> None:
        for field_name in ALL_CREDENTIAL_FIELDS:
            if _has_configured_value(self._existing_credentials.get(field_name)):
                self.fields[field_name].widget.attrs[
                    "placeholder"
                ] = "Credencial guardada ••••••••••••"

    # ========================================================
    # LECTURA SEGURA DE CREDENCIALES EXISTENTES
    # ========================================================

    def _load_existing_credentials(
        self,
    ) -> None:
        encrypted_value = _clean_text(
            getattr(
                self.instance,
                "credentials_encrypted",
                "",
            )
        )

        if not encrypted_value:
            return

        if not is_wms360_encrypted_value(encrypted_value):
            self._existing_credentials_error = (
                "Las credenciales guardadas utilizan " "un formato antiguo no admitido."
            )

            return

        try:
            credentials = decrypt_credentials(encrypted_value)

        except (
            CredentialConfigurationError,
            CredentialDecryptionError,
            CredentialValidationError,
        ):
            self._existing_credentials_error = (
                "Las credenciales guardadas no pudieron "
                "descifrarse con la clave actual."
            )

            return

        if isinstance(
            credentials,
            Mapping,
        ):
            self._existing_credentials = dict(credentials)

    # ========================================================
    # CONFIGURACIÓN MAESTRA
    # ========================================================

    def _get_master_credentials_configuration(
        self,
        *,
        provider: str,
        environment: str,
    ) -> dict[str, Any]:
        master_credentials = getattr(
            settings,
            "SHIPPING_HUB_MASTER_CREDENTIALS",
            {},
        )

        if not isinstance(
            master_credentials,
            Mapping,
        ):
            raise ValidationError(
                ("SHIPPING_HUB_MASTER_CREDENTIALS " "no tiene una estructura válida.")
            )

        provider_config = master_credentials.get(provider)

        if not isinstance(
            provider_config,
            Mapping,
        ):
            raise ValidationError(
                (
                    "WMS360 no tiene una cuenta maestra "
                    "configurada para este proveedor."
                )
            )

        environment_config = provider_config.get(environment)

        if not isinstance(
            environment_config,
            Mapping,
        ):
            raise ValidationError(
                (
                    "WMS360 no tiene credenciales maestras "
                    "para el entorno seleccionado."
                )
            )

        return dict(environment_config)

    def _validate_master_configuration(
        self,
        *,
        provider: str,
        environment: str,
    ) -> None:
        if provider in DIRECT_COMPANY_ONLY_PROVIDERS:
            raise ValidationError(
                (
                    "Los contratos directos de Estafeta, "
                    "FedEx y DHL solamente pueden utilizar "
                    "una cuenta propia de la empresa."
                )
            )

        credentials = self._get_master_credentials_configuration(
            provider=provider,
            environment=environment,
        )

        required_fields = PROVIDER_CREDENTIAL_FIELDS.get(
            provider,
            (),
        )

        missing_fields = [
            field_name
            for field_name in required_fields
            if not _has_configured_value(credentials.get(field_name))
        ]

        if missing_fields:
            raise ValidationError(
                (
                    "La cuenta maestra de WMS360 está "
                    "incompleta para este proveedor y "
                    "entorno. Falta: " + ", ".join(missing_fields) + "."
                )
            )

    # ========================================================
    # CREDENCIALES DE EMPRESA
    # ========================================================

    def _build_company_credentials(
        self,
        *,
        provider: str,
        environment: str,
        account_mode: str,
        cleaned_data: dict[str, Any],
    ) -> dict[str, Any]:
        same_provider = provider == self._initial_provider

        same_environment = environment == self._initial_environment

        previous_mode_was_company = self._initial_account_mode == ACCOUNT_MODE_COMPANY

        may_preserve_existing = all(
            (
                bool(self.instance and self.instance.pk),
                same_provider,
                same_environment,
                previous_mode_was_company,
                not self._existing_credentials_error,
            )
        )

        if may_preserve_existing:
            credentials = dict(self._existing_credentials)
        else:
            credentials = {}

        required_fields = PROVIDER_CREDENTIAL_FIELDS.get(provider)

        if not required_fields:
            raise ValidationError(
                (
                    "El proveedor seleccionado todavía "
                    "no tiene un esquema de credenciales "
                    "configurado en WMS360."
                )
            )

        submitted_any_value = False

        for field_name in required_fields:
            submitted_value = _clean_text(cleaned_data.get(field_name))

            if submitted_value:
                credentials[field_name] = submitted_value

                submitted_any_value = True

        missing_fields = [
            field_name
            for field_name in required_fields
            if not _has_configured_value(credentials.get(field_name))
        ]

        if missing_fields:
            readable_names = [
                self.fields[field_name].label for field_name in missing_fields
            ]

            raise ValidationError(
                (
                    "La cuenta propia de la empresa "
                    "está incompleta. Captura: " + ", ".join(readable_names) + "."
                )
            )

        old_relevant_credentials = {
            field_name: (self._existing_credentials.get(field_name))
            for field_name in required_fields
            if field_name in self._existing_credentials
        }

        new_relevant_credentials = {
            field_name: credentials.get(field_name) for field_name in required_fields
        }

        context_changed = any(
            (
                not same_provider,
                not same_environment,
                not previous_mode_was_company,
            )
        )

        self._credentials_changed = any(
            (
                submitted_any_value,
                context_changed,
                new_relevant_credentials != old_relevant_credentials,
                not self._initial_had_credentials,
            )
        )

        return new_relevant_credentials

    # ========================================================
    # VALIDACIÓN PRINCIPAL
    # ========================================================

    def clean(
        self,
    ):
        cleaned_data = super().clean()

        provider = _clean_text(cleaned_data.get("provider"))

        account_mode = _clean_text(cleaned_data.get("account_mode"))

        environment = _clean_text(cleaned_data.get("environment"))

        preferred = cleaned_data.get("preferred_label_format")

        fallback = cleaned_data.get("fallback_label_format")

        if preferred and fallback and preferred == fallback:
            self.add_error(
                "fallback_label_format",
                ("El formato alternativo debe ser " "diferente al formato preferido."),
            )

        if not provider:
            return cleaned_data

        if not account_mode:
            self.add_error(
                "account_mode",
                ("Selecciona explícitamente " "el modo de cuenta."),
            )

            return cleaned_data

        if not environment:
            return cleaned_data

        provider_changed = provider != self._initial_provider

        environment_changed = environment != self._initial_environment

        account_mode_changed = account_mode != self._initial_account_mode

        configuration_changed = any(
            (
                not (self.instance and self.instance.pk),
                provider_changed,
                environment_changed,
                account_mode_changed,
            )
        )

        if account_mode == ACCOUNT_MODE_MASTER:
            try:
                self._validate_master_configuration(
                    provider=provider,
                    environment=environment,
                )

            except ValidationError as error:
                self.add_error(
                    "account_mode",
                    error,
                )

                return cleaned_data

            self._clear_credentials = bool(
                _clean_text(
                    getattr(
                        self.instance,
                        "credentials_encrypted",
                        "",
                    )
                )
            )

            # Se modifica antes de _post_clean para que
            # Model.full_clean() valide correctamente el
            # modo master sin credenciales de empresa.
            self.instance.credentials_encrypted = ""

            self._credentials_changed = self._clear_credentials

        elif account_mode == ACCOUNT_MODE_COMPANY:
            if self._existing_credentials_error:
                self.add_error(
                    None,
                    self._existing_credentials_error,
                )

                return cleaned_data

            try:
                credentials = self._build_company_credentials(
                    provider=provider,
                    environment=environment,
                    account_mode=account_mode,
                    cleaned_data=cleaned_data,
                )

                if self._credentials_changed or not _clean_text(
                    getattr(
                        self.instance,
                        "credentials_encrypted",
                        "",
                    )
                ):
                    # Se cifra durante clean para que
                    # Model.full_clean() vea el contenedor
                    # seguro antes de validar el modelo.
                    self.instance.credentials_encrypted = encrypt_credentials(
                        credentials
                    )

            except (
                ValidationError,
                CredentialConfigurationError,
                CredentialValidationError,
            ) as error:
                self.add_error(
                    None,
                    error,
                )

                return cleaned_data

        else:
            self.add_error(
                "account_mode",
                ("El modo de cuenta seleccionado " "no es válido."),
            )

            return cleaned_data

        if configuration_changed or self._credentials_changed:
            # Cualquier cambio sensible invalida la
            # prueba anterior y desactiva la integración.
            cleaned_data["is_active"] = False

            self.instance.is_active = False

            self.instance.last_connection_test_at = None

            self.instance.last_connection_test_success = False

            self.instance.last_connection_error = ""

        # Normaliza antes de que ModelForm ejecute _post_clean() y llame a
        # Model.full_clean(). Una integración inactiva nunca debe llegar al
        # modelo conservando la marca principal.
        if not cleaned_data.get("is_active"):
            cleaned_data["is_primary"] = False
            self.instance.is_primary = False

        new_webhook_secret = _clean_text(cleaned_data.get("webhook_secret"))
        if new_webhook_secret:
            try:
                self.instance.webhook_secret_encrypted = encrypt_credentials(
                    {"webhook_secret": new_webhook_secret}
                )
            except (CredentialConfigurationError, CredentialValidationError) as error:
                self.add_error("webhook_secret", error)

        return cleaned_data

    # ========================================================
    # GUARDADO
    # ========================================================

    def save(
        self,
        commit=True,
    ):
        integration = super().save(commit=False)

        if self.company is not None:
            integration.company = self.company

        if commit and not integration.company_id:
            raise ValidationError(
                (
                    "No se detectó la empresa actual. "
                    "La integración no puede guardarse."
                )
            )

        if commit:
            # Una integración inactiva nunca puede conservar la marca
            # principal. Esto también cubre los cambios sensibles que
            # invalidan la prueba de conexión y desactivan la integración.
            if not integration.is_active:
                integration.is_primary = False

            with transaction.atomic():
                # Serializa las configuraciones de la misma empresa y
                # entorno. Nunca toca integraciones de otra empresa.
                list(
                    CompanyShippingIntegration.objects.select_for_update()
                    .filter(
                        company_id=integration.company_id,
                        environment=integration.environment,
                    )
                    .values_list("pk", flat=True)
                )

                if integration.is_primary:
                    (
                        CompanyShippingIntegration.objects.filter(
                            company_id=integration.company_id,
                            environment=integration.environment,
                            is_primary=True,
                        )
                        .exclude(pk=integration.pk)
                        .update(is_primary=False)
                    )

                integration.full_clean()
                integration.save()
                self.save_m2m()

        return integration


# ============================================================
# FORMULARIO DE DIRECCIÓN DE ORIGEN
# ============================================================


class ShippingOriginAddressForm(forms.ModelForm):
    class Meta:
        model = ShippingOriginAddress

        fields = (
            "warehouse",
            "contact_name",
            "company_name",
            "phone",
            "email",
            "street",
            "exterior_number",
            "interior_number",
            "neighborhood",
            "city",
            "state",
            "country",
            "country_code",
            "zip_code",
            "reference",
            "is_active",
        )

        labels = {
            "warehouse": "Bodega",
            "contact_name": "Persona de contacto",
            "company_name": "Empresa remitente",
            "phone": "Teléfono",
            "email": "Correo electrónico",
            "street": "Calle",
            "exterior_number": "Número exterior",
            "interior_number": "Número interior",
            "neighborhood": "Colonia",
            "city": "Ciudad o municipio",
            "state": "Estado",
            "country": "País",
            "country_code": "Código de país",
            "zip_code": "Código postal",
            "reference": "Referencias de ubicación",
            "is_active": "Dirección activa",
        }

        widgets = {
            "contact_name": forms.TextInput(
                attrs={
                    "placeholder": ("Ej. José Manuel Santiago"),
                }
            ),
            "company_name": forms.TextInput(
                attrs={
                    "placeholder": ("Ej. Bodega Prueba Norte"),
                }
            ),
            "phone": forms.TextInput(
                attrs={
                    "placeholder": ("Ej. 5555555555"),
                }
            ),
            "email": forms.EmailInput(
                attrs={
                    "placeholder": ("logistica@empresa.com"),
                }
            ),
            "street": forms.TextInput(
                attrs={
                    "placeholder": ("Ej. Avenida Reforma"),
                }
            ),
            "exterior_number": forms.TextInput(
                attrs={
                    "placeholder": ("Ej. 250"),
                }
            ),
            "interior_number": forms.TextInput(
                attrs={
                    "placeholder": ("Opcional"),
                }
            ),
            "neighborhood": forms.TextInput(
                attrs={
                    "placeholder": ("Ej. Centro"),
                }
            ),
            "city": forms.TextInput(
                attrs={
                    "placeholder": ("Ej. Ciudad de México"),
                }
            ),
            "state": forms.TextInput(
                attrs={
                    "placeholder": ("Ej. Estado de México"),
                }
            ),
            "country": forms.TextInput(
                attrs={
                    "placeholder": ("México"),
                }
            ),
            "country_code": forms.TextInput(
                attrs={
                    "maxlength": "2",
                    "placeholder": "MX",
                }
            ),
            "zip_code": forms.TextInput(
                attrs={
                    "placeholder": ("Ej. 06600"),
                }
            ),
            "reference": forms.Textarea(
                attrs={
                    "rows": 3,
                    "placeholder": ("Ej. Portón azul, recepción " "de mercancía."),
                }
            ),
        }

    def __init__(
        self,
        *args,
        company=None,
        **kwargs,
    ):
        super().__init__(
            *args,
            **kwargs,
        )

        self.company = company

        if company is not None:
            self.instance.company = company

        if company is None:
            self.fields["warehouse"].queryset = Warehouse.objects.none()

        else:
            warehouse_queryset = Warehouse.objects.filter(
                company=company,
                is_active=True,
            ).order_by("name")

            if self.instance and self.instance.pk:
                warehouse_queryset = Warehouse.objects.filter(
                    company=company,
                ).order_by("name")

            self.fields["warehouse"].queryset = warehouse_queryset

        for field in self.fields.values():
            current_class = field.widget.attrs.get(
                "class",
                "",
            )

            field.widget.attrs["class"] = (f"{current_class} shipping-input").strip()

    def clean_warehouse(
        self,
    ):
        warehouse = self.cleaned_data.get("warehouse")

        if warehouse is None:
            return warehouse

        if self.company is None:
            raise ValidationError("No se detectó la empresa actual.")

        if warehouse.company_id != self.company.id:
            raise ValidationError(("La bodega no pertenece " "a la empresa actual."))

        return warehouse

    def clean_country_code(
        self,
    ):
        country_code = (self.cleaned_data.get("country_code") or "MX").strip().upper()

        if len(country_code) != 2:
            raise ValidationError(("Escribe un código de dos letras. " "Ejemplo: MX."))

        return country_code

    def clean_phone(
        self,
    ):
        phone = (self.cleaned_data.get("phone") or "").strip()

        normalized_phone = (
            phone.replace(
                " ",
                "",
            )
            .replace(
                "-",
                "",
            )
            .replace(
                "(",
                "",
            )
            .replace(
                ")",
                "",
            )
        )

        if len(normalized_phone) < 10:
            raise ValidationError(
                ("Escribe un teléfono válido " "de al menos 10 dígitos.")
            )

        return normalized_phone

    def clean_zip_code(
        self,
    ):
        zip_code = (self.cleaned_data.get("zip_code") or "").strip()

        if not zip_code:
            raise ValidationError("Escribe el código postal.")

        if not zip_code.isdigit():
            raise ValidationError(
                ("El código postal debe contener " "solamente números.")
            )

        if len(zip_code) != 5:
            raise ValidationError(
                ("El código postal de México " "debe tener 5 dígitos.")
            )

        return zip_code

    def save(
        self,
        commit=True,
    ):
        origin = super().save(commit=False)

        if self.company is None:
            raise ValidationError("No se detectó la empresa actual.")

        origin.company = self.company

        if commit:
            origin.full_clean()

            origin.save()

        return origin


# ============================================================
# FORMULARIO DE BULTOS
# ============================================================


class CubicationPackageForm(forms.Form):
    description = forms.CharField(
        label="Contenido",
        max_length=200,
        required=False,
        initial="Mercancía",
        widget=forms.TextInput(
            attrs={
                "class": "cubication-input",
                "placeholder": ("Ej. Ropa, calzado o mercancía"),
            }
        ),
    )

    weight_kg = forms.DecimalField(
        label="Peso",
        min_value=0.001,
        max_digits=10,
        decimal_places=3,
        widget=forms.NumberInput(
            attrs={
                "class": ("cubication-input " "package-measure"),
                "step": "0.001",
                "min": "0.001",
                "placeholder": "5.000",
            }
        ),
    )

    length_cm = forms.DecimalField(
        label="Largo",
        min_value=0.01,
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={
                "class": ("cubication-input " "package-measure"),
                "step": "0.01",
                "min": "0.01",
                "placeholder": "40",
            }
        ),
    )

    width_cm = forms.DecimalField(
        label="Ancho",
        min_value=0.01,
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={
                "class": ("cubication-input " "package-measure"),
                "step": "0.01",
                "min": "0.01",
                "placeholder": "30",
            }
        ),
    )

    height_cm = forms.DecimalField(
        label="Alto",
        min_value=0.01,
        max_digits=10,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={
                "class": ("cubication-input " "package-measure"),
                "step": "0.01",
                "min": "0.01",
                "placeholder": "25",
            }
        ),
    )


CubicationPackageFormSet = formset_factory(
    CubicationPackageForm,
    extra=0,
    can_delete=True,
    min_num=1,
    validate_min=True,
    max_num=20,
    validate_max=True,
)


# ============================================================
# GUÍA EXTERNA
# ============================================================


class ExternalShippingLabelForm(forms.Form):
    carrier_name = forms.CharField(
        label="Paquetería",
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Ej. FedEx, DHL, Estafeta o UPS",
                "autocomplete": "off",
            }
        ),
    )

    tracking_number = forms.CharField(
        label="Número de guía / rastreo",
        max_length=200,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Captura el número de rastreo",
                "autocomplete": "off",
            }
        ),
    )

    tracking_url = forms.URLField(
        label="Enlace de seguimiento",
        required=False,
        max_length=1000,
        widget=forms.URLInput(
            attrs={
                "class": "form-control",
                "placeholder": "https://...",
            }
        ),
    )

    total_cost = forms.DecimalField(
        label="Costo real de la guía",
        required=False,
        min_value=0,
        max_digits=12,
        decimal_places=2,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "placeholder": "0.00",
                "step": "0.01",
                "min": "0",
            }
        ),
    )

    currency = forms.ChoiceField(
        label="Moneda",
        choices=[
            ("MXN", "MXN"),
            ("USD", "USD"),
        ],
        initial="MXN",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    label_file = forms.FileField(
        label="Etiqueta PDF",
        required=False,
        widget=forms.ClearableFileInput(
            attrs={
                "class": "form-control",
                "accept": "application/pdf,.pdf",
            }
        ),
        help_text="Opcional. Únicamente PDF, máximo 10 MB.",
    )

    def clean_carrier_name(self):
        carrier_name = self.cleaned_data["carrier_name"]
        return " ".join(carrier_name.split())

    def clean_tracking_number(self):
        tracking_number = self.cleaned_data["tracking_number"].strip().upper()

        if len(tracking_number) < 4:
            raise forms.ValidationError("Captura un número de rastreo válido.")

        return tracking_number

    def clean_label_file(self):
        label_file = self.cleaned_data.get("label_file")

        if not label_file:
            return None

        if label_file.size > 10 * 1024 * 1024:
            raise forms.ValidationError("La etiqueta no puede superar los 10 MB.")

        filename = label_file.name.lower()
        header = label_file.read(8)
        label_file.seek(0)

        if not filename.endswith(".pdf") or not header.startswith(b"%PDF-"):
            raise forms.ValidationError("La etiqueta debe ser un archivo PDF.")

        return label_file


class PackageExternalLabelForm(forms.Form):
    custom_carrier = forms.ModelChoiceField(
        label="Transportista registrado",
        queryset=CustomCarrier.objects.none(),
        required=False,
        empty_label="Escribir otro transportista",
    )
    carrier_name = forms.CharField(label="Paquetería / transportista", max_length=150, required=False)
    service_name = forms.CharField(label="Servicio", max_length=150, required=False)
    tracking_number = forms.CharField(label="Rastreo de este bulto", max_length=200)
    tracking_url = forms.URLField(label="Enlace de seguimiento", max_length=1000, required=False)
    label_file = forms.FileField(
        label="Guía del bulto (PDF, PNG o ZPL)",
        help_text="Máximo 10 MB. Se valida el contenido real, no solo la extensión.",
    )

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company
        self.fields["custom_carrier"].queryset = CustomCarrier.objects.for_company(company).filter(is_active=True)

    def clean_tracking_number(self):
        value = "".join(self.cleaned_data["tracking_number"].split()).upper()
        if len(value) < 4:
            raise forms.ValidationError("Captura un rastreo válido de al menos 4 caracteres.")
        return value

    def clean_label_file(self):
        uploaded = self.cleaned_data["label_file"]
        if uploaded.size > 10 * 1024 * 1024:
            raise forms.ValidationError("La guía no puede superar los 10 MB.")
        name = uploaded.name.lower()
        header = uploaded.read(16)
        uploaded.seek(0)
        if name.endswith(".pdf") and header.startswith(b"%PDF-"):
            uploaded.wms_label_format = "pdf"
            uploaded.wms_content_type = "application/pdf"
        elif name.endswith(".png") and header.startswith(b"\x89PNG\r\n\x1a\n"):
            uploaded.wms_label_format = "png"
            uploaded.wms_content_type = "image/png"
        elif name.endswith((".zpl", ".txt")):
            raw = uploaded.read()
            uploaded.seek(0)
            try:
                text = raw.decode("utf-8-sig").strip()
            except UnicodeDecodeError as exc:
                raise forms.ValidationError("El ZPL debe ser texto UTF-8 válido.") from exc
            if not text.startswith("^XA") or "^XZ" not in text:
                raise forms.ValidationError("El archivo no contiene una etiqueta ZPL válida (^XA ... ^XZ).")
            uploaded.wms_label_format = "zpl"
            uploaded.wms_content_type = "text/plain"
        else:
            raise forms.ValidationError("El contenido no corresponde a un PDF, PNG o ZPL válido.")
        return uploaded

    def clean(self):
        data = super().clean()
        custom = data.get("custom_carrier")
        name = " ".join((data.get("carrier_name") or "").split())
        if custom:
            if custom.company_id != getattr(self.company, "id", None):
                raise forms.ValidationError("El transportista pertenece a otra empresa.")
            data["carrier_name"] = custom.name
            if not data.get("tracking_url") and custom.tracking_url_template:
                data["tracking_url"] = custom.tracking_url_template.replace("{tracking}", data.get("tracking_number", ""))
        elif not name:
            self.add_error("carrier_name", "Escribe o selecciona un transportista.")
        else:
            data["carrier_name"] = name
        return data


class CustomCarrierForm(forms.ModelForm):
    class Meta:
        model = CustomCarrier
        fields = ("name", "contact_name", "phone", "tracking_url_template", "notes", "is_active")


class ManualTrackingEventForm(forms.Form):
    status = forms.ChoiceField(choices=(
        ("in_transit", "En tránsito"),
        ("out_for_delivery", "En reparto"),
        ("delivered", "Entregado"),
        ("exception", "Incidencia"),
    ))
    description = forms.CharField(max_length=500)
    location = forms.CharField(max_length=200, required=False)


# ============================================================
# ENTREGA PROPIA / LOCAL
# ============================================================


class LocalDeliveryForm(forms.Form):
    carrier_name = forms.CharField(
        label="Responsable o método de entrega",
        max_length=150,
        initial="Entrega propia",
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": ("Ej. Repartidor propio o entrega en tienda"),
            }
        ),
    )

    notes = forms.CharField(
        label="Instrucciones de entrega",
        required=False,
        max_length=1000,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": ("Indicaciones para el repartidor o cliente"),
            }
        ),
    )

    driver = forms.ModelChoiceField(
        label="Chofer",
        queryset=LocalDeliveryDriver.objects.none(),
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    vehicle = forms.ModelChoiceField(
        label="Vehículo",
        queryset=LocalDeliveryVehicle.objects.none(),
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    route_name = forms.CharField(
        label="Nombre de la ruta",
        max_length=150,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Ej. Ruta Norte AM"}
        ),
    )
    scheduled_date = forms.DateField(
        label="Fecha programada",
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )
    stop_order = forms.IntegerField(
        label="Número de parada",
        min_value=1,
        initial=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
    )

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        if company is None:
            return
        self.fields["driver"].queryset = LocalDeliveryDriver.objects.filter(
            company=company, is_active=True
        ).order_by("name")
        self.fields["vehicle"].queryset = LocalDeliveryVehicle.objects.filter(
            company=company, is_active=True
        ).order_by("name", "plate")

    def clean_carrier_name(self):
        carrier_name = self.cleaned_data["carrier_name"]
        return " ".join(carrier_name.split())


class LocalDeliveryDriverForm(forms.ModelForm):
    class Meta:
        model = LocalDeliveryDriver
        fields = ["name", "phone", "employee_number", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "phone": forms.TextInput(attrs={"class": "form-control"}),
            "employee_number": forms.TextInput(attrs={"class": "form-control"}),
        }


class LocalDeliveryVehicleForm(forms.ModelForm):
    class Meta:
        model = LocalDeliveryVehicle
        fields = ["name", "plate", "vehicle_type", "max_weight_kg", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "plate": forms.TextInput(attrs={"class": "form-control"}),
            "vehicle_type": forms.TextInput(attrs={"class": "form-control"}),
            "max_weight_kg": forms.NumberInput(
                attrs={"class": "form-control", "min": "0.01", "step": "0.01"}
            ),
        }

    def clean_plate(self):
        return "".join(self.cleaned_data["plate"].upper().split())
