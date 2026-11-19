"""
Patch de sales/views.py — 3 fixes:

1. crear_cliente_rapido_desde_pedido: fail-open -> fail-closed
   (si no hay company, el query debe quedar vacío, no abierto a todas
   las empresas).
2. abrir_chat_cliente_desde_pedido (la que SÍ queda activa, la segunda
   definición): mismo fix fail-open -> fail-closed.
3. Elimina la primera definición de abrir_chat_cliente_desde_pedido
   (código muerto, idéntica a la segunda, nunca se ejecuta porque
   Python usa la última definición del módulo).

Uso (desde la raíz del proyecto, con venv activo):
    python scripts/patch_sales_views.py

Es idempotente: si ya aplicó el patch antes, no vuelve a tocar nada
y avisa qué bloques no encontró (en vez de fallar en silencio).
"""

import sys
from pathlib import Path

TARGET = Path("sales/views.py")

if not TARGET.exists():
    print(f"ERROR: no se encontró {TARGET} desde el directorio actual.")
    print("Corre este script desde la raíz del proyecto (C:\\Users\\LENOVO\\Desktop\\WMS360).")
    sys.exit(1)

original = TARGET.read_text(encoding="utf-8")
text = original
changes_applied = []
changes_skipped = []

# --- FIX 1: crear_cliente_rapido_desde_pedido, fail-open -> fail-closed ---
old_1 = (
    "    customer_query = Customer.objects.all()\n"
    "\n"
    "    if company and model_has_field(Customer, \"company\"):\n"
    "        customer_query = customer_query.filter(company=company)\n"
    "\n"
    "    existing_customer = None"
)
new_1 = (
    "    if not company:\n"
    "        messages.error(\n"
    "            request,\n"
    "            \"No se pudo determinar la empresa del usuario actual.\",\n"
    "        )\n"
    "        return redirect(\"sales:crear_pedido\")\n"
    "\n"
    "    customer_query = Customer.objects.all()\n"
    "\n"
    "    if model_has_field(Customer, \"company\"):\n"
    "        customer_query = customer_query.filter(company=company)\n"
    "    else:\n"
    "        customer_query = customer_query.none()\n"
    "\n"
    "    existing_customer = None"
)

count_1 = text.count(old_1)
if count_1 == 1:
    text = text.replace(old_1, new_1, 1)
    changes_applied.append("FIX 1: crear_cliente_rapido_desde_pedido (fail-open -> fail-closed)")
elif count_1 == 0:
    changes_skipped.append("FIX 1: no encontrado (¿ya aplicado, o el archivo cambió?)")
else:
    changes_skipped.append(f"FIX 1: encontrado {count_1} veces (se esperaba 1) — SALTADO, revisar a mano")

# --- FIX 2: abrir_chat_cliente_desde_pedido, fail-open -> fail-closed ---
old_2 = (
    "    user_company = get_user_company(request.user)\n"
    "\n"
    "    customer_query = Customer.objects.all()\n"
    "\n"
    "    if user_company:\n"
    "        customer_query = customer_query.filter(company=user_company)\n"
    "\n"
    "    customer = get_object_or_404(\n"
    "        customer_query,\n"
    "        id=customer_id,\n"
    "    )"
)
new_2 = (
    "    user_company = get_user_company(request.user)\n"
    "\n"
    "    if not user_company:\n"
    "        messages.error(\n"
    "            request,\n"
    "            \"No se pudo determinar la empresa del usuario actual.\",\n"
    "        )\n"
    "        return redirect(\"/sales/orders/create/\")\n"
    "\n"
    "    customer_query = Customer.objects.filter(company=user_company)\n"
    "\n"
    "    customer = get_object_or_404(\n"
    "        customer_query,\n"
    "        id=customer_id,\n"
    "    )"
)

count_2 = text.count(old_2)
if count_2 >= 1:
    # Aplicamos a TODAS las ocurrencias que queden (debería ser 1,
    # ya que el FIX 3 de abajo borra la copia muerta primero en el
    # texto -- pero por si el orden de aplicación cambia, replace()
    # sin límite es seguro aquí porque ambas copias son idénticas).
    text = text.replace(old_2, new_2)
    changes_applied.append(f"FIX 2: abrir_chat_cliente_desde_pedido (fail-open -> fail-closed) x{count_2}")
else:
    changes_skipped.append("FIX 2: no encontrado (¿ya aplicado, o el archivo cambió?)")

# --- FIX 3: eliminar la definición duplicada (código muerto) ---
# Se busca el bloque completo de la PRIMERA definición (idéntica a la
# segunda) y se borra por completo, dejando solo una.
dead_block = (
    "@login_required(login_url=\"/admin/login/\")\n"
    "def abrir_chat_cliente_desde_pedido(request, customer_id):\n"
)

occurrences = text.count(dead_block)
if occurrences == 2:
    first_idx = text.find(dead_block)
    second_idx = text.find(dead_block, first_idx + 1)
    # Borramos desde el inicio del primer bloque hasta el inicio del
    # segundo bloque (así se elimina función completa duplicada).
    text = text[:first_idx] + text[second_idx:]
    changes_applied.append("FIX 3: eliminada la definición duplicada/muerta de abrir_chat_cliente_desde_pedido")
elif occurrences == 1:
    changes_skipped.append("FIX 3: solo 1 definición encontrada — probablemente ya se eliminó el duplicado")
else:
    changes_skipped.append(f"FIX 3: se encontraron {occurrences} definiciones (se esperaban 2) — SALTADO, revisar a mano")

# --- Escribir resultado ---
if text != original:
    backup_path = TARGET.with_suffix(".py.antes_del_patch_crm")
    backup_path.write_text(original, encoding="utf-8")
    TARGET.write_text(text, encoding="utf-8")
    print(f"Respaldo guardado en: {backup_path}")
    print(f"Archivo modificado: {TARGET}")
else:
    print("No se modificó nada (ver detalle abajo).")

print("\n--- Aplicados ---")
for c in changes_applied:
    print(f"  ✓ {c}")

print("\n--- Saltados / requieren revisión manual ---")
for c in changes_skipped:
    print(f"  ⚠ {c}")

print("\nSiguiente paso: python manage.py check")
