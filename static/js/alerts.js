(function () {
  "use strict";

  if (window.WMSAlert) return;

  var nativeAlert = window.alert.bind(window);
  var nativeConfirm = window.confirm.bind(window);
  var root = null;
  var activeResolve = null;
  var previousFocus = null;
  var interaction = null;
  var bypassConfirm = false;
  var queue = Promise.resolve();

  var TYPES = {
    success: {
      icon: "✓",
      eyebrow: "Operación completada",
      title: "Todo salió bien",
    },
    error: {
      icon: "!",
      eyebrow: "No se pudo completar",
      title: "Ocurrió un error",
    },
    danger: {
      icon: "!",
      eyebrow: "Acción delicada",
      title: "Confirma esta acción",
    },
    warning: {
      icon: "!",
      eyebrow: "Requiere atención",
      title: "Antes de continuar",
    },
    info: { icon: "i", eyebrow: "Información", title: "Aviso de PAKIVOX" },
  };

  function safeText(value) {
    return value == null ? "" : String(value);
  }
  function normalizeType(value) {
    var type = safeText(value).toLowerCase();
    if (type.indexOf("error") >= 0 || type.indexOf("danger") >= 0)
      return "error";
    if (type.indexOf("warn") >= 0) return "warning";
    if (type.indexOf("success") >= 0) return "success";
    return "info";
  }
  function ensureRoot() {
    if (root) return root;
    if (!document.body) return null;
    root = document.createElement("div");
    root.className = "wms-alert-root";
    root.innerHTML =
      '<div class="wms-alert-toast-stack" aria-live="polite" aria-atomic="true"></div>' +
      '<div class="wms-alert-backdrop" aria-hidden="true">' +
      '<section class="wms-alert-dialog" role="alertdialog" aria-modal="true" aria-labelledby="wms-alert-title" aria-describedby="wms-alert-message">' +
      '<div class="wms-alert-head"><div class="wms-alert-icon" aria-hidden="true"></div>' +
      '<div class="wms-alert-copy"><p class="wms-alert-eyebrow"></p><h2 class="wms-alert-title" id="wms-alert-title"></h2><p class="wms-alert-message" id="wms-alert-message"></p></div>' +
      '<button class="wms-alert-close" type="button" aria-label="Cerrar">×</button></div>' +
      '<div class="wms-alert-actions"><button class="wms-alert-btn wms-alert-btn-secondary" type="button"></button><button class="wms-alert-btn wms-alert-btn-primary" type="button"></button></div>' +
      "</section></div>";
    document.body.appendChild(root);
    root
      .querySelector(".wms-alert-close")
      .addEventListener("click", function () {
        settle(false);
      });
    root
      .querySelector(".wms-alert-btn-secondary")
      .addEventListener("click", function () {
        settle(false);
      });
    root
      .querySelector(".wms-alert-btn-primary")
      .addEventListener("click", function () {
        settle(true);
      });
    root
      .querySelector(".wms-alert-backdrop")
      .addEventListener("click", function (event) {
        if (event.target === event.currentTarget) settle(false);
      });
    return root;
  }
  function settle(value) {
    if (!root || !activeResolve) return;
    var resolve = activeResolve;
    activeResolve = null;
    var backdrop = root.querySelector(".wms-alert-backdrop");
    backdrop.classList.remove("is-open");
    backdrop.setAttribute("aria-hidden", "true");
    document.documentElement.classList.remove("wms-alert-lock");
    if (previousFocus && typeof previousFocus.focus === "function")
      previousFocus.focus({ preventScroll: true });
    window.setTimeout(function () {
      resolve(Boolean(value));
    }, 20);
  }
  function open(options) {
    options = options || {};
    queue = queue.then(function () {
      return new Promise(function (resolve) {
        if (!ensureRoot()) {
          nativeAlert(safeText(options.message));
          resolve(true);
          return;
        }
        var type = TYPES[options.type] ? options.type : "info";
        var preset = TYPES[type];
        var dialog = root.querySelector(".wms-alert-dialog");
        dialog.dataset.type = type;
        dialog.querySelector(".wms-alert-icon").textContent =
          options.icon || preset.icon;
        dialog.querySelector(".wms-alert-eyebrow").textContent =
          options.eyebrow || preset.eyebrow;
        dialog.querySelector(".wms-alert-title").textContent =
          options.title || preset.title;
        dialog.querySelector(".wms-alert-message").textContent = safeText(
          options.message,
        );
        var cancel = dialog.querySelector(".wms-alert-btn-secondary");
        var accept = dialog.querySelector(".wms-alert-btn-primary");
        cancel.textContent = options.cancelText || "Cancelar";
        cancel.hidden = !options.confirm;
        accept.textContent =
          options.confirmText ||
          (options.confirm ? "Sí, confirmar" : "Entendido");
        previousFocus = document.activeElement;
        activeResolve = resolve;
        var backdrop = root.querySelector(".wms-alert-backdrop");
        backdrop.setAttribute("aria-hidden", "false");
        document.documentElement.classList.add("wms-alert-lock");
        requestAnimationFrame(function () {
          backdrop.classList.add("is-open");
          accept.focus();
        });
      });
    });
    return queue;
  }
  function toast(message, type, options) {
    options = options || {};
    if (!ensureRoot()) return;
    type = normalizeType(type);
    var preset = TYPES[type];
    var node = document.createElement("article");
    node.className = "wms-alert-toast";
    node.dataset.type = type;
    node.innerHTML =
      '<span class="wms-alert-toast-icon" aria-hidden="true"></span><div><strong></strong><p></p></div><button class="wms-alert-toast-close" type="button" aria-label="Cerrar">×</button>';
    node.querySelector(".wms-alert-toast-icon").textContent = preset.icon;
    node.querySelector("strong").textContent = options.title || preset.title;
    node.querySelector("p").textContent = safeText(message);
    function remove() {
      if (!node.isConnected) return;
      node.classList.add("is-leaving");
      window.setTimeout(function () {
        node.remove();
      }, 190);
    }
    node.querySelector("button").addEventListener("click", remove);
    root.querySelector(".wms-alert-toast-stack").appendChild(node);
    window.setTimeout(remove, Number(options.duration) || 9000);
  }
  function replay(context) {
    if (!context || !context.target || !context.target.isConnected) return;
    bypassConfirm = true;
    try {
      if (context.type === "submit") {
        var form = context.target;
        if (typeof form.requestSubmit === "function")
          form.requestSubmit(context.submitter || undefined);
        else form.submit();
      } else if (context.type === "click") {
        context.target.click();
      }
    } finally {
      window.setTimeout(function () {
        bypassConfirm = false;
      }, 0);
    }
  }
  function remember(event) {
    interaction = {
      type: event.type,
      target:
        event.type === "submit"
          ? event.target
          : event.target.closest(
              "button, a, input[type='submit'], input[type='button']",
            ) || event.target,
      submitter: event.submitter || null,
    };
    window.setTimeout(function () {
      interaction = null;
    }, 0);
  }

  document.addEventListener("click", remember, true);
  document.addEventListener("submit", remember, true);
  document.addEventListener("keydown", function (event) {
    if (!activeResolve) return;
    if (event.key === "Escape") {
      event.preventDefault();
      settle(false);
    }
    if (event.key === "Tab" && root) {
      var items = Array.prototype.filter.call(
        root.querySelectorAll("button:not([hidden])"),
        function (el) {
          return el.offsetParent !== null;
        },
      );
      if (!items.length) return;
      var first = items[0],
        last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });

  var api = {
    alert: function (options) {
      return open(typeof options === "string" ? { message: options } : options);
    },
    confirm: function (options) {
      options =
        typeof options === "string" ? { message: options } : options || {};
      options.confirm = true;
      options.type = options.type || "danger";
      return open(options);
    },
    success: function (message, options) {
      toast(message, "success", options);
    },
    error: function (message, options) {
      toast(message, "error", options);
    },
    warning: function (message, options) {
      toast(message, "warning", options);
    },
    info: function (message, options) {
      toast(message, "info", options);
    },
    toast: toast,
    nativeAlert: nativeAlert,
    nativeConfirm: nativeConfirm,
  };
  window.WMSAlert = api;
  window.alert = function (message) {
    api.alert({ message: message, type: "info" });
  };
  window.confirm = function (message) {
    if (bypassConfirm) {
      bypassConfirm = false;
      return true;
    }
    var context = interaction;
    api.confirm({ message: message }).then(function (accepted) {
      if (accepted) replay(context);
    });
    return false;
  };

  function boot() {
    ensureRoot();
    document
      .querySelectorAll("[data-wms-django-message]")
      .forEach(function (node) {
        toast(node.textContent.trim(), node.dataset.tags || "info");
        node.remove();
      });
  }
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  else boot();
})();
