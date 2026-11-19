from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import (
    validate_password,
)
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from accounts.models import (
    EmployeeInvitation,
    PendingCompanyRegistration,
    UserWarehouseAccess,
)
from inventory.models import Warehouse
from accounts.simple_employee_access import activity_choices, normalize_activity_codes

User = get_user_model()


class CompanyRegistrationForm(forms.Form):
    owner_name = forms.CharField(
        label="Nombre completo",
        max_length=180,
        min_length=2,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Tu nombre completo",
                "autocomplete": "name",
                "autofocus": True,
            }
        ),
    )

    email = forms.EmailField(
        label="Correo de acceso",
        max_length=254,
        widget=forms.EmailInput(
            attrs={
                "placeholder": "nombre@empresa.com",
                "autocomplete": "email",
                "autocapitalize": "none",
                "spellcheck": "false",
            }
        ),
    )

    company_name = forms.CharField(
        label="Nombre de la empresa",
        max_length=200,
        min_length=2,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Nombre comercial de tu empresa",
                "autocomplete": "organization",
            }
        ),
    )

    password1 = forms.CharField(
        label="Contraseña",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "Crea una contraseña segura",
                "autocomplete": "new-password",
            }
        ),
    )

    password2 = forms.CharField(
        label="Confirmar contraseña",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "Repite tu contraseña",
                "autocomplete": "new-password",
            }
        ),
    )

    accept_terms = forms.BooleanField(
        label=(
            "Acepto los términos, el aviso de privacidad y el acuerdo de tratamiento de datos de PAKIVOX."
        ),
        required=True,
        error_messages={
            "required": (
                "Debes aceptar los términos y el aviso " "de privacidad para continuar."
            ),
        },
    )

    def clean_owner_name(self):
        owner_name = " ".join(
            str(self.cleaned_data.get("owner_name", "")).strip().split()
        )

        if len(owner_name) < 2:
            raise forms.ValidationError("Escribe el nombre completo del propietario.")

        return owner_name

    def clean_company_name(self):
        company_name = " ".join(
            str(self.cleaned_data.get("company_name", "")).strip().split()
        )

        if len(company_name) < 2:
            raise forms.ValidationError("Escribe el nombre de la empresa.")

        return company_name

    def clean_email(self):
        email = PendingCompanyRegistration.normalize_email(
            self.cleaned_data.get("email")
        )

        if not email:
            raise forms.ValidationError("Escribe un correo válido.")

        user_exists = User.objects.filter(
            Q(username__iexact=email) | Q(email__iexact=email)
        ).exists()

        if user_exists:
            raise forms.ValidationError(
                "Ya existe una cuenta registrada con este correo. "
                "Utiliza la opción de iniciar sesión."
            )

        pending_registration = (
            PendingCompanyRegistration.objects.filter(
                email__iexact=email,
                status=(PendingCompanyRegistration.STATUS_PENDING),
                expires_at__gt=timezone.now(),
            )
            .order_by("-created_at")
            .first()
        )

        if pending_registration:
            raise forms.ValidationError(
                "Este correo ya tiene un registro pendiente de "
                "verificación. Revisa tu bandeja de entrada."
            )

        return email

    def clean(self):
        cleaned_data = super().clean()

        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        email = cleaned_data.get("email")
        owner_name = cleaned_data.get("owner_name")

        if password1 and password2:
            if password1 != password2:
                self.add_error(
                    "password2",
                    "Las contraseñas no coinciden.",
                )
                return cleaned_data

        if password1:
            temporary_user = User(
                username=email or "",
                email=email or "",
            )

            if owner_name:
                temporary_user.first_name = owner_name[:150]

            try:
                validate_password(
                    password1,
                    user=temporary_user,
                )
            except ValidationError as error:
                self.add_error(
                    "password1",
                    error,
                )

        return cleaned_data


class EmployeeInvitationForm(forms.Form):
    WAREHOUSE_ACCESS_LEVEL_CHOICES = (
        (UserWarehouseAccess.LEVEL_VIEW, "Consultar"),
        (UserWarehouseAccess.LEVEL_OPERATE, "Trabajar"),
        (UserWarehouseAccess.LEVEL_APPROVE, "Supervisar"),
        (UserWarehouseAccess.LEVEL_MANAGE, "Administrar"),
    )
    VALID_WAREHOUSE_ACCESS_LEVELS = frozenset(
        code for code, _ in WAREHOUSE_ACCESS_LEVEL_CHOICES
    )

    full_name = forms.CharField(
        label="Nombre completo",
        max_length=180,
        min_length=2,
        widget=forms.TextInput(
            attrs={
                "placeholder": "Nombre del empleado",
                "autocomplete": "name",
            }
        ),
    )

    email = forms.EmailField(
        label="Correo del empleado",
        max_length=254,
        widget=forms.EmailInput(
            attrs={
                "placeholder": "empleado@empresa.com",
                "autocomplete": "email",
                "autocapitalize": "none",
                "spellcheck": "false",
            }
        ),
    )

    activities = forms.MultipleChoiceField(
        label="¿Qué hace esta persona?",
        choices=activity_choices(),
        required=True,
        widget=forms.CheckboxSelectMultiple,
    )

    warehouses = forms.ModelMultipleChoiceField(
        label="¿Dónde trabaja?",
        queryset=Warehouse.objects.none(),
        required=True,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, company=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.company = company
        if company is not None:
            self.fields["warehouses"].queryset = Warehouse.objects.filter(
                company=company,
                is_active=True,
            ).order_by("name")

    def clean_activities(self):
        try:
            return list(
                normalize_activity_codes(
                    self.cleaned_data.get("activities"),
                )
            )
        except ValidationError as error:
            raise forms.ValidationError(error.messages)

    def clean_warehouses(self):
        warehouses = self.cleaned_data["warehouses"]
        if not self.company:
            raise forms.ValidationError("No se detectó la empresa.")
        if warehouses.exclude(company=self.company, is_active=True).exists():
            raise forms.ValidationError("Seleccionaste una bodega de otra empresa.")
        return warehouses

    def clean(self):
        cleaned_data = super().clean()
        warehouses = cleaned_data.get("warehouses")

        if warehouses is not None:
            warehouse_levels = {}
            for warehouse in warehouses:
                raw_level = self.data.get(f"warehouse_level_{warehouse.pk}", "")
                if raw_level not in self.VALID_WAREHOUSE_ACCESS_LEVELS:
                    self.add_error(
                        None,
                        f"Selecciona un nivel válido para la bodega "
                        f"{warehouse.name}.",
                    )
                    continue
                warehouse_levels[warehouse.pk] = raw_level
            cleaned_data["warehouse_levels"] = warehouse_levels

        return cleaned_data

    def clean_full_name(self):
        full_name = " ".join(
            str(
                self.cleaned_data.get(
                    "full_name",
                    "",
                )
            )
            .strip()
            .split()
        )

        if len(full_name) < 2:
            raise forms.ValidationError("Escribe el nombre completo del empleado.")

        return full_name

    def clean_email(self):
        email = (
            str(
                self.cleaned_data.get(
                    "email",
                    "",
                )
                or ""
            )
            .strip()
            .lower()
        )

        if not email:
            raise forms.ValidationError("Escribe un correo válido.")

        user_exists = User.objects.filter(
            Q(username__iexact=email) | Q(email__iexact=email)
        ).exists()

        if user_exists:
            raise forms.ValidationError(
                "Ya existe un usuario registrado con este correo."
            )

        return email


class AcceptEmployeeInvitationForm(forms.Form):
    password1 = forms.CharField(
        label="Crea tu contraseña",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "Contraseña segura",
                "autocomplete": "new-password",
            }
        ),
    )

    password2 = forms.CharField(
        label="Confirmar contraseña",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "Repite tu contraseña",
                "autocomplete": "new-password",
            }
        ),
    )

    def __init__(
        self,
        *args,
        invitation=None,
        **kwargs,
    ):
        super().__init__(
            *args,
            **kwargs,
        )

        self.invitation = invitation

    def clean(self):
        cleaned_data = super().clean()

        password1 = cleaned_data.get("password1")

        password2 = cleaned_data.get("password2")

        if password1 and password2:
            if password1 != password2:
                self.add_error(
                    "password2",
                    "Las contraseñas no coinciden.",
                )

                return cleaned_data

        if password1:
            invitation = self.invitation

            temporary_user = User(
                username=(invitation.email if invitation else ""),
                email=(invitation.email if invitation else ""),
            )

            if invitation:
                temporary_user.first_name = invitation.full_name[:150]

            try:
                validate_password(
                    password1,
                    user=temporary_user,
                )
            except ValidationError as error:
                self.add_error(
                    "password1",
                    error,
                )

        return cleaned_data


class AcceptSaaSClientInvitationForm(forms.Form):
    password1 = forms.CharField(
        label="Crea tu contraseña",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "Contraseña segura",
                "autocomplete": "new-password",
            }
        ),
    )

    password2 = forms.CharField(
        label="Confirmar contraseña",
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "placeholder": "Repite tu contraseña",
                "autocomplete": "new-password",
            }
        ),
    )

    def __init__(self, *args, invitation=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.invitation = invitation

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")

        if password1 and password2 and password1 != password2:
            self.add_error("password2", "Las contraseñas no coinciden.")
            return cleaned_data

        if password1:
            from django.contrib.auth.password_validation import validate_password

            invitation = self.invitation
            temporary_user = User(
                username=(invitation.admin_email if invitation else ""),
                email=(invitation.admin_email if invitation else ""),
            )
            if invitation:
                temporary_user.first_name = invitation.admin_name[:150]

            try:
                validate_password(password1, user=temporary_user)
            except ValidationError as error:
                self.add_error("password1", error)

        return cleaned_data
