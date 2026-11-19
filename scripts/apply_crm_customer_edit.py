from pathlib import Path
import re
import sys


ROOT = Path.cwd()
URLS_PATH = ROOT / "crm" / "urls.py"

TEMPLATE_ROOTS = [
    ROOT / "templates" / "crm",
    ROOT / "crm" / "templates" / "crm",
]


def patch_urls():
    text = URLS_PATH.read_text(encoding="utf-8")

    if "from . import customer_edit_views" not in text:
        if "from . import views" in text:
            text = text.replace(
                "from . import views",
                "from . import views\nfrom . import customer_edit_views",
                1,
            )
        else:
            text = "from . import customer_edit_views\n" + text

    marker = "# VISTA INTERNA PARA EDITAR CLIENTES"

    if marker not in text:
        route = '''
    # VISTA INTERNA PARA EDITAR CLIENTES
    path(
        "customer/<int:customer_id>/edit/",
        customer_edit_views.customer_edit,
        name="customer_edit",
    ),
'''

        closing_index = text.rfind("]")

        if closing_index == -1:
            raise RuntimeError(
                "No se encontró el cierre de urlpatterns."
            )

        text = text[:closing_index] + route + text[closing_index:]

    URLS_PATH.write_text(text, encoding="utf-8")


def patch_profile_templates():
    changed_files = []

    replacements = [
        (
            re.compile(
                r'href=["\']\{\%\s*url\s+["\']admin:crm_customer_change["\']\s+customer\.(?:id|pk)\s*\%\}["\']'
            ),
            'href="{% url \'customer_edit\' customer.id %}"',
        ),
        (
            re.compile(
                r'href=["\']/admin/crm/customer/\{\{\s*customer\.(?:id|pk)\s*\}\}/change/["\']'
            ),
            'href="{% url \'customer_edit\' customer.id %}"',
        ),
    ]

    for template_root in TEMPLATE_ROOTS:
        if not template_root.exists():
            continue

        for path in template_root.rglob("*.html"):
            text = path.read_text(encoding="utf-8")
            original = text

            for pattern, replacement in replacements:
                text = pattern.sub(replacement, text)

            if (
                "Editar cliente" in text
                and "admin/crm/customer" in text
            ):
                text = re.sub(
                    r'href=["\'][^"\']*admin/crm/customer[^"\']*["\']',
                    'href="{% url \'customer_edit\' customer.id %}"',
                    text,
                    count=1,
                )

            if text != original:
                path.write_text(text, encoding="utf-8")
                changed_files.append(
                    str(path.relative_to(ROOT))
                )

    return changed_files


def main():
    if not URLS_PATH.exists():
        print(f"No existe: {URLS_PATH}")
        sys.exit(1)

    patch_urls()
    changed_files = patch_profile_templates()

    print("RUTA DE EDICIÓN AGREGADA")

    if changed_files:
        print("PLANTILLAS ACTUALIZADAS:")
        for path in changed_files:
            print(f"- {path}")
    else:
        print("No se reemplazó automáticamente el botón.")
        print("Cambia manualmente el href por:")
        print("{% url 'customer_edit' customer.id %}")


if __name__ == "__main__":
    main()
