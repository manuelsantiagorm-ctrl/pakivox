/*
  Panel "Avisar al cliente" del detalle del pedido
  (templates/customer_updates/_panel_pedido.html).
*/
(function () {
  "use strict";

  const panel = document.querySelector("[data-avisos-cliente]");
  if (!panel) return;

  const status = panel.querySelector("[data-cu-status]");
  const say = (text) => {
    if (status) status.textContent = text;
  };

  // ---------------------------------------------------- copiar
  const copyText = async (text) => {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (error) {
      const helper = document.createElement("textarea");
      helper.value = text;
      helper.setAttribute("readonly", "");
      helper.style.position = "fixed";
      helper.style.opacity = "0";
      document.body.appendChild(helper);
      helper.select();
      let ok = false;
      try {
        ok = document.execCommand("copy");
      } catch (ignored) {
        ok = false;
      }
      helper.remove();
      return ok;
    }
  };

  panel.querySelectorAll("[data-cu-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const source = panel.querySelector(button.getAttribute("data-cu-copy"));
      if (!source) return;
      const ok = await copyText(source.value || source.textContent || "");
      const original = button.innerHTML;
      button.innerHTML = ok ? '<i class="bi bi-check2"></i> Copiado' : "No se pudo copiar";
      setTimeout(() => {
        button.innerHTML = original;
      }, 1800);
    });
  });

  // ---------------------------------------------------- borradores
  let drafts = [];
  const dataNode = document.getElementById("cuDraftsData");
  if (dataNode) {
    try {
      drafts = JSON.parse(dataNode.textContent) || [];
    } catch (error) {
      drafts = [];
    }
  }
  const byKey = new Map(drafts.map((draft) => [draft.key, draft]));
  const body = panel.querySelector("[data-cu-body]");
  const keyInput = panel.querySelector("[data-cu-draft-key]");
  const original = panel.querySelector("[data-cu-original]");
  const edited = () => body && original && body.value.trim() !== original.value.trim();

  panel.querySelectorAll("[data-cu-draft]").forEach((chip) => {
    chip.addEventListener("click", () => {
      const draft = byKey.get(chip.getAttribute("data-cu-draft"));
      if (!draft || !body) return;
      if (edited() && !window.confirm("Cambiaste el texto. ¿Reemplazarlo por este otro mensaje?")) return;
      body.value = draft.body;
      if (keyInput) keyInput.value = draft.key;
      if (original) original.value = draft.body;
      panel.querySelectorAll("[data-cu-draft]").forEach((other) => {
        const active = other === chip;
        other.classList.toggle("is-active", active);
        other.setAttribute("aria-pressed", active ? "true" : "false");
      });
      say("");
    });
  });

  // ---------------------------------------------------- número
  const manualInput = panel.querySelector("[data-cu-manual-input]");
  panel.querySelectorAll("[data-cu-phone-option]").forEach((radio) => {
    radio.addEventListener("change", () => {
      if (!manualInput) return;
      const manual = radio.hasAttribute("data-cu-phone-manual") && radio.checked;
      manualInput.hidden = !manual;
      if (manual) manualInput.focus();
    });
  });

  // ---------------------------------------------------- enviar
  const form = panel.querySelector("[data-cu-whatsapp-form]");
  if (form) {
    form.addEventListener("submit", (event) => {
      const manualRadio = form.querySelector("[data-cu-phone-manual]");
      if (manualRadio && manualRadio.checked && manualInput) {
        const digits = (manualInput.value || "").replace(/\D/g, "");
        if (digits.length < 10) {
          event.preventDefault();
          say("Escribe un número de al menos 10 dígitos.");
          manualInput.focus();
          return;
        }
      }
      if (body && !body.value.trim()) {
        event.preventDefault();
        say("El mensaje está vacío.");
        body.focus();
        return;
      }
      say("Abriendo WhatsApp en otra pestaña… revisa el mensaje y presiona enviar.");
    });
  }

  // ---------------------------------------------------- confirmaciones
  panel.querySelectorAll("form[data-cu-confirm]").forEach((confirmForm) => {
    confirmForm.addEventListener("submit", (event) => {
      if (!window.confirm(confirmForm.getAttribute("data-cu-confirm"))) event.preventDefault();
    });
  });
})();
