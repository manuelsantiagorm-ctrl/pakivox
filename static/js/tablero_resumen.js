// Dashboard CEO · pestaña Resumen = Centro de mando (templates/dashboard/partials/executive_home.html)
// Solo dibuja: el puntaje, el rio y las cifras ya vienen calculados por la vista.
document.addEventListener("DOMContentLoaded", () => {
  "use strict";

  const root = document.querySelector(".resumen-ceo.cm");
  if (!root) return;

  const num = (value) => { const n = Number.parseFloat(String(value ?? "").replace(",", ".")); return Number.isFinite(n) ? n : null; };
  const readData = (name) => {
    const node = document.getElementById(`ceo-data-${name}`);
    if (!node) return [];
    try { let data = JSON.parse(node.textContent); if (typeof data === "string") data = JSON.parse(data); return Array.isArray(data) ? data.map((v) => num(v) || 0) : []; } catch (error) { return []; }
  };
  const uid = () => Math.random().toString(36).slice(2, 9);

  // Saludo segun la hora del equipo del dueno.
  const greeting = root.querySelector("[data-cm-saludo]");
  if (greeting) {
    const hour = new Date().getHours();
    const text = hour < 12 ? "Buenos días" : hour < 19 ? "Buenas tardes" : "Buenas noches";
    greeting.textContent = greeting.textContent.replace(/^Hola/, text);
  }

  // Anillo de salud.
  const ring = root.querySelector("[data-cm-anillo]");
  const ringSvg = root.querySelector("[data-cm-anillo-svg]");
  if (ring && ringSvg) {
    const score = num(ring.dataset.cmAnillo);
    const size = 230, stroke = 20, r = (size - stroke) / 2, c = 2 * Math.PI * r, id = uid();
    const pct = score === null ? 0 : Math.max(0, Math.min(100, score));
    const color = pct >= 75 ? "#5483B3" : pct >= 50 ? "#F0B856" : "#F17882";
    ringSvg.innerHTML = `<svg viewBox="0 0 ${size} ${size}"><defs>
      <linearGradient id="cmr${id}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#C1E8FF"/><stop offset="1" stop-color="${color}"/></linearGradient>
      <filter id="cmf${id}" x="-30%" y="-30%" width="160%" height="160%"><feGaussianBlur stdDeviation="6"/></filter></defs>
      <g transform="rotate(-90 ${size / 2} ${size / 2})">
        <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="rgba(84,131,179,.2)" stroke-width="${stroke}"/>
        <circle class="cm-arco" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="url(#cmr${id})" stroke-width="${stroke}" stroke-linecap="round" stroke-dasharray="0 ${c}" filter="url(#cmf${id})" opacity=".6"/>
        <circle class="cm-arco" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="url(#cmr${id})" stroke-width="${stroke}" stroke-linecap="round" stroke-dasharray="0 ${c}"/>
      </g></svg>`;
    const arcs = ringSvg.querySelectorAll(".cm-arco");
    const target = (c * pct) / 100;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reducedMotion) { arcs.forEach((a) => a.setAttribute("stroke-dasharray", `${target} ${c}`)); }
    else {
      const start = performance.now();
      const step = (t) => { const k = Math.min(1, (t - start) / 1200); const e = 1 - Math.pow(1 - k, 4); arcs.forEach((a) => a.setAttribute("stroke-dasharray", `${target * e} ${c}`)); if (k < 1) requestAnimationFrame(step); };
      requestAnimationFrame(step);
    }
  }

  requestAnimationFrame(() => {
    root.querySelectorAll("[data-cm-barra]").forEach((bar) => { bar.style.width = `${Math.max(0, Math.min(100, num(bar.dataset.cmBarra) || 0))}%`; });
    const tall = [...root.querySelectorAll("[data-cm-alto]")];
    const maxTall = Math.max(1, ...tall.map((bar) => num(bar.dataset.cmAlto) || 0));
    tall.forEach((bar) => { const v = num(bar.dataset.cmAlto) || 0; bar.style.height = `${v ? Math.max(8, (v / maxTall) * 78) : 4}px`; });
  });

  // Mini graficas de tendencia (utilidad y ventas del mes).
  root.querySelectorAll("[data-cm-spark]").forEach((box) => {
    let values = readData(box.dataset.cmSpark);
    if ("cmAcumulado" in box.dataset) { let total = 0; values = values.map((v) => (total += v)); }
    if (values.length < 2 || values.every((v) => v === 0)) return;
    const color = box.dataset.cmColor || "#7DA0CA";
    const w = 300, h = 90, pad = 4, id = uid();
    const max = Math.max(...values), min = Math.min(0, ...values);
    const x = (i) => pad + (i * (w - pad * 2)) / (values.length - 1);
    const y = (v) => h - pad - ((v - min) / (max - min || 1)) * (h - pad * 2);
    let d = `M${x(0)},${y(values[0])}`;
    for (let i = 1; i < values.length; i++) { const cx = (x(i - 1) + x(i)) / 2; d += ` C${cx},${y(values[i - 1])} ${cx},${y(values[i])} ${x(i)},${y(values[i])}`; }
    box.innerHTML = `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><defs>
      <linearGradient id="cms${id}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="${color}" stop-opacity=".45"/><stop offset="1" stop-color="${color}" stop-opacity="0"/></linearGradient>
      <filter id="cmb${id}"><feGaussianBlur stdDeviation="3"/></filter></defs>
      <path d="${d} L${x(values.length - 1)},${h} L${x(0)},${h} Z" fill="url(#cms${id})"/>
      <path d="${d}" fill="none" stroke="${color}" stroke-width="5" opacity=".35" filter="url(#cmb${id})" vector-effect="non-scaling-stroke"/>
      <path d="${d}" fill="none" stroke="${color}" stroke-width="2.4" stroke-linecap="round" vector-effect="non-scaling-stroke"/></svg>`;
  });

  // Rio: el grosor de cada tramo es el dinero de la etapa (raiz cuadrada para que
  // una etapa enorme no aplaste a las demas). Inventario no tiene dinero: toma el
  // promedio de sus vecinas.
  const cauce = root.querySelector("[data-cm-cauce]");
  const stages = [...root.querySelectorAll(".cm-etapa")];
  function drawRiver() {
    if (!cauce || stages.length < 2 || getComputedStyle(cauce).display === "none") return;
    const box = cauce.getBoundingClientRect();
    if (!box.width) return;
    const raw = stages.map((li) => num(li.dataset.cmDinero));
    const filled = raw.map((v, i) => {
      if (v !== null) return v;
      const near = [raw[i - 1], raw[i + 1]].filter((n) => n !== null && n !== undefined);
      return near.length ? near.reduce((a, b) => a + b, 0) / near.length : 0;
    });
    const roots = filled.map((v) => Math.sqrt(Math.max(0, v)));
    const top = Math.max(...roots);
    const h = box.height, cy = h / 2;
    const thick = roots.map((v) => (top ? 6 + (v / top) * (h - 16) : 6));
    const xs = stages.map((li) => { const r = li.querySelector(".cm-nodo").getBoundingClientRect(); return r.left + r.width / 2 - box.left; });
    let d = `M${xs[0]},${cy - thick[0] / 2}`;
    for (let i = 1; i < xs.length; i++) { const cx = (xs[i - 1] + xs[i]) / 2; d += ` C${cx},${cy - thick[i - 1] / 2} ${cx},${cy - thick[i] / 2} ${xs[i]},${cy - thick[i] / 2}`; }
    d += ` L${xs[xs.length - 1]},${cy + thick[xs.length - 1] / 2}`;
    for (let i = xs.length - 2; i >= 0; i--) { const cx = (xs[i + 1] + xs[i]) / 2; d += ` C${cx},${cy + thick[i + 1] / 2} ${cx},${cy + thick[i] / 2} ${xs[i]},${cy + thick[i] / 2}`; }
    const id = uid();
    cauce.innerHTML = `<svg viewBox="0 0 ${box.width} ${h}"><defs>
      <linearGradient id="cmg${id}" x1="0" x2="1"><stop offset="0" stop-color="#5483B3"/><stop offset=".55" stop-color="#C1E8FF"/><stop offset="1" stop-color="#8EB69B"/></linearGradient>
      <filter id="cmh${id}" x="-10%" y="-100%" width="120%" height="300%"><feGaussianBlur stdDeviation="12"/></filter></defs>
      <path d="${d} Z" fill="url(#cmg${id})" filter="url(#cmh${id})" opacity=".65"/>
      <path d="${d} Z" fill="url(#cmg${id})" opacity=".85"/></svg>`;
  }
  drawRiver();
  let resizeTimer = null;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(drawRiver, 150); });
  // Si el Resumen estaba oculto al cargar, se dibuja al volver a la pestaña.
  document.querySelectorAll('.ceo-view-tab[data-ceo-view="summary"]').forEach((tab) => tab.addEventListener("click", () => requestAnimationFrame(drawRiver)));

  // Actividad: lo mas reciente primero, solo cuatro.
  const events = root.querySelector("[data-cm-eventos]");
  if (events) {
    const items = [...events.querySelectorAll("li[data-cm-ts]")].sort((a, b) => (num(b.dataset.cmTs) || 0) - (num(a.dataset.cmTs) || 0));
    items.forEach((li, index) => { if (index < 4) events.appendChild(li); else li.remove(); });
  }

  // Puertas: abren la pestaña correspondiente del mismo dashboard.
  root.querySelectorAll("[data-cm-ir]").forEach((button) => {
    button.addEventListener("click", () => {
      const tab = document.querySelector(`.ceo-view-tab[data-ceo-view="${button.dataset.cmIr}"]`);
      if (!tab) return;
      tab.click();
      tab.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
});

// Las graficas de 30 dias viven dentro de un <details> plegado: se dibujan
// la primera vez que el dueno lo abre, para que la pagina cargue mas ligera.
document.addEventListener("DOMContentLoaded", () => {
  "use strict";

  const details = document.getElementById("executiveAnalysisDetails");
  if (!details) return;

  const palette = {
    sales: "#7DA0CA",
    collected: "#8EB69B",
    payables: "#C1E8FF",
    muted: "#9DB6D2",
    grid: "rgba(125,160,202,.12)",
    card: "#04173A",
  };

  const read = (id) => {
    const node = document.getElementById(id);
    if (!node) return [];
    try { return JSON.parse(node.textContent); } catch (error) { return []; }
  };

  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const money = (value) => new Intl.NumberFormat("es-MX", { style: "currency", currency: "MXN", maximumFractionDigits: 2 }).format(Number(value) || 0);
  const baseTooltip = { backgroundColor: "rgba(2,16,36,.96)", titleColor: "#EAF6FF", bodyColor: "#C1E8FF", borderColor: "rgba(125,160,202,.35)", borderWidth: 1, padding: 11, displayColors: true, cornerRadius: 9 };
  const ticks = { color: palette.muted, font: { size: 11 } };

  let drawn = false;

  function drawCharts() {
    if (drawn || typeof Chart === "undefined") return;
    drawn = true;

    const trendCanvas = document.getElementById("executiveCollectionChartAF10B");
    if (trendCanvas) {
      const context = trendCanvas.getContext("2d");
      const salesFill = context.createLinearGradient(0, 0, 0, 250);
      const collectedFill = context.createLinearGradient(0, 0, 0, 250);
      salesFill.addColorStop(0, "rgba(125,160,202,.32)"); salesFill.addColorStop(1, "rgba(125,160,202,0)");
      collectedFill.addColorStop(0, "rgba(142,182,155,.24)"); collectedFill.addColorStop(1, "rgba(142,182,155,0)");
      new Chart(trendCanvas, {
        type: "line",
        data: {
          labels: read("executiveCollectionLabelsAF10B"),
          datasets: [
            { label: "Ventas", data: read("executiveCollectionSalesAF10B"), borderColor: palette.sales, backgroundColor: salesFill, fill: true, borderWidth: 2, tension: .38, pointRadius: 0, pointHoverRadius: 5, pointHoverBackgroundColor: palette.card },
            { label: "Cobros", data: read("executiveCollectionCollectedAF10B"), borderColor: palette.collected, backgroundColor: collectedFill, fill: true, borderWidth: 2, tension: .38, pointRadius: 0, pointHoverRadius: 5, pointHoverBackgroundColor: palette.card },
          ],
        },
        options: {
          responsive: true, maintainAspectRatio: false,
          interaction:{mode:"index",intersect:false},
          animation: { duration:reduced?0:1100, easing:"easeOutQuart" },
          plugins: { legend: { display: false }, tooltip: { ...baseTooltip, callbacks: { label: (item) => ` ${item.dataset.label}: ${money(item.parsed.y)}` } } },
          scales: {
            x: { grid: { display: false }, ticks: { ...ticks, maxTicksLimit: 8 } },
            y: { beginAtZero: true, grid: { color: palette.grid }, ticks: { ...ticks, callback: (value) => money(value) } },
          },
        },
      });
    }

    const fulfillmentCanvas = document.getElementById("executiveFulfillmentChartAF10B");
    if (fulfillmentCanvas) {
      new Chart(fulfillmentCanvas, {
        type: "doughnut",
        data: { labels: ["Entregados", "En proceso"], datasets: [{ data: read("executiveFulfillmentCountsAF10B"), backgroundColor: [palette.collected, "rgba(84,131,179,.35)"], borderColor: palette.card, borderWidth: 4, hoverOffset: 6 }] },
        options: {
          responsive: true, maintainAspectRatio: false, cutout: "74%",
          animation: { animateRotate: true, duration:reduced?0:1100, easing:"easeOutQuart" },
          plugins: { legend: { display: false }, tooltip: { ...baseTooltip, callbacks: { label: (item) => ` ${item.label}: ${item.parsed} pedidos` } } },
        },
      });
    }

    const capitalCanvas = document.getElementById("executiveCapitalChartAF10B");
    if (capitalCanvas) {
      new Chart(capitalCanvas, {
        type: "bar",
        data: { labels: ["Por cobrar", "Proveedores"], datasets: [{ data: read("executiveCapitalCountsAF10B"), backgroundColor: [palette.sales, palette.payables], borderRadius: 7, borderSkipped: false, barThickness: 18 }] },
        options: {
          indexAxis: "y", responsive: true, maintainAspectRatio: false,
          animation: { duration:reduced?0:1100, easing:"easeOutQuart" },
          plugins: { legend: { display: false }, tooltip: { ...baseTooltip, callbacks: { label: (item) => ` ${money(item.parsed.x)}` } } },
          scales: {
            x: { beginAtZero: true, grid: { color: palette.grid }, ticks: { ...ticks, callback: (value) => money(value) } },
            y: { grid: { display: false }, ticks: { ...ticks, font: { size: 12, weight: "bold" } } },
          },
        },
      });
    }
  }

  if (details.open) drawCharts();
  details.addEventListener("toggle", () => { if (details.open) drawCharts(); });
});
