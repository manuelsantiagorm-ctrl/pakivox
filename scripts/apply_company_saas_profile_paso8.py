from pathlib import Path
import shutil
import sys


ROOT = Path.cwd()

CONFIG_URLS = ROOT / "config" / "urls.py"
OWNER_URLS = ROOT / "owner" / "urls.py"
BASE_TEMPLATE = ROOT / "templates" / "base.html"
OWNER_CONSOLE = (
    ROOT
    / "templates"
    / "owner"
    / "owner_saas_console.html"
)
OWNER_FORMS = ROOT / "owner" / "forms.py"
OWNER_VIEWS = ROOT / "owner" / "views.py"


def backup(path):
    if not path.exists():
        raise FileNotFoundError(
            f"No existe el archivo requerido: {path}"
        )

    backup_path = path.with_name(
        f"{path.stem}_backup_paso8{path.suffix}"
    )

    if not backup_path.exists():
        shutil.copy2(
            path,
            backup_path,
        )


def patch_config_urls():
    backup(CONFIG_URLS)

    text = CONFIG_URLS.read_text(
        encoding="utf-8"
    )

    if 'include("companies.urls")' not in text:
        accounts_line = (
            '    path("accounts/", include("accounts.urls")),'
        )

        company_line = (
            "\n"
            "    # Perfil corporativo de la empresa cliente\n"
            '    path("empresa/", include("companies.urls")),'
        )

        if accounts_line in text:
            text = text.replace(
                accounts_line,
                accounts_line + company_line,
                1,
            )
        else:
            marker = "urlpatterns = ["

            if marker not in text:
                raise RuntimeError(
                    "No se encontró urlpatterns en config/urls.py."
                )

            text = text.replace(
                marker,
                (
                    marker
                    + "\n"
                    + '    path("empresa/", include("companies.urls")),'
                ),
                1,
            )

    CONFIG_URLS.write_text(
        text,
        encoding="utf-8",
    )


def patch_owner_urls():
    backup(OWNER_URLS)

    text = OWNER_URLS.read_text(
        encoding="utf-8"
    )

    import_line = (
        "from . import company_profile_views"
    )

    if import_line not in text:
        views_import = "from . import views"

        if views_import in text:
            text = text.replace(
                views_import,
                views_import + "\n" + import_line,
                1,
            )
        else:
            text = import_line + "\n" + text

    marker = (
        "# PERFIL DE EMPRESA SAAS - PASO 8"
    )

    if marker not in text:
        routes = '''
    # PERFIL DE EMPRESA SAAS - PASO 8
    path(
        "companies/<int:company_id>/profile/",
        company_profile_views.owner_company_profile,
        name="company_profile",
    ),
    path(
        "companies/<int:company_id>/edit/",
        company_profile_views.owner_company_edit,
        name="company_edit",
    ),
'''

        closing_index = text.rfind("]")

        if closing_index == -1:
            raise RuntimeError(
                "No se encontró el cierre de urlpatterns en owner/urls.py."
            )

        text = (
            text[:closing_index]
            + routes
            + text[closing_index:]
        )

    OWNER_URLS.write_text(
        text,
        encoding="utf-8",
    )


def patch_base_template():
    backup(BASE_TEMPLATE)

    text = BASE_TEMPLATE.read_text(
        encoding="utf-8"
    )

    marker = (
        "<!-- PERFIL DE EMPRESA SAAS - PASO 8 -->"
    )

    if marker in text:
        return

    company_menu = '''
          <!-- PERFIL DE EMPRESA SAAS - PASO 8 -->
          {% if request.user.is_authenticated and not request.user.is_superuser %}
            <a
              href="{% url 'companies:profile' %}"
              data-menu-link
            >
              <i class="bi bi-building-check"></i>

              <span class="menu-label">
                Mi empresa
                <small>Perfil corporativo</small>
              </span>
            </a>
          {% endif %}
'''

    crm_start = text.find(
        '<a href="/crm/" data-menu-link>'
    )

    if crm_start == -1:
        raise RuntimeError(
            "No se encontró el enlace principal del CRM en templates/base.html."
        )

    crm_end = text.find(
        "</a>",
        crm_start,
    )

    if crm_end == -1:
        raise RuntimeError(
            "No se encontró el cierre del enlace CRM."
        )

    crm_end += len("</a>")

    text = (
        text[:crm_end]
        + "\n"
        + company_menu
        + text[crm_end:]
    )

    BASE_TEMPLATE.write_text(
        text,
        encoding="utf-8",
    )


def patch_owner_console():
    backup(OWNER_CONSOLE)

    text = OWNER_CONSOLE.read_text(
        encoding="utf-8"
    )

    marker = (
        "<!-- ACCESO PERFIL EMPRESA SAAS - PASO 8 -->"
    )

    if marker in text:
        return

    header = (
        "              <th>Créditos</th>"
    )

    new_header = (
        header
        + "\n"
        + "              <th>Perfil</th>"
    )

    if header not in text:
        raise RuntimeError(
            "No se encontró la columna Créditos en el panel Owner."
        )

    text = text.replace(
        header,
        new_header,
        1,
    )

    credits_cell = (
        "                <td><strong>{{ row.credits }}</strong></td>"
    )

    profile_cell = '''
                <!-- ACCESO PERFIL EMPRESA SAAS - PASO 8 -->
                <td>
                  <a
                    href="{% url 'owner:company_profile' row.company.id %}"
                    class="owner-button"
                    style="padding:8px 11px;font-size:11px;"
                  >
                    Ver perfil
                  </a>
                </td>'''

    if credits_cell not in text:
        raise RuntimeError(
            "No se encontró la celda de créditos en el panel Owner."
        )

    text = text.replace(
        credits_cell,
        credits_cell + "\n" + profile_cell,
        1,
    )

    text = text.replace(
        '<td colspan="5">Todavía no hay clientes SaaS registrados.</td>',
        '<td colspan="6">Todavía no hay clientes SaaS registrados.</td>',
        1,
    )

    OWNER_CONSOLE.write_text(
        text,
        encoding="utf-8",
    )


def patch_owner_client_creation_form():
    backup(OWNER_FORMS)

    text = OWNER_FORMS.read_text(
        encoding="utf-8"
    )

    if (
        "from django.contrib.auth import get_user_model"
        not in text
    ):
        text = text.replace(
            "from django import forms",
            (
                "from django import forms\n"
                "from django.contrib.auth import get_user_model\n"
                "from django.core.exceptions import ValidationError"
            ),
            1,
        )

    marker = (
        "# VALIDACION ADMIN CLIENTE SAAS - PASO 8"
    )

    if marker not in text:
        class_start = text.find(
            "class SaaSClientQuickCreateForm"
        )
        next_class = text.find(
            "class OwnerWhatsAppNumberForm",
            class_start,
        )

        if (
            class_start == -1
            or next_class == -1
        ):
            raise RuntimeError(
                "No se encontró SaaSClientQuickCreateForm en owner/forms.py."
            )

        init_index = text.rfind(
            "    def __init__(self, *args, **kwargs):",
            class_start,
            next_class,
        )

        if init_index == -1:
            raise RuntimeError(
                "No se encontró __init__ de SaaSClientQuickCreateForm."
            )

        validation_method = '''
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

'''

        text = (
            text[:init_index]
            + validation_method
            + text[init_index:]
        )

    OWNER_FORMS.write_text(
        text,
        encoding="utf-8",
    )


def patch_owner_client_creation_view():
    backup(OWNER_VIEWS)

    text = OWNER_VIEWS.read_text(
        encoding="utf-8"
    )

    if (
        "from django.contrib.auth import get_user_model"
        not in text
    ):
        text = text.replace(
            "from django.contrib import messages",
            (
                "from django.contrib import messages\n"
                "from django.contrib.auth import get_user_model"
            ),
            1,
        )

    if "from accounts.models import UserProfile" not in text:
        company_import = (
            "from companies.models import Company"
        )

        text = text.replace(
            company_import,
            (
                "from accounts.models import UserProfile\n"
                + company_import
            ),
            1,
        )

    marker = (
        "# ADMIN PRINCIPAL CLIENTE SAAS - PASO 8"
    )

    if marker not in text:
        company_create = '''                    company = Company.objects.create(
                        name=client_form.cleaned_data["company_name"]
                    )
'''

        if company_create not in text:
            raise RuntimeError(
                "No se encontró la creación de Company en owner/views.py."
            )

        create_admin = '''
                    # ADMIN PRINCIPAL CLIENTE SAAS - PASO 8
                    admin_email = client_form.cleaned_data[
                        "admin_email"
                    ].strip().lower()

                    admin_user = get_user_model().objects.create_user(
                        username=admin_email,
                        email=admin_email,
                        password=client_form.cleaned_data[
                            "admin_password"
                        ],
                        first_name=client_form.cleaned_data[
                            "admin_name"
                        ].strip(),
                        is_active=True,
                    )

                    UserProfile.objects.create(
                        user=admin_user,
                        company=company,
                        role="owner",
                        is_active=True,
                    )
'''

        text = text.replace(
            company_create,
            company_create + create_admin,
            1,
        )

    OWNER_VIEWS.write_text(
        text,
        encoding="utf-8",
    )


def main():
    required_files = [
        CONFIG_URLS,
        OWNER_URLS,
        BASE_TEMPLATE,
        OWNER_CONSOLE,
        OWNER_FORMS,
        OWNER_VIEWS,
        ROOT / "companies" / "profile_services.py",
        ROOT / "companies" / "profile_forms.py",
        ROOT / "companies" / "profile_views.py",
        ROOT / "companies" / "urls.py",
        ROOT / "owner" / "company_profile_views.py",
        ROOT / "templates" / "companies" / "company_profile.html",
        ROOT / "templates" / "companies" / "company_profile_edit.html",
        ROOT / "templates" / "owner" / "company_profile.html",
        ROOT / "templates" / "owner" / "company_profile_edit.html",
    ]

    missing = [
        str(path)
        for path in required_files
        if not path.exists()
    ]

    if missing:
        print(
            "FALTAN ARCHIVOS DEL PAQUETE:"
        )

        for path in missing:
            print(
                f"- {path}"
            )

        sys.exit(1)

    patch_config_urls()
    patch_owner_urls()
    patch_base_template()
    patch_owner_console()
    patch_owner_client_creation_form()
    patch_owner_client_creation_view()

    print(
        "PERFIL DE EMPRESA SAAS APLICADO CORRECTAMENTE"
    )
    print(
        "- Vista Mi empresa para el cliente"
    )
    print(
        "- Edición corporativa por Dueño/Administrador"
    )
    print(
        "- Perfil de cada empresa en Owner SaaS"
    )
    print(
        "- Menú lateral y tabla Owner actualizados"
    )
    print(
        "- El alta SaaS ahora crea el usuario administrador"
    )
    print(
        "- Seguridad multiempresa aplicada"
    )
    print(
        "- No requiere migraciones"
    )


if __name__ == "__main__":
    main()
