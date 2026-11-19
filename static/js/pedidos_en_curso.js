// Ventas · pedidos en curso (templates/sales/pedidos_en_curso.html)
// Búsqueda en vivo: pide la misma página con los filtros y reemplaza la lista
// y los contadores, sin recargar. Si falla, el formulario sigue funcionando normal.
(() => {
  "use strict";
  const form = document.getElementById("inProgressFilters");
  const input = document.getElementById("inProgressSearch");
  const stage = document.getElementById("inProgressStage");
  const clear = document.getElementById("inProgressClear");
  const live = document.getElementById("inProgressLive");
  if (!form || !input || !stage) return;
  let timer = null;
  let controller = null;
  let sequence = 0;

  function urlForFilters() {
    const url = new URL(window.location.href);
    const q = input.value.trim();
    if (q) url.searchParams.set("q", q); else url.searchParams.delete("q");
    if (stage.value && stage.value !== "all") url.searchParams.set("stage", stage.value); else url.searchParams.delete("stage");
    url.searchParams.delete("page");
    return url;
  }

  async function refresh() {
    const current = ++sequence;
    if (controller) controller.abort();
    controller = new AbortController();
    const url = urlForFilters();
    const results = document.getElementById("inProgressResults");
    if (results) results.classList.add("is-cargando");
    if (live) live.textContent = "Actualizando pedidos…";
    try {
      const response = await fetch(url.toString(), { credentials: "same-origin", signal: controller.signal, headers: { "X-Requested-With": "XMLHttpRequest" } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const parsed = new DOMParser().parseFromString(await response.text(), "text/html");
      const freshResults = parsed.getElementById("inProgressResults");
      const freshSummary = parsed.getElementById("inProgressSummary");
      const oldResults = document.getElementById("inProgressResults");
      const oldSummary = document.getElementById("inProgressSummary");
      if (current !== sequence) return;
      if (!freshResults || !oldResults) throw new Error("Respuesta incompleta");
      oldResults.replaceWith(freshResults);
      if (freshSummary && oldSummary) oldSummary.replaceWith(freshSummary);
      history.replaceState({}, "", url.pathname + url.search);
      if (live) live.textContent = "Resultados actualizados";
    } catch (error) {
      if (error.name === "AbortError") return;
      console.error("Pedidos en curso:", error);
      if (live) live.textContent = "No fue posible actualizar automáticamente.";
    } finally {
      const currentResults = document.getElementById("inProgressResults");
      if (currentResults) currentResults.classList.remove("is-cargando");
    }
  }

  function schedule() {
    clearTimeout(timer);
    if (clear) clear.hidden = !input.value.trim();
    timer = setTimeout(refresh, 350);
  }

  input.addEventListener("input", schedule);
  if (clear) clear.addEventListener("click", () => { input.value = ""; clear.hidden = true; input.focus(); refresh(); });
  form.addEventListener("submit", (event) => { event.preventDefault(); clearTimeout(timer); refresh(); });
})();
