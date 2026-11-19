// Paginas de error: el boton "Regresar" vuelve a la pagina anterior solo si
// vino de este mismo sitio; si no (enlace externo, pestaña nueva), va al inicio.
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-error-regresar]");
  if (!button) return;
  event.preventDefault();
  let mismoSitio = false;
  try { mismoSitio = Boolean(document.referrer) && new URL(document.referrer).origin === window.location.origin; } catch (error) { mismoSitio = false; }
  if (mismoSitio && window.history.length > 1) window.history.back();
  else window.location.assign(button.dataset.errorRegresar || "/");
});
