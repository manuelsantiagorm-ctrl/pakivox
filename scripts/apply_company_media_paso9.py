from pathlib import Path
import shutil
import sys


ROOT = Path.cwd()
MODELS_PATH = ROOT / "companies" / "models.py"
BASE_TEMPLATE = ROOT / "templates" / "base.html"
CONFIG_URLS = ROOT / "config" / "urls.py"


def backup(path):
    if not path.exists():
        raise FileNotFoundError(
            f"No existe el archivo requerido: {path}"
        )

    backup_path = path.with_name(
        f"{path.stem}_backup_paso9{path.suffix}"
    )

    if not backup_path.exists():
        shutil.copy2(path, backup_path)


def patch_models():
    backup(MODELS_PATH)
    text = MODELS_PATH.read_text(encoding="utf-8")
    marker = "# PERFIL EMPRESA MULTIMEDIA - PASO 9"

    if marker not in text:
        text = text.rstrip() + '''


# PERFIL EMPRESA MULTIMEDIA - PASO 9
# Los modelos viven en un módulo separado para no alterar la arquitectura
# existente de Company.
from .media_models import (  # noqa: E402, F401
    CompanyBranchPhoto,
    CompanyBranding,
    CompanyDocument,
)
'''

    MODELS_PATH.write_text(text, encoding="utf-8")


def remove_old_company_menu(text):
    markers = [
        "<!-- PERFIL DE EMPRESA SAAS - PASO 8 -->",
        "<!-- PERFIL EMPRESA PRIORITARIO - PASO 9 -->",
    ]

    for marker in markers:
        while marker in text:
            start = text.find(marker)
            end = text.find("{% endif %}", start)

            if end == -1:
                raise RuntimeError(
                    f"No se encontró el cierre del menú marcado por: {marker}"
                )

            end += len("{% endif %}")
            text = text[:start] + text[end:]

    return text


def patch_base_template():
    backup(BASE_TEMPLATE)
    text = BASE_TEMPLATE.read_text(encoding="utf-8")
    text = remove_old_company_menu(text)

    dashboard_link = '<a href="/ceo/" data-menu-link>'

    if dashboard_link not in text:
        raise RuntimeError(
            "No se encontró el enlace Dashboard en templates/base.html."
        )

    company_menu = '''
          <!-- PERFIL EMPRESA PRIORITARIO - PASO 9 -->
          {% if request.user.is_authenticated and not request.user.is_superuser %}
            <a
              href="{% url 'companies:profile' %}"
              data-menu-link
              class="company-priority-menu"
            >
              <i class="bi bi-building-check"></i>

              <span class="menu-label">
                Mi empresa
                <small>Perfil corporativo</small>
              </span>
            </a>
          {% endif %}

'''

    text = text.replace(
        dashboard_link,
        company_menu + "          " + dashboard_link,
        1,
    )

    style_marker = "/* PERFIL EMPRESA MENU PRIORITARIO - PASO 9 */"

    if style_marker not in text:
        style = '''
      /* PERFIL EMPRESA MENU PRIORITARIO - PASO 9 */
      .company-priority-menu {
        margin-bottom: 8px;
        border: 1px solid rgba(34, 197, 94, .2);
        background: linear-gradient(
          135deg,
          rgba(34, 197, 94, .12),
          rgba(6, 182, 212, .06)
        );
      }

      .company-priority-menu > i {
        color: #4ade80;
      }
'''
        closing_style = text.find("</style>")

        if closing_style == -1:
            raise RuntimeError(
                "No se encontró el bloque style en templates/base.html."
            )

        text = text[:closing_style] + style + text[closing_style:]

    BASE_TEMPLATE.write_text(text, encoding="utf-8")


def patch_config_urls():
    backup(CONFIG_URLS)
    text = CONFIG_URLS.read_text(encoding="utf-8")

    if 'include("companies.urls")' not in text:
        accounts_line = '    path("accounts/", include("accounts.urls")),'
        company_line = (
            '\n    # Perfil corporativo de la empresa cliente\n'
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
                marker + '\n    path("empresa/", include("companies.urls")),',
                1,
            )

    CONFIG_URLS.write_text(text, encoding="utf-8")


def main():
    required = [
        MODELS_PATH,
        BASE_TEMPLATE,
        CONFIG_URLS,
        ROOT / "companies" / "media_models.py",
        ROOT / "companies" / "media_forms.py",
        ROOT / "companies" / "media_views.py",
        ROOT / "companies" / "profile_forms.py",
        ROOT / "companies" / "profile_services.py",
        ROOT / "companies" / "profile_views.py",
        ROOT / "companies" / "urls.py",
        ROOT / "templates" / "companies" / "company_profile.html",
    ]

    missing = [str(path) for path in required if not path.exists()]

    if missing:
        print("FALTAN ARCHIVOS:")
        for path in missing:
            print(f"- {path}")
        sys.exit(1)

    patch_models()
    patch_base_template()
    patch_config_urls()

    print("PERFIL EMPRESA MULTIMEDIA APLICADO")
    print("- Mi empresa movido arriba de Dashboard")
    print("- Modelos multimedia registrados")
    print("- Rutas protegidas por empresa activadas")


if __name__ == "__main__":
    main()
