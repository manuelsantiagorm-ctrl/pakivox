(() => {
  "use strict";
  const KEY = "pakivox_cookie_consent_v1";
  const MAX_AGE = 180 * 24 * 60 * 60 * 1000;
  const layer = document.querySelector("[data-cookie-layer]");
  if (!layer) return;
  const panel = layer.querySelector("[data-cookie-panel]");
  const checks = [...layer.querySelectorAll("[data-cookie-category]")];

  const read = () => {
    try {
      const value = JSON.parse(localStorage.getItem(KEY));
      if (!value || Date.now() - value.savedAt > MAX_AGE) return null;
      return value;
    } catch (_) { return null; }
  };
  const dispatch = (consent) => {
    window.PAKIVOX_COOKIE_CONSENT = consent;
    window.dispatchEvent(new CustomEvent("pakivox:cookie-consent", {detail: consent}));
  };
  const save = (analytics, marketing) => {
    const consent = {essential:true, analytics:!!analytics, marketing:!!marketing, savedAt:Date.now(), version:"2026-08-12.1"};
    localStorage.setItem(KEY, JSON.stringify(consent));
    layer.hidden = true;
    dispatch(consent);
  };
  const open = () => {
    const current = read();
    checks.forEach((input) => { input.checked = !!(current && current[input.dataset.cookieCategory]); });
    panel.hidden = true;
    layer.hidden = false;
  };
  layer.querySelector("[data-cookie-accept]").addEventListener("click", () => save(true, true));
  layer.querySelector("[data-cookie-reject]").addEventListener("click", () => save(false, false));
  layer.querySelector("[data-cookie-customize]").addEventListener("click", () => { panel.hidden = false; });
  layer.querySelector("[data-cookie-save]").addEventListener("click", () => {
    const values = Object.fromEntries(checks.map((input) => [input.dataset.cookieCategory, input.checked]));
    save(values.analytics, values.marketing);
  });
  document.querySelectorAll("[data-cookie-settings]").forEach((button) => button.addEventListener("click", open));
  const current = read();
  if (current) dispatch(current); else open();
})();
