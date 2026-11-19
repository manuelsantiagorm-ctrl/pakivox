/* PAKIVOX AF-MC1 · navegación responsive y accesibilidad */
(function () {
  "use strict";

  function activatePane(name) {
    document.querySelectorAll("[data-mc-pane-name]").forEach(function (pane) {
      pane.classList.toggle("is-active", pane.dataset.mcPaneName === name);
    });
    document.querySelectorAll("[data-mc-pane]").forEach(function (button) {
      button.classList.toggle("is-active", button.dataset.mcPane === name);
      button.setAttribute("aria-selected", button.dataset.mcPane === name ? "true" : "false");
    });
    if (name === "conversation") {
      const messages = document.getElementById("chat-messages");
      if (messages) messages.scrollTop = messages.scrollHeight;
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-mc-pane]").forEach(function (button) {
      button.addEventListener("click", function () { activatePane(button.dataset.mcPane); });
    });

    document.querySelectorAll(".conversation-mini").forEach(function (row) {
      row.addEventListener("click", function () {
        try { sessionStorage.setItem("pakivox-messaging-pane", "conversation"); } catch (error) {}
      });
    });

    if (window.matchMedia("(max-width: 1050px)").matches) {
      let pane = "conversation";
      try { pane = sessionStorage.getItem("pakivox-messaging-pane") || pane; } catch (error) {}
      activatePane(pane);
    }
  });
})();
