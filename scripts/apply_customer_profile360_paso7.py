from datetime import datetime
from pathlib import Path
import re
import shutil
import sys


ROOT = Path.cwd()
CONFIG_URLS = ROOT / "config" / "urls.py"
PROFILE_TEMPLATE = (
    ROOT
    / "templates"
    / "crm"
    / "customer_smart_profile.html"
)
CRM_HOME_TEMPLATE = (
    ROOT
    / "templates"
    / "crm"
    / "home.html"
)

IMPORT_BLOCK = '''
from crm.customer_profile360_views import (
    crm_home_secure,
    customer_address_create,
    customer_address_edit,
    customer_address_set_default,
    customer_profile_360,
    customer_profile_360_by_phone,
    duplicate_last_customer_order_secure,
)

'''

ROUTES_MARKER = (
    "# PERFIL CLIENTE 360 - DIRECCIONES"
)

ROUTES_BLOCK = r'''

# PERFIL CLIENTE 360 - DIRECCIONES
urlpatterns += [
    path(
        "crm/customer/<int:customer_id>/addresses/new/",
        customer_address_create,
        name="customer_address_create",
    ),
    path(
        (
            "crm/customer/<int:customer_id>/"
            "addresses/<int:address_id>/edit/"
        ),
        customer_address_edit,
        name="customer_address_edit",
    ),
    path(
        (
            "crm/customer/<int:customer_id>/"
            "addresses/<int:address_id>/default/"
        ),
        customer_address_set_default,
        name="customer_address_set_default",
    ),
]
'''


def backup(path):
    if not path.exists():
        return None

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S"
    )

    backup_path = path.with_name(
        f"{path.name}.backup_paso7_{timestamp}"
    )

    shutil.copy2(
        path,
        backup_path,
    )

    return backup_path


def patch_config_urls():
    if not CONFIG_URLS.exists():
        raise RuntimeError(
            f"No existe {CONFIG_URLS}"
        )

    text = CONFIG_URLS.read_text(
        encoding="utf-8"
    )

    backup_path = backup(
        CONFIG_URLS
    )

    if (
        "from crm.customer_profile360_views import"
        not in text
    ):
        match = re.search(
            r"^urlpatterns\s*=",
            text,
            flags=re.MULTILINE,
        )

        if not match:
            raise RuntimeError(
                "No se encontró urlpatterns en config/urls.py."
            )

        text = (
            text[:match.start()]
            + IMPORT_BLOCK
            + text[match.start():]
        )

    callback_replacements = [
        (
            "crm/",
            "crm_home",
            "crm_home_secure",
            "crm_home",
        ),
        (
            "crm/customer/<int:customer_id>/smart-profile/",
            "customer_smart_profile",
            "customer_profile_360",
            "customer_smart_profile",
        ),
        (
            "crm/customer/search-phone/",
            "customer_smart_profile_by_phone",
            "customer_profile_360_by_phone",
            "customer_smart_profile_by_phone",
        ),
        (
            "crm/customer/phone/<str:phone>/",
            "customer_smart_profile_by_phone",
            "customer_profile_360_by_phone",
            "customer_smart_profile_by_phone_direct",
        ),
        (
            "crm/customer/<int:customer_id>/duplicate-last-order/",
            "duplicate_last_customer_order",
            "duplicate_last_customer_order_secure",
            "duplicate_last_customer_order",
        ),
    ]

    for route, old_callback, new_callback, route_name in callback_replacements:
        route_pattern = re.compile(
            (
                r'(?P<prefix>'
                r'path\(\s*'
                + re.escape(f'"{route}"')
                + r'\s*,\s*'
                r')'
                + re.escape(old_callback)
                + r'(?P<suffix>'
                r'\s*,\s*'
                r'name\s*=\s*'
                + re.escape(f'"{route_name}"')
                + r'\s*,?\s*\)'
                r')'
            ),
            flags=re.DOTALL,
        )

        text, replacements = route_pattern.subn(
            lambda match: (
                match.group("prefix")
                + new_callback
                + match.group("suffix")
            ),
            text,
            count=1,
        )

        if replacements == 0:
            already_pattern = re.compile(
                (
                    r'path\(\s*'
                    + re.escape(f'"{route}"')
                    + r'\s*,\s*'
                    + re.escape(new_callback)
                    + r'\s*,\s*'
                    r'name\s*=\s*'
                    + re.escape(f'"{route_name}"')
                ),
                flags=re.DOTALL,
            )

            if not already_pattern.search(
                text
            ):
                raise RuntimeError(
                    (
                        "No se localizó la ruta "
                        f"{route_name} para asegurarla."
                    )
                )

    if ROUTES_MARKER not in text:
        text = (
            text.rstrip()
            + "\n"
            + ROUTES_BLOCK
        )

    CONFIG_URLS.write_text(
        text,
        encoding="utf-8",
    )

    return backup_path


def patch_crm_button():
    if not CRM_HOME_TEMPLATE.exists():
        return None

    original = CRM_HOME_TEMPLATE.read_text(
        encoding="utf-8"
    )

    updated = original.replace(
        "Perfil inteligente",
        "Perfil 360",
    )

    if updated == original:
        return None

    backup(
        CRM_HOME_TEMPLATE
    )

    CRM_HOME_TEMPLATE.write_text(
        updated,
        encoding="utf-8",
    )

    return CRM_HOME_TEMPLATE


def validate_required_files():
    required = [
        ROOT
        / "crm"
        / "customer_profile360_forms.py",
        ROOT
        / "crm"
        / "customer_profile360_views.py",
        PROFILE_TEMPLATE,
        ROOT
        / "templates"
        / "crm"
        / "customer_address_form.html",
    ]

    missing = [
        str(path)
        for path in required
        if not path.exists()
    ]

    if missing:
        raise RuntimeError(
            "Faltan archivos del paquete:\n- "
            + "\n- ".join(
                missing
            )
        )


def validate_python_syntax():
    paths = [
        ROOT
        / "crm"
        / "customer_profile360_forms.py",
        ROOT
        / "crm"
        / "customer_profile360_views.py",
        CONFIG_URLS,
    ]

    for path in paths:
        source = path.read_text(
            encoding="utf-8"
        )

        compile(
            source,
            str(path),
            "exec",
        )


def main():
    try:
        validate_required_files()

        config_backup = patch_config_urls()
        changed_home = patch_crm_button()

        validate_python_syntax()

    except Exception as error:
        print(
            f"ERROR PASO 7: {error}"
        )
        sys.exit(1)

    print(
        "PERFIL CLIENTE 360 APLICADO CORRECTAMENTE"
    )

    if config_backup:
        print(
            f"Respaldo config: {config_backup}"
        )

    if changed_home:
        print(
            "Botón del CRM renombrado a Perfil 360"
        )

    print(
        "No requiere migraciones."
    )

    print(
        "Siguiente validación:"
    )

    print(
        "python manage.py check"
    )


if __name__ == "__main__":
    main()
