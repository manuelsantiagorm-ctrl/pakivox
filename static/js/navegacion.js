// Navegacion comun de todas las pantallas (templates/base.html):
//  1. "Volver" respeta de donde llegaste: si venias del listado, regresa con
//     sus filtros, busqueda, pagina y posicion; si venias de un tablero
//     (Dashboard CEO, Mesa de Control...), regresa a ese tablero.
//  2. Aviso antes de salir de un formulario con cambios sin guardar.
//  3. Un formulario no se puede enviar dos veces por doble clic.
// La ruta de respaldo siempre es la que calcula el servidor (accounts/navigation.py):
// si no hay rastro (enlace externo, pestaña nueva, recarga), "Volver" funciona igual.
(() => {
  "use strict";

  const CLAVE_RASTRO = "pakivox:rastro";
  const CLAVE_RESTAURAR = "pakivox:restaurar-posicion";
  const MAX_RASTRO = 30;

  const leer = (clave, defecto) => {
    try { const v = sessionStorage.getItem(clave); return v ? JSON.parse(v) : defecto; } catch (error) { return defecto; }
  };
  const guardar = (clave, valor) => { try { sessionStorage.setItem(clave, JSON.stringify(valor)); } catch (error) { /* modo privado o cuota llena */ } };

  // ---------------------------------------------------------------- rastro
  const aqui = { ruta: window.location.pathname, busqueda: window.location.search, y: 0 };
  const rastro = leer(CLAVE_RASTRO, []).filter((e) => e && typeof e.ruta === "string");
  const anterior = rastro.length ? rastro[rastro.length - 1] : null;
  const recarga = anterior && anterior.ruta === aqui.ruta && anterior.busqueda === aqui.busqueda;

  // Posicion a restaurar si llegamos con "Volver".
  const restaurar = leer(CLAVE_RESTAURAR, null);
  let yRestaurar = null;
  if (restaurar && restaurar.ruta === aqui.ruta) {
    for (let i = rastro.length - 1; i >= 0; i--) {
      if (rastro[i].ruta === aqui.ruta && rastro[i].busqueda === aqui.busqueda) { yRestaurar = rastro[i].y || 0; break; }
    }
  }
  try { sessionStorage.removeItem(CLAVE_RESTAURAR); } catch (error) { /* sin storage */ }

  const previa = recarga ? (rastro.length > 1 ? rastro[rastro.length - 2] : null) : anterior;
  if (!recarga) rastro.push(aqui);
  while (rastro.length > MAX_RASTRO) rastro.shift();
  guardar(CLAVE_RASTRO, rastro);

  window.addEventListener("pagehide", () => {
    const actual = leer(CLAVE_RASTRO, []);
    for (let i = actual.length - 1; i >= 0; i--) {
      if (actual[i].ruta === aqui.ruta && actual[i].busqueda === aqui.busqueda) { actual[i].y = Math.round(window.scrollY); break; }
    }
    guardar(CLAVE_RASTRO, actual);
  });

  const ultimaVisita = (ruta) => {
    for (let i = rastro.length - 2; i >= 0; i--) if (rastro[i].ruta === ruta) return rastro[i];
    return null;
  };

  function prepararVolver() {
    const nav = document.querySelector("[data-navegacion]");
    if (!nav) return;
    let contextos = {};
    try { contextos = JSON.parse(nav.dataset.contextos || "{}"); } catch (error) { contextos = {}; }
    const volver = nav.querySelector(".wms-global-nav__back");
    if (volver) {
      const destino = new URL(volver.href, window.location.origin);
      const etiqueta = volver.querySelector("span");
      if (previa && previa.ruta !== aqui.ruta && previa.ruta !== destino.pathname && contextos[previa.ruta]) {
        // Llegaste desde un tablero o listado principal distinto al padre: vuelve ahi.
        volver.href = previa.ruta + (previa.busqueda || "");
        if (etiqueta) etiqueta.textContent = `Volver a ${contextos[previa.ruta]}`;
        volver.setAttribute("aria-label", `Volver a ${contextos[previa.ruta]}`);
      } else {
        const visita = ultimaVisita(destino.pathname);
        if (visita && visita.busqueda) volver.href = destino.pathname + visita.busqueda;
      }
    }
    // Las migas de pan tambien recuperan filtros de la ultima visita.
    nav.querySelectorAll(".wms-global-nav__crumbs a").forEach((a) => {
      const url = new URL(a.href, window.location.origin);
      const visita = ultimaVisita(url.pathname);
      if (visita && visita.busqueda && !url.search) a.href = url.pathname + visita.busqueda;
    });
    nav.addEventListener("click", (event) => {
      const enlace = event.target.closest("a");
      if (!enlace) return;
      guardar(CLAVE_RESTAURAR, { ruta: new URL(enlace.href, window.location.origin).pathname });
    });
  }

  // ------------------------------------------------- cambios sin guardar
  const formularioVigilado = (form) =>
    form instanceof HTMLFormElement &&
    (form.getAttribute("method") || "get").toLowerCase() === "post" &&
    !form.hasAttribute("data-sin-aviso") &&
    !form.querySelector("[data-enviar-al-cambiar]") &&
    !form.hasAttribute("hx-post") && !form.hasAttribute("hx-put") &&
    Boolean(form.closest(".main-content"));

  const sucios = new Set();
  let enviando = false;

  const marcarSucio = (event) => {
    if (!event.isTrusted) return;
    const campo = event.target;
    if (!campo || !campo.form || campo.type === "search" || campo.type === "hidden") return;
    if (formularioVigilado(campo.form)) sucios.add(campo.form);
  };
  document.addEventListener("input", marcarSucio, true);
  document.addEventListener("change", marcarSucio, true);

  // Algunas pantallas envian con form.submit() (sin evento "submit"): tambien cuenta como guardar.
  const submitOriginal = HTMLFormElement.prototype.submit;
  HTMLFormElement.prototype.submit = function submitConAviso() {
    enviando = true;
    sucios.delete(this);
    return submitOriginal.call(this);
  };

  window.addEventListener("beforeunload", (event) => {
    if (enviando) return;
    const pendientes = [...sucios].filter((form) => form.isConnected);
    if (!pendientes.length) return;
    event.preventDefault();
    event.returnValue = "";
  });

  // ------------------------------------------------------- doble envio
  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.dataset.enviando === "1") { event.preventDefault(); return; }
    // Cualquier intento de guardar (aunque lo maneje otro script por AJAX) limpia el aviso.
    sucios.delete(form);
    // Otro script ya lo maneja (htmx, confirmaciones, validaciones propias).
    if (event.defaultPrevented) return;
    if ((form.getAttribute("method") || "get").toLowerCase() !== "post") return;
    if (form.hasAttribute("data-permitir-reenvio") || form.target) return;

    enviando = true;
    form.dataset.enviando = "1";
    const boton = event.submitter;
    // Un boton deshabilitado no envia su name/value: se conserva en un campo oculto.
    if (boton && boton.name && !form.querySelector(`input[type="hidden"][data-copia-boton][name="${CSS.escape(boton.name)}"]`)) {
      const copia = document.createElement("input");
      copia.type = "hidden"; copia.name = boton.name; copia.value = boton.value; copia.dataset.copiaBoton = "1";
      form.appendChild(copia);
    }
    const botones = form.querySelectorAll('button[type="submit"], button:not([type]), input[type="submit"]');
    window.setTimeout(() => {
      botones.forEach((b) => { b.disabled = true; b.classList.add("is-enviando"); b.setAttribute("aria-busy", "true"); });
    }, 0);
    // Respaldo: si la respuesta es una descarga, la pagina no cambia; se reactiva.
    window.setTimeout(() => liberar(form), 8000);
  });

  function liberar(form) {
    enviando = false;
    delete form.dataset.enviando;
    form.querySelectorAll(".is-enviando").forEach((b) => { b.disabled = false; b.classList.remove("is-enviando"); b.removeAttribute("aria-busy"); });
  }

  // Al volver con el boton "atras" del navegador la pagina puede venir de cache.
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) document.querySelectorAll("form[data-enviando]").forEach(liberar);
  });

  const iniciar = () => {
    prepararVolver();
    if (yRestaurar !== null && yRestaurar > 0) {
      window.requestAnimationFrame(() => window.scrollTo(0, yRestaurar));
    }
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", iniciar);
  else iniciar();
})();
