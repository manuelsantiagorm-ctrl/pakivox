from pathlib import Path
from datetime import datetime
import shutil


ROOT = Path.cwd()
TEMPLATE_PATH = ROOT / "templates" / "dashboard" / "ceo.html"

if not TEMPLATE_PATH.exists():
    raise SystemExit(
        f"No existe la plantilla: {TEMPLATE_PATH}"
    )

text = TEMPLATE_PATH.read_text(
    encoding="utf-8"
)

stock_marker = "function renderStock(stockItems) {"

if stock_marker not in text:
    raise SystemExit(
        "No se encontró la función renderStock."
    )

before_stock, stock_section = text.split(
    stock_marker,
    1,
)

bad_start = (
    '      const forecast = document.createElement("small");'
)

bad_end = (
    "      titleBox.appendChild(forecast);"
)

start_index = before_stock.find(
    bad_start
)

if start_index != -1:
    end_index = before_stock.find(
        bad_end,
        start_index,
    )

    if end_index == -1:
        raise SystemExit(
            "Se encontró el bloque incorrecto, pero no su final."
        )

    end_index += len(bad_end)

    before_stock = (
        before_stock[:start_index]
        + before_stock[end_index:]
    )

forecast_block = '''
      const forecast = document.createElement("small");
      forecast.className = "ceo-ai-stock-forecast";

      forecast.textContent = (
        "Pronóstico 15 días: demanda "
        + Number(
          stock.expected_demand_internal_15d ?? 0
        ).toFixed(1)
        + " · compras esperadas "
        + Number(
          stock.incoming_internal_15d ?? 0
        ).toFixed(0)
        + " · saldo proyectado "
        + Number(
          stock.projected_available_internal_15d ?? 0
        ).toFixed(1)
        + " piezas"
      );

      if (
        Number(
          stock.overdue_purchase_internal ?? 0
        ) > 0
      ) {
        forecast.textContent += (
          " · compra vencida pendiente "
          + Number(
            stock.overdue_purchase_internal
          ).toFixed(0)
        );
      }
'''

if "ceo-ai-stock-forecast" not in stock_section:
    insert_target = (
        "      titleBox.appendChild(title);\n"
        "      titleBox.appendChild(subtitle);"
    )

    if insert_target not in stock_section:
        raise SystemExit(
            "No se encontró el punto de inserción en renderStock."
        )

    insert_replacement = (
        insert_target
        + "\n"
        + forecast_block
        + "\n"
        + "      titleBox.appendChild(forecast);"
    )

    stock_section = stock_section.replace(
        insert_target,
        insert_replacement,
        1,
    )

new_text = (
    before_stock
    + stock_marker
    + stock_section
)

if "stock.expected_demand_internal_15d" in before_stock:
    raise SystemExit(
        "La referencia incorrecta a stock sigue fuera de renderStock."
    )

timestamp = datetime.now().strftime(
    "%Y%m%d_%H%M%S"
)

backup_path = TEMPLATE_PATH.with_name(
    f"ceo_backup_hotfix_paso11_{timestamp}.html"
)

shutil.copy2(
    TEMPLATE_PATH,
    backup_path,
)

TEMPLATE_PATH.write_text(
    new_text,
    encoding="utf-8",
)

print("HOTFIX PASO 11 APLICADO CORRECTAMENTE")
print(f"Respaldo: {backup_path}")
print(
    "La variable stock ahora se usa solamente dentro de renderStock."
)
