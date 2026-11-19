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

timestamp = datetime.now().strftime(
    "%Y%m%d_%H%M%S"
)

backup_path = TEMPLATE_PATH.with_name(
    f"ceo_backup_paso12_{timestamp}.html"
)

shutil.copy2(
    TEMPLATE_PATH,
    backup_path,
)

html_marker = '''
      <article
        class="ceo-ai-result-card"
        id="ceoAiWarningsCard"
        hidden
      >
'''

deep_html = '''
      <!-- PASO 12: ANÁLISIS EJECUTIVO AMPLIADO -->
      <article
        class="ceo-ai-result-card ceo-ai-deep-analysis"
        id="ceoAiDeepAnalysisCard"
        hidden
      >
        <div class="ceo-ai-result-title">
          <i class="bi bi-graph-up-arrow"></i>

          <div>
            <span>Inteligencia ejecutiva ampliada</span>
            <h3 id="ceoAiDeepTitle">
              Análisis ejecutivo ampliado
            </h3>
            <small id="ceoAiDeepSubtitle"></small>
          </div>
        </div>

        <div
          class="ceo-ai-deep-kpis"
          id="ceoAiDeepKpis"
        ></div>

        <div
          class="ceo-ai-deep-narrative"
          id="ceoAiDeepNarrative"
        ></div>

        <div class="ceo-ai-deep-columns">
          <section>
            <span class="ceo-ai-deep-eyebrow">
              Hallazgos clave
            </span>
            <ul
              class="ceo-ai-list"
              id="ceoAiDeepFindings"
            ></ul>
          </section>

          <section>
            <span class="ceo-ai-deep-eyebrow">
              Acciones prioritarias
            </span>
            <ol
              class="ceo-ai-list"
              id="ceoAiDeepActions"
            ></ol>
          </section>
        </div>

        <section
          class="ceo-ai-deep-table-section"
          id="ceoAiDeepTableSection"
          hidden
        >
          <div class="ceo-ai-deep-table-title">
            <span>Detalle operativo</span>
            <h4 id="ceoAiDeepTableTitle"></h4>
          </div>

          <div class="ceo-ai-deep-table-wrap">
            <table class="ceo-ai-deep-table">
              <thead id="ceoAiDeepTableHead"></thead>
              <tbody id="ceoAiDeepTableBody"></tbody>
            </table>
          </div>
        </section>

        <section
          class="ceo-ai-deep-charts"
          id="ceoAiDeepCharts"
        ></section>
      </article>

'''

if "id=\"ceoAiDeepAnalysisCard\"" not in text:
    if html_marker not in text:
        raise SystemExit(
            "No se encontró el bloque de advertencias para insertar el análisis."
        )

    text = text.replace(
        html_marker,
        deep_html + html_marker,
        1,
    )

deep_css = r'''

  /* PASO 12: ANÁLISIS EJECUTIVO AMPLIADO */
  .ceo-ai-deep-analysis {
    grid-column: 1 / -1;
    border-color: rgba(56, 189, 248, .28);
    background:
      radial-gradient(
        circle at top right,
        rgba(14, 165, 233, .10),
        transparent 34%
      ),
      rgba(15, 23, 42, .82);
  }

  .ceo-ai-deep-kpis {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 11px;
    margin-top: 15px;
  }

  .ceo-ai-deep-kpi {
    padding: 14px;
    border-radius: 12px;
    border: 1px solid rgba(148, 163, 184, .14);
    background: rgba(2, 6, 23, .52);
  }

  .ceo-ai-deep-kpi span {
    display: block;
    color: #94a3b8;
    font-size: 10px;
    font-weight: 900;
    letter-spacing: .06em;
    text-transform: uppercase;
  }

  .ceo-ai-deep-kpi strong {
    display: block;
    margin-top: 7px;
    color: #f8fafc;
    font-size: 21px;
  }

  .ceo-ai-deep-kpi small {
    display: block;
    margin-top: 6px;
    color: #94a3b8;
    line-height: 1.45;
  }

  .ceo-ai-deep-kpi.danger {
    border-color: rgba(239, 68, 68, .30);
  }

  .ceo-ai-deep-kpi.danger strong {
    color: #fca5a5;
  }

  .ceo-ai-deep-kpi.warning {
    border-color: rgba(245, 158, 11, .30);
  }

  .ceo-ai-deep-kpi.warning strong {
    color: #fcd34d;
  }

  .ceo-ai-deep-kpi.success {
    border-color: rgba(34, 197, 94, .26);
  }

  .ceo-ai-deep-kpi.success strong {
    color: #86efac;
  }

  .ceo-ai-deep-kpi.info {
    border-color: rgba(56, 189, 248, .28);
  }

  .ceo-ai-deep-kpi.info strong {
    color: #7dd3fc;
  }

  .ceo-ai-deep-kpi.purple {
    border-color: rgba(139, 92, 246, .30);
  }

  .ceo-ai-deep-kpi.purple strong {
    color: #c4b5fd;
  }

  .ceo-ai-deep-narrative {
    display: grid;
    gap: 10px;
    margin-top: 16px;
    padding: 15px;
    border-radius: 12px;
    color: #cbd5e1;
    background: rgba(2, 6, 23, .38);
    border: 1px solid rgba(148, 163, 184, .12);
  }

  .ceo-ai-deep-narrative p {
    margin: 0;
    line-height: 1.72;
  }

  .ceo-ai-deep-columns {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
    margin-top: 16px;
  }

  .ceo-ai-deep-columns > section {
    padding: 15px;
    border-radius: 12px;
    background: rgba(2, 6, 23, .40);
    border: 1px solid rgba(148, 163, 184, .12);
  }

  .ceo-ai-deep-eyebrow,
  .ceo-ai-deep-table-title span {
    display: block;
    margin-bottom: 10px;
    color: #67e8f9;
    font-size: 10px;
    font-weight: 950;
    letter-spacing: .07em;
    text-transform: uppercase;
  }

  .ceo-ai-deep-table-section {
    margin-top: 16px;
    padding-top: 16px;
    border-top: 1px solid rgba(148, 163, 184, .14);
  }

  .ceo-ai-deep-table-title h4 {
    margin: 0 0 12px;
    color: #f8fafc;
    font-size: 17px;
  }

  .ceo-ai-deep-table-wrap {
    overflow-x: auto;
    border-radius: 12px;
    border: 1px solid rgba(148, 163, 184, .13);
  }

  .ceo-ai-deep-table {
    width: 100%;
    min-width: 900px;
    border-collapse: collapse;
    background: rgba(2, 6, 23, .38);
  }

  .ceo-ai-deep-table th,
  .ceo-ai-deep-table td {
    padding: 11px 12px;
    text-align: left;
    border-bottom: 1px solid rgba(148, 163, 184, .10);
  }

  .ceo-ai-deep-table th {
    color: #93c5fd;
    font-size: 10px;
    font-weight: 950;
    letter-spacing: .05em;
    text-transform: uppercase;
    background: rgba(15, 23, 42, .92);
  }

  .ceo-ai-deep-table td {
    color: #dbeafe;
    font-size: 12px;
    white-space: nowrap;
  }

  .ceo-ai-deep-table tbody tr:hover {
    background: rgba(56, 189, 248, .06);
  }

  .ceo-ai-deep-charts {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 14px;
    margin-top: 16px;
  }

  .ceo-ai-deep-chart-card {
    min-width: 0;
    padding: 14px;
    border-radius: 12px;
    background: rgba(2, 6, 23, .42);
    border: 1px solid rgba(139, 92, 246, .20);
  }

  .ceo-ai-deep-chart-card h4 {
    margin: 0 0 10px;
    color: #f8fafc;
    font-size: 14px;
  }

  .ceo-ai-deep-chart-box {
    height: 280px;
  }

  #ceoAiDeepSubtitle {
    display: block;
    margin-top: 4px;
    color: #94a3b8;
  }

  @media (max-width: 1000px) {
    .ceo-ai-deep-kpis {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .ceo-ai-deep-columns,
    .ceo-ai-deep-charts {
      grid-template-columns: 1fr;
    }
  }

  @media (max-width: 620px) {
    .ceo-ai-deep-kpis {
      grid-template-columns: 1fr;
    }
  }
'''

if "/* PASO 12: ANÁLISIS EJECUTIVO AMPLIADO */" not in text:
    style_end = text.rfind("</style>")

    if style_end == -1:
        raise SystemExit(
            "No se encontró el cierre del bloque style."
        )

    text = (
        text[:style_end]
        + deep_css
        + "\n"
        + text[style_end:]
    )

chart_variable = "  let ceoAiChartInstance = null;"

if "ceoAiDeepChartInstances" not in text:
    if chart_variable not in text:
        raise SystemExit(
            "No se encontró la variable de Chart principal."
        )

    text = text.replace(
        chart_variable,
        (
            chart_variable
            + "\n"
            + "  let ceoAiDeepChartInstances = [];"
        ),
        1,
    )

deep_js = r'''
  // =========================================================
  // PASO 12: ANÁLISIS EJECUTIVO AMPLIADO
  // =========================================================

  function clearDeepCharts() {
    ceoAiDeepChartInstances.forEach(
      function (chart) {
        try {
          chart.destroy();
        } catch (error) {
          console.warn(
            "No se pudo destruir una gráfica ampliada.",
            error
          );
        }
      }
    );

    ceoAiDeepChartInstances = [];
  }

  function createDeepList(
    elementId,
    values
  ) {
    const element = document.getElementById(
      elementId
    );

    if (!element) {
      return;
    }

    element.replaceChildren();

    (Array.isArray(values) ? values : []).forEach(
      function (value) {
        const item = document.createElement("li");
        item.textContent = String(value || "");
        element.appendChild(item);
      }
    );
  }

  function renderDeepAnalysis(deep) {
    const card = document.getElementById(
      "ceoAiDeepAnalysisCard"
    );

    if (!card) {
      return;
    }

    clearDeepCharts();

    const visible = Boolean(
      deep
      && typeof deep === "object"
    );

    card.hidden = !visible;
    card.style.display = visible ? "" : "none";

    if (!visible) {
      return;
    }

    setText(
      "ceoAiDeepTitle",
      deep.title || "Análisis ejecutivo ampliado"
    );

    setText(
      "ceoAiDeepSubtitle",
      deep.subtitle || ""
    );

    const kpiContainer = document.getElementById(
      "ceoAiDeepKpis"
    );

    if (kpiContainer) {
      kpiContainer.replaceChildren();

      (
        Array.isArray(deep.kpis)
          ? deep.kpis
          : []
      ).forEach(function (kpi) {
        const box = document.createElement("article");
        box.className = (
          "ceo-ai-deep-kpi "
          + (
            kpi.tone
            || "info"
          )
        );

        const label = document.createElement("span");
        label.textContent = kpi.label || "Indicador";

        const value = document.createElement("strong");
        value.textContent = (
          kpi.value === null
          || kpi.value === undefined
        )
          ? "-"
          : String(kpi.value);

        const detail = document.createElement("small");
        detail.textContent = kpi.detail || "";

        box.appendChild(label);
        box.appendChild(value);
        box.appendChild(detail);
        kpiContainer.appendChild(box);
      });
    }

    const narrative = document.getElementById(
      "ceoAiDeepNarrative"
    );

    if (narrative) {
      narrative.replaceChildren();

      (
        Array.isArray(deep.narrative)
          ? deep.narrative
          : []
      ).forEach(function (paragraph) {
        const p = document.createElement("p");
        p.textContent = String(paragraph || "");
        narrative.appendChild(p);
      });
    }

    createDeepList(
      "ceoAiDeepFindings",
      deep.findings
    );

    createDeepList(
      "ceoAiDeepActions",
      deep.actions
    );

    const tableSection = document.getElementById(
      "ceoAiDeepTableSection"
    );
    const tableHead = document.getElementById(
      "ceoAiDeepTableHead"
    );
    const tableBody = document.getElementById(
      "ceoAiDeepTableBody"
    );

    const table = (
      deep.table
      && Array.isArray(deep.table.headers)
      && Array.isArray(deep.table.rows)
    )
      ? deep.table
      : null;

    if (
      tableSection
      && tableHead
      && tableBody
    ) {
      const showTable = Boolean(
        table
        && table.headers.length
        && table.rows.length
      );

      tableSection.hidden = !showTable;
      tableSection.style.display = (
        showTable
          ? ""
          : "none"
      );

      tableHead.replaceChildren();
      tableBody.replaceChildren();

      if (showTable) {
        setText(
          "ceoAiDeepTableTitle",
          table.title || "Detalle operativo"
        );

        const headerRow = document.createElement("tr");

        table.headers.forEach(
          function (header) {
            const th = document.createElement("th");
            th.textContent = String(header || "");
            headerRow.appendChild(th);
          }
        );

        tableHead.appendChild(headerRow);

        table.rows.forEach(
          function (row) {
            const tr = document.createElement("tr");

            (
              Array.isArray(row)
                ? row
                : []
            ).forEach(function (cell) {
              const td = document.createElement("td");
              td.textContent = (
                cell === null
                || cell === undefined
              )
                ? "-"
                : String(cell);
              tr.appendChild(td);
            });

            tableBody.appendChild(tr);
          }
        );
      }
    }

    const chartsContainer = document.getElementById(
      "ceoAiDeepCharts"
    );

    if (!chartsContainer) {
      return;
    }

    chartsContainer.replaceChildren();

    (
      Array.isArray(deep.graphs)
        ? deep.graphs
        : []
    ).slice(0, 4).forEach(
      function (graph, index) {
        if (
          !graph
          || !Array.isArray(graph.etiquetas)
          || !Array.isArray(graph.valores)
          || !graph.etiquetas.length
          || graph.etiquetas.length
            !== graph.valores.length
        ) {
          return;
        }

        const article = document.createElement("article");
        article.className = "ceo-ai-deep-chart-card";

        const title = document.createElement("h4");
        title.textContent = (
          graph.titulo
          || "Visualización ejecutiva"
        );

        const box = document.createElement("div");
        box.className = "ceo-ai-deep-chart-box";

        const canvas = document.createElement("canvas");
        canvas.id = (
          "ceoAiDeepChart"
          + index
        );

        box.appendChild(canvas);
        article.appendChild(title);
        article.appendChild(box);
        chartsContainer.appendChild(article);

        const validTypes = [
          "bar",
          "line",
          "pie",
          "doughnut"
        ];

        const chartType = validTypes.includes(
          graph.tipo_grafica
        )
          ? graph.tipo_grafica
          : "bar";

        const instance = new Chart(
          canvas,
          {
            type: chartType,
            data: {
              labels: graph.etiquetas,
              datasets: [
                {
                  label: (
                    graph.titulo_eje
                    || "Valor"
                  ),
                  data: graph.valores,
                  borderWidth: 2,
                  borderRadius: 7,
                  backgroundColor: [
                    "rgba(139, 92, 246, .70)",
                    "rgba(56, 189, 248, .70)",
                    "rgba(34, 197, 94, .70)",
                    "rgba(245, 158, 11, .70)",
                    "rgba(239, 68, 68, .70)",
                    "rgba(6, 182, 212, .70)",
                    "rgba(236, 72, 153, .70)",
                    "rgba(99, 102, 241, .70)",
                    "rgba(20, 184, 166, .70)",
                    "rgba(244, 63, 94, .70)",
                    "rgba(132, 204, 22, .70)",
                    "rgba(251, 146, 60, .70)"
                  ],
                  borderColor: (
                    "rgba(196, 181, 253, 1)"
                  )
                }
              ]
            },
            options: {
              responsive: true,
              maintainAspectRatio: false,
              plugins: {
                legend: {
                  labels: {
                    color: "#cbd5e1"
                  }
                }
              },
              scales: (
                chartType === "pie"
                || chartType === "doughnut"
              )
                ? {}
                : {
                    x: {
                      ticks: {
                        color: "#cbd5e1"
                      },
                      grid: {
                        color: (
                          "rgba(255,255,255,.05)"
                        )
                      }
                    },
                    y: {
                      beginAtZero: true,
                      ticks: {
                        color: "#cbd5e1"
                      },
                      grid: {
                        color: (
                          "rgba(255,255,255,.05)"
                        )
                      }
                    }
                  }
            }
          }
        );

        ceoAiDeepChartInstances.push(
          instance
        );
      }
    );
  }

'''

js_marker = '''  // =========================================================
  // ADVERTENCIAS
  // =========================================================
'''

if "function renderDeepAnalysis(deep)" not in text:
    if js_marker not in text:
        raise SystemExit(
            "No se encontró el bloque de advertencias en JavaScript."
        )

    text = text.replace(
        js_marker,
        deep_js + js_marker,
        1,
    )

render_marker = '''    renderChart(
      showChart
        ? graph
        : null
    );

    renderWarnings(
      result.advertencias || []
    );
'''

render_replacement = '''    renderChart(
      showChart
        ? graph
        : null
    );

    renderDeepAnalysis(
      result.deep_analysis || null
    );

    renderWarnings(
      result.advertencias || []
    );
'''

if "result.deep_analysis || null" not in text:
    if render_marker not in text:
        raise SystemExit(
            "No se encontró el render principal para conectar el análisis."
        )

    text = text.replace(
        render_marker,
        render_replacement,
        1,
    )

TEMPLATE_PATH.write_text(
    text,
    encoding="utf-8",
)

print("PLANTILLA PASO 12 ACTUALIZADA")
print(f"Respaldo: {backup_path}")
