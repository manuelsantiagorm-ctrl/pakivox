// Dashboard CEO · pestaña Finanzas (bloque #financeExecutiveV8 en templates/dashboard/ceo.html)
// Solo dibuja: todas las cifras vienen ya calculadas del libro financiero (vista ceo_dashboard).
// Las graficas se dibujan la primera vez que la pestaña se ve.
document.addEventListener("DOMContentLoaded", () => {
  "use strict";

  const root = document.getElementById("financeExecutiveV8");
  if (!root) return;

  const C = {
    navy900: "#021024", blue500: "#5483B3", blue300: "#7DA0CA", blue100: "#C1E8FF",
    text: "#EAF6FF", muted: "#9DB6D2", grid: "rgba(125,160,202,.12)",
    good: "#8EB69B", warn: "#F0B856", bad: "#F17882", track: "rgba(84,131,179,.2)",
  };
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const moneyFmt = new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN", maximumFractionDigits: 2 });
  const money = (value) => moneyFmt.format(Number(value) || 0);
  const shortMoney = (value) => { const n = Number(value) || 0; return Math.abs(n) >= 1000 ? `$${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : `$${n.toFixed(0)}`; };
  const num = (value) => { const n = Number.parseFloat(value); return Number.isFinite(n) ? n : 0; };
  const series = (name) => (Array.isArray(ceoData(name)) ? ceoData(name).map(num) : []);
  const cumulative = (values) => { let total = 0; return values.map((v) => (total += v)); };
  const tooltip = {
    backgroundColor: "rgba(2,16,36,.97)", titleColor: C.text, bodyColor: C.blue100,
    borderColor: "rgba(125,160,202,.4)", borderWidth: 1, padding: 12, cornerRadius: 10,
    boxPadding: 4, usePointStyle: true,
  };

  // Resplandor detras de lineas y barras: profundidad sin deformar los datos.
  const glow = {
    id: "fnxGlow",
    beforeDatasetsDraw(chart) { const ctx = chart.ctx; ctx.save(); ctx.shadowColor = "rgba(125,160,202,.5)"; ctx.shadowBlur = 16; ctx.shadowOffsetY = 6; },
    afterDatasetsDraw(chart) { chart.ctx.restore(); },
  };
  const fill = (chart, top, bottom) => {
    const area = chart.chartArea; if (!area) return top;
    const g = chart.ctx.createLinearGradient(0, area.top, 0, area.bottom);
    g.addColorStop(0, top); g.addColorStop(1, bottom); return g;
  };

  // ---------- Datos ----------
  const labels = ceoData("finance_trend_labels");
  const sales = series("finance_trend_sales");
  const collected = series("finance_trend_collected");
  const profit = series("finance_trend_profit");
  const paid = num(root.dataset.paidTotal);
  const due = num(root.dataset.balanceDue);
  const collectedPct = paid + due > 0 ? Math.round((paid / (paid + due)) * 100) : 0;
  const pctNode = root.querySelector("[data-fnx-collected-pct]");
  if (pctNode) pctNode.textContent = `${collectedPct}%`;

  const marginBar = root.querySelector("[data-fnx-margin]");
  const margin = marginBar ? Math.max(0, Math.min(100, num(marginBar.dataset.fnxMargin))) : 0;
  const per100 = root.querySelector("[data-fnx-per-100]");
  if (per100) per100.textContent = money(margin);

  // ---------- Graficas ----------
  let trendChart = null;

  const charts = {
    fnxSparkProfit(canvas) { spark(canvas, profit, C.good); },
    fnxSparkSales(canvas) { spark(canvas, sales, C.blue300); },
    fnxSparkCollected(canvas) { spark(canvas, collected, C.blue100); },

    profitabilityTrendChart(canvas) {
      trendChart = new Chart(canvas, {
        data: {
          labels,
          datasets: [
            { type: "bar", label: "Ventas", data: sales, order: 3, borderRadius: { topLeft: 6, topRight: 6 }, borderSkipped: false, maxBarThickness: 26,
              backgroundColor: (ctx) => fill(ctx.chart, "rgba(125,160,202,.95)", "rgba(84,131,179,.25)") },
            { type: "line", label: "Cobrado", data: collected, order: 2, borderColor: C.blue100, borderWidth: 2.5, tension: .38, pointRadius: 0, pointHoverRadius: 6, pointHoverBackgroundColor: C.navy900, fill: false },
            { type: "line", label: "Utilidad", data: profit, order: 1, borderColor: C.good, borderWidth: 3, tension: .38, pointRadius: 0, pointHoverRadius: 6, pointHoverBackgroundColor: C.navy900,
              fill: true, backgroundColor: (ctx) => fill(ctx.chart, "rgba(142,182,155,.28)", "rgba(142,182,155,0)") },
          ],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          interaction:{mode:"index",intersect:false},
          animation:{duration:1100,easing:"easeOutQuart"},
          plugins: { legend: { display: false }, tooltip: { ...tooltip, callbacks: { label: (i) => ` ${i.dataset.label}: ${money(i.parsed.y)}` } } },
          scales: {
            x: { grid: { display: false }, border: { display: false }, ticks: { color: C.muted, maxRotation: 0, autoSkip: true, maxTicksLimit: 10, font: { size: 11 } } },
            y: { beginAtZero: true, grid: { color: C.grid }, border: { display: false }, ticks: { color: C.muted, font: { size: 11 }, callback: (v) => shortMoney(v) } },
          },
        },
        plugins: [glow],
      });
    },

    profitabilityMixChart(canvas) {
      new Chart(canvas, {
        type: "doughnut",
        data: { labels: ["Costo de la mercancía", "Utilidad bruta"], datasets: [{ data: [ceoNumber("sales_cost_total"), ceoNumber("gross_profit_total")], backgroundColor: [C.blue500, C.good], borderColor: C.navy900, borderWidth: 4, borderRadius: 8, spacing: 2, hoverOffset: 10 }] },
        options: { responsive: true, maintainAspectRatio: false, cutout: "74%", animation: { animateRotate: true, duration: reduced ? 0 : 1100, easing: "easeOutQuart" },
          plugins: { legend: { display: false }, tooltip: { ...tooltip, callbacks: { label: (i) => ` ${i.label}: ${money(i.parsed)}` } } } },
        plugins: [glow],
      });
    },

    fnxCollectionGauge(canvas) {
      const empty = paid + due === 0;
      new Chart(canvas, {
        type: "doughnut",
        data: { labels: ["Cobrado", "Por cobrar"], datasets: [{ data: empty ? [0, 1] : [paid, due], backgroundColor: [C.blue100, empty ? C.track : "rgba(240,184,86,.55)"], borderWidth: 0, borderRadius: empty ? 0 : 14 }] },
        options: { responsive: true, maintainAspectRatio: false, cutout: "80%", rotation: -110, circumference: 220,
          animation: { animateRotate: true, duration: reduced ? 0 : 1100, easing: "easeOutQuart" },
          plugins: { legend: { display: false }, tooltip: empty ? { enabled: false } : { ...tooltip, callbacks: { label: (i) => ` ${i.label}: ${money(i.parsed)}` } } } },
        plugins: [glow],
      });
    },
  };

  function spark(canvas, values, color) {
    if (!values.length) return;
    new Chart(canvas, {
      type: "line",
      data: { labels: values.map((_, i) => i), datasets: [{ data: values, borderColor: color, borderWidth: 2, tension: .4, pointRadius: 0, fill: true,
        backgroundColor: (ctx) => fill(ctx.chart, color + "55", color + "00") }] },
      options: { responsive: true, maintainAspectRatio: false, animation: { duration: reduced ? 0 : 900 },
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        scales: { x: { display: false }, y: { display: false, beginAtZero: true } }, elements: { line: { capBezierPoints: true } } },
    });
  }

  // Cambio "Por dia" / "Acumulado" de la grafica principal.
  root.querySelectorAll("[data-fnx-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      const mode = button.dataset.fnxMode;
      root.querySelectorAll("[data-fnx-mode]").forEach((b) => { const on = b === button; b.classList.toggle("is-on", on); b.setAttribute("aria-pressed", String(on)); });
      if (!trendChart) return;
      const daily = mode === "daily";
      const [s, c, p] = trendChart.data.datasets;
      s.type = daily ? "bar" : "line";
      s.data = daily ? sales : cumulative(sales);
      s.tension = .38; s.pointRadius = 0; s.borderColor = C.blue300; s.borderWidth = daily ? 0 : 2.5;
      s.fill = !daily;
      c.data = daily ? collected : cumulative(collected);
      p.data = daily ? profit : cumulative(profit);
      trendChart.update();
    });
  });

  if (marginBar) requestAnimationFrame(() => { marginBar.style.width = `${margin}%`; });

  if (typeof Chart === "undefined") return;
  const targets = {
    fnxSparkProfit: document.getElementById("fnxSparkProfit"),
    fnxSparkSales: document.getElementById("fnxSparkSales"),
    fnxSparkCollected: document.getElementById("fnxSparkCollected"),
    profitabilityTrendChart: document.getElementById("profitabilityTrendChartV8"),
    profitabilityMixChart: document.getElementById("profitabilityMixChartV8"),
    fnxCollectionGauge: document.getElementById("fnxCollectionGauge"),
  };
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      const key = entry.target.dataset.fnxChart;
      observer.unobserve(entry.target);
      if (charts[key]) { charts[key](entry.target); delete charts[key]; }
    });
  }, { threshold: .2 });
  Object.entries(targets).forEach(([key, canvas]) => { if (canvas) { canvas.dataset.fnxChart = key; observer.observe(canvas); } });
});
