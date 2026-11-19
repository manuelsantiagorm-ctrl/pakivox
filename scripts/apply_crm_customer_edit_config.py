from pathlib import Path
import re

ROOT = Path.cwd()
URLS_PATH = ROOT / "config" / "urls.py"

if not URLS_PATH.exists():
    raise SystemExit(f"No existe: {URLS_PATH}")

text = URLS_PATH.read_text(encoding="utf-8")

IMPORT_LINE = "from crm.customer_edit_views import customer_edit"

if IMPORT_LINE not in text:
    lines = text.splitlines()

    insert_at = 0

    for index, line in enumerate(lines):
        if line.startswith("from ") or line.startswith("import "):
            insert_at = index + 1

    lines.insert(insert_at, IMPORT_LINE)

    text = "\n".join(lines) + "\n"

MARKER = "# VISTA INTERNA PARA EDITAR CLIENTES"

if MARKER not in text:
    text += """
# VISTA INTERNA PARA EDITAR CLIENTES
urlpatterns += [
    path(
        "crm/customer/<int:customer_id>/edit/",
        customer_edit,
        name="customer_edit",
    ),
]
"""

URLS_PATH.write_text(text, encoding="utf-8")

template_roots = [
    ROOT / "templates" / "crm",
    ROOT / "crm" / "templates" / "crm",
]

changed = []

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

for template_root in template_roots:
    if not template_root.exists():
        continue

    for path in template_root.rglob("*.html"):
        original = path.read_text(encoding="utf-8")
        updated = original

        for pattern, replacement in replacements:
            updated = pattern.sub(replacement, updated)

        if (
            "Editar cliente" in updated
            and "admin/crm/customer" in updated
        ):
            updated = re.sub(
                r'href=["\'][^"\']*admin/crm/customer[^"\']*["\']',
                'href="{% url \'customer_edit\' customer.id %}"',
                updated,
                count=1,
            )

        if updated != original:
            path.write_text(updated, encoding="utf-8")
            changed.append(str(path.relative_to(ROOT)))

print("RUTA AGREGADA EN config/urls.py")

if changed:
    print("PLANTILLAS ACTUALIZADAS:")
    for item in changed:
        print("-", item)
else:
    print("NO SE LOCALIZÓ AUTOMÁTICAMENTE EL BOTÓN EDITAR.")
