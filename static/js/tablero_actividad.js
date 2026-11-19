// Dashboard CEO · pestaña Actividad (bloque #ceoActivityOverview en templates/dashboard/ceo.html)
// Solo dibuja: los datos ya vienen en el HTML (pedidos, movimientos, CRM y almacén de la empresa activa).
document.addEventListener("DOMContentLoaded", () => {
  "use strict";

  const root = document.getElementById("ceoActivityOverview");
  if (!root) return;

  const C = {
    navy900: "#021024", blue500: "#5483B3", blue300: "#7DA0CA", blue100: "#C1E8FF",
    text: "#EAF6FF", muted: "#9DB6D2", grid: "rgba(125,160,202,.12)", good: "#8EB69B", bad: "#F17882",
  };
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const moneyFmt = new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN", maximumFractionDigits: 2 });
  const money = (value) => moneyFmt.format(Number(value) || 0);
  const shortMoney = (value) => { const n = Number(value) || 0; const a = Math.abs(n); const sign = n < 0 ? "-" : ""; return a >= 1000 ? `${sign}$${(a / 1000).toFixed(a >= 10000 ? 0 : 1)}k` : `${sign}$${a.toFixed(0)}`; };
  const num = (value) => { const n = Number.parseFloat(String(value ?? "").replace(",", ".")); return Number.isFinite(n) ? n : 0; };

  // ---------- Venta = costo + utilidad del ultimo pedido entregado ----------
  const delivered = root.querySelector(".acx-delivered");
  if (delivered) {
    const sale = num(delivered.dataset.acxSale);
    const cost = num(delivered.dataset.acxCost);
    const costPct = sale > 0 ? Math.max(0, Math.min(100, (cost / sale) * 100)) : 0;
    const profitPct = sale > 0 ? Math.max(0, 100 - costPct) : 0;
    const costBar = delivered.querySelector('[data-acx-split="cost"]');
    const profitBar = delivered.querySelector('[data-acx-split="profit"]');
    requestAnimationFrame(() => {
      if (costBar) costBar.style.width = `${costPct}%`;
      if (profitBar) profitBar.style.width = `${profitPct}%`;
    });
  }

  // ---------- Ranking de clientes ----------
  const rankItems = [...root.querySelectorAll(".acx-rank li[data-acx-value]")];
  const maxValue = Math.max(0, ...rankItems.map((li) => num(li.dataset.acxValue)));
  requestAnimationFrame(() => {
    rankItems.forEach((li) => {
      const bar = li.querySelector(".acx-rank-bar i");
      if (bar) bar.style.width = maxValue > 0 ? `${Math.max(3, (num(li.dataset.acxValue) / maxValue) * 100)}%` : "0%";
    });
  });

  // ---------- Linea de tiempo: ordenar y filtrar ----------
  const timeline = root.querySelector("[data-acx-timeline]");
  const filterEmpty = root.querySelector("[data-acx-filter-empty]");
  if (timeline) {
    [...timeline.children]
      .sort((a, b) => num(b.dataset.acxTs) - num(a.dataset.acxTs))
      .forEach((li) => timeline.appendChild(li));
  }

  root.querySelectorAll("[data-acx-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      const kind = button.dataset.acxFilter;
      root.querySelectorAll("[data-acx-filter]").forEach((b) => { const on = b === button; b.classList.toggle("is-on", on); b.setAttribute("aria-pressed", String(on)); });
      if (!timeline) return;
      let visible = 0;
      [...timeline.children].forEach((li) => { const show = kind === "all" || li.dataset.acxKind === kind; li.hidden = !show; if (show) visible += 1; });
      if (filterEmpty) filterEmpty.hidden = visible > 0 || timeline.children.length === 0;
    });
  });

  // ---------- Grafica: utilidad de los ultimos pedidos ----------
  const canvas = document.getElementById("acxOrdersChart");
  if (!canvas || typeof Chart === "undefined") return;

  // Del mas viejo al mas nuevo, para leer de izquierda a derecha.
  const orders = [...root.querySelectorAll("[data-acx-order]")]
    .map((li) => ({
      folio: li.dataset.acxOrder, profit: num(li.dataset.acxProfit), margin: num(li.dataset.acxMargin),
      customer: li.dataset.acxCustomer, status: li.dataset.acxStatus, ts: num(li.dataset.acxTs),
    }))
    .sort((a, b) => a.ts - b.ts);

  const glow = {
    id: "acxGlow",
    beforeDatasetsDraw(chart) { const ctx = chart.ctx; ctx.save(); ctx.shadowColor = "rgba(125,160,202,.55)"; ctx.shadowBlur = 18; ctx.shadowOffsetY = 6; },
    afterDatasetsDraw(chart) { chart.ctx.restore(); },
  };
  const gradient = (chart, value) => {
    const area = chart.chartArea; if (!area) return value < 0 ? C.bad : C.blue300;
    const g = chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
    if (value < 0) { g.addColorStop(0, "rgba(241,120,130,.35)"); g.addColorStop(1, "rgba(241,120,130,.95)"); }
    else { g.addColorStop(0, "rgba(193,232,255,.98)"); g.addColorStop(1, "rgba(84,131,179,.3)"); }
    return g;
  };
  // Monto encima de cada barra.
  const valueLabels = {
    id: "acxValues",
    afterDatasetsDraw(chart) {
      const { ctx } = chart; const meta = chart.getDatasetMeta(0);
      if (meta.data.length > 10) return;
      ctx.save(); ctx.font = "600 11px 'IBM Plex Sans', system-ui, sans-serif"; ctx.textAlign = "center"; ctx.fillStyle = C.text;
      meta.data.forEach((bar, i) => { const v = orders[i].profit; ctx.textBaseline = v < 0 ? "top" : "bottom"; ctx.fillText(shortMoney(v), bar.x, v < 0 ? bar.y + 6 : bar.y - 6); });
      ctx.restore();
    },
  };

  const draw = () => new Chart(canvas, {
    type: "bar",
    data: {
      labels: orders.map((o) => o.folio),
      datasets: [{ label: "Utilidad", data: orders.map((o) => o.profit), borderRadius: 8, borderSkipped: false, maxBarThickness: 46,
        backgroundColor: (ctx) => gradient(ctx.chart, orders[ctx.dataIndex] ? orders[ctx.dataIndex].profit : 0) }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      layout: { padding: { top: 22 } },
      animation: { duration: reduced ? 0 : 1100, easing: "easeOutQuart" },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: "rgba(2,16,36,.97)", titleColor: C.text, bodyColor: C.blue100, borderColor: "rgba(125,160,202,.4)", borderWidth: 1, padding: 12, cornerRadius: 10,
          callbacks: {
            title: (items) => `Pedido ${orders[items[0].dataIndex].folio}`,
            label: (item) => { const o = orders[item.dataIndex]; return [` Utilidad: ${money(o.profit)}`, ` Margen: ${o.margin.toFixed(1)}%`, ` ${o.customer} · ${o.status}`]; },
          },
        },
      },
      scales: {
        x: { grid: { display: false }, border: { display: false }, ticks: { color: C.muted, maxRotation: 0, autoSkip: true, font: { size: 11 } } },
        y: { beginAtZero: true, grid: { color: C.grid }, border: { display: false }, ticks: { color: C.muted, font: { size: 11 }, callback: (v) => shortMoney(v) } },
      },
    },
    plugins: [glow, valueLabels],
  });

  // Se dibuja la primera vez que la pestaña se ve.
  const observer = new IntersectionObserver((entries) => {
    if (!entries.some((e) => e.isIntersecting)) return;
    observer.disconnect();
    draw();
  }, { threshold: .2 });
  observer.observe(canvas);
});
