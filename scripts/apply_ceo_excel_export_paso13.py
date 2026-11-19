from pathlib import Path
from datetime import datetime
import shutil


ROOT = Path.cwd()
VIEWS_PATH = ROOT / "dashboard" / "views.py"
URLS_PATH = ROOT / "dashboard" / "urls.py"
TEMPLATE_PATH = ROOT / "templates" / "dashboard" / "ceo.html"

for path in (VIEWS_PATH, URLS_PATH, TEMPLATE_PATH):
    if not path.exists():
        raise SystemExit(f"No existe: {path}")

timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_root = ROOT / "backups" / f"paso13_excel_dinamico_{timestamp}"

for source in (VIEWS_PATH, URLS_PATH, TEMPLATE_PATH):
    destination = backup_root / source.relative_to(ROOT)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)

# ============================================================
# dashboard/views.py
# ============================================================

views = VIEWS_PATH.read_text(encoding="utf-8")

if "import uuid" not in views:
    views = views.replace("import json\n", "import json\nimport uuid\n", 1)

if "import re" not in views:
    views = views.replace("import uuid\n", "import uuid\nimport re\n", 1)

views = views.replace(
    "from django.http import JsonResponse\n",
    "from django.http import HttpResponse, JsonResponse\n",
    1,
)

export_import = '''from dashboard.services.ceo_excel_export import (
    build_ceo_analysis_workbook,
    build_ceo_export_filename,
)

'''

if "build_ceo_analysis_workbook" not in views:
    marker = "from dashboard.services.ceo_ai import (\n"
    if marker not in views:
        raise SystemExit("No se encontró el import de ceo_ai en views.py.")
    views = views.replace(marker, export_import + marker, 1)

success_block = '''        return JsonResponse(
            {
                "ok": True,
                "result": result,
            },
            json_dumps_params={"ensure_ascii": False},
        )
'''

success_replacement = '''        export_token = uuid.uuid4().hex
        export_cache_key = (
            f"ceo_ai_export:{company.pk}:"
            f"{request.user.pk}:{export_token}"
        )

        cache.set(
            export_cache_key,
            {
                "result": result,
                "question": question,
                "mode": mode,
                "date_from": date_from,
                "date_to": date_to,
                "company_name": str(company),
                "user_display": (
                    request.user.get_full_name()
                    or request.user.get_username()
                ),
                "generated_at": timezone.localtime().strftime(
                    "%d/%m/%Y %H:%M:%S"
                ),
            },
            timeout=900,
        )

        return JsonResponse(
            {
                "ok": True,
                "result": result,
                "export_token": export_token,
                "export_expires_seconds": 900,
            },
            json_dumps_params={"ensure_ascii": False},
        )
'''

if "export_expires_seconds" not in views:
    if success_block not in views:
        raise SystemExit("No se encontró la respuesta exitosa de ceo_ai_query.")
    views = views.replace(success_block, success_replacement, 1)

export_view = r'''

@login_required
@require_POST
def ceo_ai_export_excel(request):
    """
    Exporta exactamente el último análisis generado por el usuario.

    Seguridad:
    - No acepta resultados ni cifras desde el navegador.
    - No acepta company_id ni warehouse_id.
    - Recupera el resultado usando empresa, usuario y token.
    - El token expira en 15 minutos.
    """

    company = get_user_company(request.user)

    if not company:
        return JsonResponse(
            {
                "ok": False,
                "error": "Tu usuario no tiene una empresa asignada.",
            },
            status=403,
            json_dumps_params={"ensure_ascii": False},
        )

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse(
            {
                "ok": False,
                "error": "La solicitud no contiene JSON válido.",
            },
            status=400,
            json_dumps_params={"ensure_ascii": False},
        )

    export_token = str(payload.get("export_token", "") or "").strip().lower()

    if not re.fullmatch(r"[a-f0-9]{32}", export_token):
        return JsonResponse(
            {
                "ok": False,
                "error": "El token de exportación no es válido.",
            },
            status=400,
            json_dumps_params={"ensure_ascii": False},
        )

    cache_key = (
        f"ceo_ai_export:{company.pk}:"
        f"{request.user.pk}:{export_token}"
    )
    export_context = cache.get(cache_key)

    if not export_context:
        return JsonResponse(
            {
                "ok": False,
                "error": (
                    "El enlace de exportación expiró. "
                    "Genera nuevamente el análisis."
                ),
            },
            status=410,
            json_dumps_params={"ensure_ascii": False},
        )

    try:
        workbook_bytes = build_ceo_analysis_workbook(export_context)
        filename = build_ceo_export_filename(export_context)
    except Exception as exc:
        logger.exception("No fue posible generar el Excel del Copiloto CEO.")
        error_message = "No fue posible construir el Excel profesional."

        if settings.DEBUG:
            error_message = f"{error_message} Detalle técnico: {exc}"

        return JsonResponse(
            {
                "ok": False,
                "error": error_message,
            },
            status=500,
            json_dumps_params={"ensure_ascii": False},
        )

    response = HttpResponse(
        workbook_bytes,
        content_type=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response
'''

if "def ceo_ai_export_excel(" not in views:
    marker = (
        '\n\n@login_required(login_url="/admin/login/")\n'
        "def control_flow_dashboard(request):"
    )
    if marker not in views:
        raise SystemExit("No se encontró control_flow_dashboard.")
    views = views.replace(marker, export_view + marker, 1)

VIEWS_PATH.write_text(views, encoding="utf-8")

# ============================================================
# dashboard/urls.py
# ============================================================

urls = URLS_PATH.read_text(encoding="utf-8")

if 'name="ceo_ai_export_excel"' not in urls:
    marker = '''    path(
        "ia/",
        views.ceo_ai_query,
        name="ceo_ai_query",
    ),
'''
    replacement = marker + '''    path(
        "ia/exportar-excel/",
        views.ceo_ai_export_excel,
        name="ceo_ai_export_excel",
    ),
'''
    if marker not in urls:
        raise SystemExit("No se encontró la ruta ceo_ai_query.")
    urls = urls.replace(marker, replacement, 1)

URLS_PATH.write_text(urls, encoding="utf-8")

# ============================================================
# templates/dashboard/ceo.html
# ============================================================

template = TEMPLATE_PATH.read_text(encoding="utf-8")

if "data-export-endpoint" not in template:
    template = template.replace(
        '      data-endpoint="/ceo/ia/"\n',
        (
            '      data-endpoint="/ceo/ia/"\n'
            '      data-export-endpoint="'
            "{% url 'dashboard:ceo_ai_export_excel' %}"
            '"\n'
        ),
        1,
    )

export_html = '''
        <div class="ceo-ai-export-toolbar">
          <button
            type="button"
            class="ceo-ai-export-excel"
            id="ceoAiExportExcel"
            hidden
          >
            <i class="bi bi-file-earmark-excel"></i>
            <span>Exportar Excel profesional</span>
          </button>

          <small id="ceoAiExportHint" hidden>
            Incluye resumen, datos, tablas, bodegas, gráficas y auditoría.
          </small>
        </div>
'''

if 'id="ceoAiExportExcel"' not in template:
    marker = '        <p id="ceoAiSummary"></p>\n'
    if marker not in template:
        raise SystemExit("No se encontró ceoAiSummary en la plantilla.")
    template = template.replace(marker, marker + export_html, 1)

export_css = r'''

  /* PASO 13: EXPORTACIÓN EXCEL PROFESIONAL */
  .ceo-ai-export-toolbar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
    margin-top: 14px;
  }

  .ceo-ai-export-excel {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    min-height: 40px;
    padding: 9px 15px;
    border: 1px solid rgba(34, 197, 94, .48);
    border-radius: 10px;
    color: #dcfce7;
    background: linear-gradient(
      135deg,
      rgba(22, 163, 74, .34),
      rgba(5, 150, 105, .20)
    );
    box-shadow: 0 10px 24px rgba(5, 150, 105, .10);
    cursor: pointer;
    font-weight: 900;
    transition: transform .16s ease, border-color .16s ease, background .16s ease;
  }

  .ceo-ai-export-excel:hover {
    transform: translateY(-1px);
    border-color: rgba(74, 222, 128, .78);
    background: linear-gradient(
      135deg,
      rgba(22, 163, 74, .48),
      rgba(5, 150, 105, .30)
    );
  }

  .ceo-ai-export-excel:disabled {
    opacity: .65;
    cursor: wait;
    transform: none;
  }

  #ceoAiExportHint {
    color: #86efac;
    line-height: 1.45;
  }
'''

if "/* PASO 13: EXPORTACIÓN EXCEL PROFESIONAL */" not in template:
    style_end = template.rfind("</style>")
    if style_end == -1:
        raise SystemExit("No se encontró el cierre style.")
    template = template[:style_end] + export_css + "\n" + template[style_end:]

const_marker = '  const warehouseScopeBox = document.getElementById("ceoAiWarehouseScope");\n'
const_replacement = const_marker + '''  const exportExcelButton = document.getElementById(
    "ceoAiExportExcel"
  );
  const exportExcelHint = document.getElementById(
    "ceoAiExportHint"
  );
'''

if "const exportExcelButton" not in template:
    if const_marker not in template:
        raise SystemExit("No se encontró warehouseScopeBox.")
    template = template.replace(const_marker, const_replacement, 1)

if "let ceoAiExportToken = null;" not in template:
    marker = "  let ceoAiChartInstance = null;\n"
    if marker not in template:
        raise SystemExit("No se encontró ceoAiChartInstance.")
    template = template.replace(marker, marker + "  let ceoAiExportToken = null;\n", 1)

export_js = r'''
  // =========================================================
  // EXPORTAR EXCEL PROFESIONAL
  // =========================================================

  function resetExcelExport() {
    ceoAiExportToken = null;

    if (exportExcelButton) {
      exportExcelButton.hidden = true;
      exportExcelButton.style.display = "none";
      exportExcelButton.disabled = false;

      const label = exportExcelButton.querySelector("span");
      if (label) {
        label.textContent = "Exportar Excel profesional";
      }
    }

    if (exportExcelHint) {
      exportExcelHint.hidden = true;
      exportExcelHint.style.display = "none";
    }
  }

  function enableExcelExport(token) {
    ceoAiExportToken = String(token || "").trim();
    const enabled = Boolean(ceoAiExportToken);

    if (exportExcelButton) {
      exportExcelButton.hidden = !enabled;
      exportExcelButton.style.display = enabled ? "inline-flex" : "none";
      exportExcelButton.disabled = false;
    }

    if (exportExcelHint) {
      exportExcelHint.hidden = !enabled;
      exportExcelHint.style.display = enabled ? "" : "none";
    }
  }

  function resolveDownloadFilename(response) {
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="?([^";]+)"?/i);
    return match ? match[1] : "WMS360_Copiloto_CEO.xlsx";
  }

  if (exportExcelButton) {
    exportExcelButton.addEventListener("click", async function () {
      hideError();

      if (!ceoAiExportToken) {
        showError("Genera nuevamente el análisis antes de exportar.");
        return;
      }

      const endpoint = form.dataset.exportEndpoint;
      if (!endpoint) {
        showError("No se configuró la ruta de exportación Excel.");
        return;
      }

      const label = exportExcelButton.querySelector("span");
      const originalText = label
        ? label.textContent
        : "Exportar Excel profesional";

      exportExcelButton.disabled = true;
      if (label) {
        label.textContent = "Construyendo Excel...";
      }

      try {
        const response = await fetch(endpoint, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": getCsrfToken(),
            "X-Requested-With": "XMLHttpRequest"
          },
          credentials: "same-origin",
          body: JSON.stringify({
            export_token: ceoAiExportToken
          })
        });

        if (!response.ok) {
          let message = "No fue posible generar el Excel.";
          try {
            const errorData = await response.json();
            message = errorData.error || message;
          } catch (jsonError) {
            console.error("Error no JSON en exportación.", jsonError);
          }
          throw new Error(message);
        }

        const blob = await response.blob();
        const filename = resolveDownloadFilename(response);
        const downloadUrl = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = downloadUrl;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();

        setTimeout(function () {
          URL.revokeObjectURL(downloadUrl);
        }, 1000);

        if (label) {
          label.textContent = "Excel descargado";
        }

        setTimeout(function () {
          if (label) {
            label.textContent = originalText;
          }
        }, 1800);

      } catch (error) {
        console.error("Error exportando Excel:", error);
        showError(error.message || "No fue posible generar el Excel.");
        if (label) {
          label.textContent = originalText;
        }
      } finally {
        exportExcelButton.disabled = false;
      }
    });
  }

'''

submit_marker = '''  // =========================================================
  // ENVIAR CONSULTA AL COPILOTO
  // =========================================================
'''

if "function resetExcelExport()" not in template:
    if submit_marker not in template:
        raise SystemExit("No se encontró el bloque de envío.")
    template = template.replace(submit_marker, export_js + submit_marker, 1)

reset_marker = '''      hideError();

      if (loadingBox) {
'''
reset_replacement = '''      hideError();
      resetExcelExport();

      if (loadingBox) {
'''

if "hideError();\n      resetExcelExport();" not in template:
    if reset_marker not in template:
        raise SystemExit("No se encontró el inicio del submit.")
    template = template.replace(reset_marker, reset_replacement, 1)

render_marker = '''        renderResult(
          data.result || {}
        );
'''
render_replacement = '''        renderResult(
          data.result || {}
        );

        enableExcelExport(
          data.export_token || ""
        );
'''

if 'data.export_token || ""' not in template:
    if render_marker not in template:
        raise SystemExit("No se encontró renderResult en el submit.")
    template = template.replace(render_marker, render_replacement, 1)

TEMPLATE_PATH.write_text(template, encoding="utf-8")

print("PASO 13 APLICADO CORRECTAMENTE")
print(f"Respaldo: {backup_root}")
print("Botón dinámico de Excel agregado.")
print("El token de exportación expira en 15 minutos.")
