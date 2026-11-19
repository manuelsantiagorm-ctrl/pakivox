from pathlib import Path
from datetime import datetime
import py_compile
import shutil


ROOT = Path.cwd()
VIEWS_PATH = ROOT / "dashboard" / "views.py"

if not VIEWS_PATH.exists():
    raise SystemExit(
        f"No existe el archivo: {VIEWS_PATH}"
    )

text = VIEWS_PATH.read_text(
    encoding="utf-8"
)

if "def ceo_ai_export_excel(" not in text:
    raise SystemExit(
        "No se encontró la vista ceo_ai_export_excel. "
        "Confirma que el Paso 13 esté instalado."
    )

timestamp = datetime.now().strftime(
    "%Y%m%d_%H%M%S"
)

backup_path = VIEWS_PATH.with_name(
    f"views_backup_hotfix_paso13_{timestamp}.py"
)

shutil.copy2(
    VIEWS_PATH,
    backup_path,
)

if not any(
    line.strip() == "import re"
    for line in text.splitlines()
):
    lines = text.splitlines()

    insert_index = 0

    # Inserta el import junto a los imports estándar, antes de Django.
    for index, line in enumerate(lines):
        stripped = line.strip()

        if (
            stripped.startswith("from django")
            or stripped.startswith("import django")
        ):
            insert_index = index
            break

    lines.insert(
        insert_index,
        "import re",
    )

    text = "\n".join(lines) + "\n"

VIEWS_PATH.write_text(
    text,
    encoding="utf-8",
)

py_compile.compile(
    str(VIEWS_PATH),
    doraise=True,
)

print("HOTFIX PASO 13 APLICADO CORRECTAMENTE")
print("Se agregó el import faltante: import re")
print(f"Respaldo: {backup_path}")
