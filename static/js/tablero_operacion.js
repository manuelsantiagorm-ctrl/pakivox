// Dashboard CEO · pestaña Operación (bloque .opx-board en templates/dashboard/ceo.html)
// Las graficas se dibujan la primera vez que la pestaña se hace visible, para
// que la animacion se vea y para no gastar trabajo si el dueno nunca la abre.
document.addEventListener("DOMContentLoaded", () => {
  "use strict";

  const board = document.querySelector(".opx-board");
  if (!board) return;

  const C = {
    navy900: "#021024", navy700: "#052659", blue500: "#5483B3", blue300: "#7DA0CA", blue100: "#C1E8FF",
    text: "#EAF6FF", muted: "#9DB6D2", grid: "rgba(125,160,202,.12)",
    good: "#8EB69B", warn: "#F0B856", bad: "#F17882", track: "rgba(84,131,179,.18)",
  };
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const num = (value) => { const n = Number.parseFloat(value); return Number.isFinite(n) ? Math.max(0, n) : 0; };
  const pct = (part, total) => (total > 0 ? Math.round((part / total) * 100) : 0);
  const fmt = (n) => Number(n).toLocaleString("es-MX");
  const setText = (selector, text) => { const el = board.querySelector(selector); if (el) el.textContent = text; };
  const tooltip = {
    backgroundColor: "rgba(2,16,36,.97)", titleColor: C.text, bodyColor: C.blue100,
    borderColor: "rgba(125,160,202,.4)", borderWidth: 1, padding: 12, cornerRadius: 10,
    titleFont: { weight: "bold" }, displayColors: true, boxPadding: 4,
  };
  const animation = { duration: reduced ? 0 : 1200, easing: "easeOutQuart" };

  // Resplandor suave detras de cada barra/arco: da profundidad sin deformar los datos.
  const glow = {
    id: "opxGlow",
    beforeDatasetsDraw(chart) { const ctx = chart.ctx; ctx.save(); ctx.shadowColor = "rgba(125,160,202,.45)"; ctx.shadowBlur = 14; ctx.shadowOffsetY = 5; },
    afterDatasetsDraw(chart) { chart.ctx.restore(); },
  };

  // Numero encima de cada barra (pocas barras, cada una con su valor).
  const barValues = {
    id: "opxBarValues",
    afterDatasetsDraw(chart) {
      const { ctx } = chart;
      ctx.save();
      ctx.fillStyle = C.text;
      ctx.font = "700 13px system-ui, -apple-system, 'Segoe UI', sans-serif";
      const horizontal = chart.options.indexAxis === "y";
      ctx.textAlign = horizontal ? "left" : "center";
      ctx.textBaseline = horizontal ? "middle" : "bottom";
      chart.getDatasetMeta(0).data.forEach((bar, i) => {
        const value = chart.data.datasets[0].data[i];
        if (horizontal) ctx.fillText(fmt(value), bar.x + 8, bar.y);
        else ctx.fillText(fmt(value), bar.x, bar.y - 6);
      });
      ctx.restore();
    },
  };

  const verticalFill = (chart, top, bottom) => {
    const { ctx, chartArea } = chart;
    if (!chartArea) return top;
    const g = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
    g.addColorStop(0, top); g.addColorStop(1, bottom);
    return g;
  };

  // ---------- Datos ----------
  const orders = board.querySelector('[data-executive-card="orders"]');
  const o = orders ? orders.dataset : {};
  const flow = [
    ["Pendientes", num(o.pending)], ["Pagados", num(o.paid)], ["En picking", num(o.picking)],
    ["Empacados", num(o.packed)], ["Enviados", num(o.shipped)], ["Entregados", num(o.delivered)],
  ];
  const ordersTotal = flow.reduce((s, [, v]) => s + v, 0) + num(o.draft) + num(o.cancelled);
  const inProgress = flow.slice(0, 5).reduce((s, [, v]) => s + v, 0);
  setText('[data-opx-kpi="in-progress"]', fmt(inProgress));
  setText("[data-opx-delivered-pct]", `${pct(num(o.delivered), ordersTotal)}%`);
  setText("[data-opx-orders-note]", `${fmt(ordersTotal)} pedidos en total · ${fmt(num(o.draft))} en borrador · ${fmt(num(o.cancelled))} cancelados`);

  const rings = [...board.querySelectorAll(".opx-ring[data-process]")].map((el) => {
    const done = num(el.dataset.complete), wait = num(el.dataset.pending);
    const share = pct(done, done + wait);
    el.querySelector("[data-opx-ring-pct]").textContent = `${share}%`;
    el.querySelector("[data-opx-ring-text]").textContent = `${fmt(done)} terminados · ${fmt(wait)} pendientes`;
    return { el, done, wait };
  });
  const allDone = rings.reduce((s, r) => s + r.done, 0), allWait = rings.reduce((s, r) => s + r.wait, 0);
  setText("[data-opx-warehouse-pct]", `${pct(allDone, allDone + allWait)}%`);

  const inventory = board.querySelector('[data-executive-card="inventory"]');
  const inv = inventory ? inventory.dataset : {};
  setText("[data-opx-available-pct]", `${Math.min(100, pct(num(inv.available), num(inv.physical)))}%`);

  const purchases = board.querySelector('[data-executive-card="purchases"]');
  const pu = purchases ? purchases.dataset : {};
  const stages = [["Borrador", num(pu.draft)], ["Enviadas", num(pu.sent)], ["Parciales", num(pu.partial)], ["Recibidas", num(pu.received)], ["Canceladas", num(pu.cancelled)]];
  const purchasesTotal = stages.reduce((s, [, v]) => s + v, 0);
  setText("[data-opx-received-pct]", `${pct(num(pu.received), purchasesTotal)}%`);
  setText("[data-opx-purchases-note]", `${fmt(purchasesTotal)} órdenes de compra`);

  // ---------- Graficas ----------
  const charts = {
    opxOrdersFlow(canvas) {
      // Etapas en orden: azul que se aclara conforme el pedido avanza; entregado en verde.
      const tops = ["#3E6C9E", "#5483B3", "#6A93C0", "#7DA0CA", "#A9CDEB", C.good];
      new Chart(canvas, {
        type: "bar",
        data: {
          labels: flow.map(([l]) => l),
          datasets: [{
            label: "Pedidos",
            data: flow.map(([, v]) => v),
            backgroundColor: (ctx) => verticalFill(ctx.chart, tops[ctx.dataIndex], tops[ctx.dataIndex] + "59"),
            borderColor: tops, borderWidth: { top: 2, left: 0, right: 0, bottom: 0 },
            borderRadius: { topLeft: 10, topRight: 10 }, borderSkipped: false,
            maxBarThickness: 74, categoryPercentage: .72,
          }],
        },
        options: {
          responsive: true, maintainAspectRatio: false, animation,
          layout: { padding: { top: 26 } },
          plugins: { legend: { display: false }, tooltip: { ...tooltip, callbacks: { label: (i) => ` ${fmt(i.parsed.y)} pedidos (${pct(i.parsed.y, ordersTotal)}% del total)` } } },
          scales: {
            x: { grid: { display: false }, border: { display: false }, ticks: { color: C.muted, font: { size: 13, weight: "600" } } },
            y: { beginAtZero: true, grace: "10%", grid: { color: C.grid }, border: { display: false }, ticks: { color: C.muted, precision: 0, font: { size: 11 } } },
          },
        },
        plugins: [glow, barValues],
      });
    },

    opxPickingRing(canvas) { ring(canvas, rings[0]); },
    opxPackingRing(canvas) { ring(canvas, rings[1]); },

    opxInventoryDonut(canvas) {
      new Chart(canvas, {
        type: "doughnut",
        data: {
          labels: ["Disponible", "Reservado", "Comprometido"],
          datasets: [{
            data: [num(inv.available), num(inv.reserved), num(inv.committed)],
            backgroundColor: [C.blue100, C.blue300, C.blue500],
            borderColor: C.navy900, borderWidth: 3, hoverOffset: 10, borderRadius: 6, spacing: 2,
          }],
        },
        options: {
          responsive: true, maintainAspectRatio: false, cutout: "74%", animation: { ...animation, animateRotate: true },
          plugins: { legend: { display: false }, tooltip: { ...tooltip, callbacks: { label: (i) => ` ${i.label}: ${fmt(i.parsed)} piezas` } } },
        },
        plugins: [glow],
      });
    },

    opxPurchasesBar(canvas) {
      const colors = ["#3E6C9E", C.blue500, C.blue300, C.good, C.bad];
      new Chart(canvas, {
        type: "bar",
        data: {
          labels: stages.map(([l]) => l),
          datasets: [{
            label: "Órdenes de compra",
            data: stages.map(([, v]) => v),
            backgroundColor: (ctx) => {
              const { chart } = ctx; const area = chart.chartArea; if (!area) return colors[ctx.dataIndex];
              const g = chart.ctx.createLinearGradient(area.left, 0, area.right, 0);
              g.addColorStop(0, colors[ctx.dataIndex] + "99"); g.addColorStop(1, colors[ctx.dataIndex]);
              return g;
            },
            borderRadius: 8, borderSkipped: false, maxBarThickness: 26,
          }],
        },
        options: {
          indexAxis: "y", responsive: true, maintainAspectRatio: false, animation,
          layout: { padding: { right: 40 } },
          plugins: { legend: { display: false }, tooltip: { ...tooltip, callbacks: { label: (i) => ` ${fmt(i.parsed.x)} órdenes` } } },
          scales: {
            x: { beginAtZero: true, grace: "8%", grid: { color: C.grid }, border: { display: false }, ticks: { color: C.muted, precision: 0, font: { size: 11 } } },
            y: { grid: { display: false }, border: { display: false }, ticks: { color: C.text, font: { size: 13, weight: "600" } } },
          },
        },
        plugins: [glow, barValues],
      });
    },
  };

  function ring(canvas, data) {
    if (!data) return;
    const empty = data.done + data.wait === 0;
    new Chart(canvas, {
      type: "doughnut",
      data: {
        labels: ["Terminados", "Pendientes"],
        datasets: [{
          data: empty ? [0, 1] : [data.done, data.wait],
          backgroundColor: [C.good, C.track], borderWidth: 0, borderRadius: empty ? 0 : 20,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: "78%", rotation: -120, circumference: 240,
        animation: { ...animation, animateRotate: true },
        // Sin etiqueta flotante: el texto de abajo ya dice terminados y pendientes.
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
      },
      plugins: [glow],
    });
  }

  if (typeof Chart === "undefined") return;

  const pending = new Set(Object.keys(charts));
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      const id = entry.target.id;
      if (pending.has(id)) { pending.delete(id); charts[id](entry.target); }
      observer.unobserve(entry.target);
    });
  }, { threshold: .25 });

  Object.keys(charts).forEach((id) => { const canvas = document.getElementById(id); if (canvas) observer.observe(canvas); });
});
