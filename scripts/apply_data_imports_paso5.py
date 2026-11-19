from pathlib import Path
import sys


ROOT = Path.cwd()
URLS = ROOT / "data_imports" / "urls.py"
DETAIL = ROOT / "data_imports" / "templates" / "data_imports" / "job_detail.html"


def patch_urls():
    text = URLS.read_text(encoding="utf-8")

    if "from . import pending_views" not in text:
        if "from . import views" in text:
            text = text.replace(
                "from . import views",
                "from . import views\nfrom . import pending_views",
                1,
            )
        else:
            text = "from . import pending_views\n" + text

    marker = "# PASO 5 - CLIENTES NO CREADOS"
    if marker not in text:
        routes = '''
    # PASO 5 - CLIENTES NO CREADOS
    path(
        "<int:pk>/pendientes/",
        pending_views.pending_customers,
        name="pending_customers",
    ),
    path(
        "<int:pk>/pendientes/reporte/",
        pending_views.download_pending_report,
        name="download_pending_report",
    ),
    path(
        "<int:pk>/pendientes/<int:row_pk>/editar/",
        pending_views.edit_pending_customer,
        name="edit_pending_customer",
    ),
    path(
        "<int:pk>/pendientes/<int:row_pk>/importar/",
        pending_views.import_pending_customer,
        name="import_pending_customer",
    ),
'''
        index = text.rfind("]")
        if index == -1:
            raise RuntimeError("No se encontró urlpatterns.")
        text = text[:index] + routes + text[index:]

    URLS.write_text(text, encoding="utf-8")


def patch_detail():
    text = DETAIL.read_text(encoding="utf-8")
    marker = "<!-- PASO 5 - CLIENTES NO CREADOS -->"
    if marker in text:
        return

    block = '''
  <!-- PASO 5 - CLIENTES NO CREADOS -->
  {% if job.status == "completed" or job.error_rows or job.duplicate_rows %}
    <section class="pending-resolution-banner">
      <div>
        <strong>Revisa los clientes que no fueron creados</strong>
        <p>
          Consulta errores, clientes ya existentes,
          direcciones omitidas y correcciones pendientes.
        </p>
      </div>

      <div class="pending-resolution-actions">
        <a href="{% url 'data_imports:pending_customers' job.pk %}">
          Resolver clientes no importados
        </a>

        <a
          href="{% url 'data_imports:download_pending_report' job.pk %}"
          class="secondary"
        >
          Descargar reporte
        </a>
      </div>
    </section>
  {% endif %}
'''

    anchor = '{% if job.status == "ready" %}'
    if anchor in text:
        text = text.replace(anchor, block + "\n" + anchor, 1)
    else:
        index = text.rfind("{% endblock %}")
        if index == -1:
            raise RuntimeError("No se encontró endblock.")
        text = text[:index] + block + "\n" + text[index:]

    css = '''
<style>
  .pending-resolution-banner{
    padding:17px;display:flex;justify-content:space-between;align-items:center;
    gap:14px;border:1px solid rgba(248,113,113,.22);border-radius:18px;
    background:rgba(127,29,29,.12)
  }
  .pending-resolution-banner strong{color:#fecaca}
  .pending-resolution-banner p{margin:4px 0 0;color:#94a3b8}
  .pending-resolution-actions{display:flex;gap:8px;flex-wrap:wrap}
  .pending-resolution-actions a{
    min-height:40px;padding:0 13px;border-radius:10px;display:inline-flex;
    align-items:center;justify-content:center;color:#fff;background:#dc2626;
    text-decoration:none;font-weight:900
  }
  .pending-resolution-actions a.secondary{
    color:#cbd5e1;background:transparent;border:1px solid rgba(148,163,184,.22)
  }
  @media(max-width:780px){
    .pending-resolution-banner{align-items:stretch;flex-direction:column}
  }
</style>
'''
    index = text.rfind("{% endblock %}")
    text = text[:index] + css + "\n" + text[index:]
    DETAIL.write_text(text, encoding="utf-8")


def main():
    missing = [str(path) for path in (URLS, DETAIL) if not path.exists()]
    if missing:
        print("FALTAN ARCHIVOS:", *missing, sep="\n- ")
        sys.exit(1)

    patch_urls()
    patch_detail()
    print("PASO 5 APLICADO CORRECTAMENTE")


if __name__ == "__main__":
    main()
