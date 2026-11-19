// Marco del sistema (templates/base.html): menu lateral, avisos, sonidos y modal Premium.
// Codigo movido desde la plantilla, en el mismo orden en que estaba.

/* ===== origen: <script id="pakivox-nav-responsive-v1"> ===== */
(() => {
  "use strict";

  const sidebar = document.getElementById("appSidebar");
  const overlay = document.getElementById("sidebarOverlay");
  const topButton = document.getElementById("mobileSidebarButton");
  const bottomButton = document.getElementById("mobileBottomMenuButton");
  const desktopBreakpoint = window.matchMedia("(min-width: 1025px)");

  if (!sidebar || !overlay || !topButton) return;

  const isDrawerMode = () => !desktopBreakpoint.matches;

  const setExpanded = (expanded) => {
    topButton.setAttribute("aria-expanded", expanded ? "true" : "false");
    topButton.setAttribute("aria-label", expanded ? "Cerrar navegación" : "Abrir navegación");
    const icon = topButton.querySelector("i");
    if (icon) icon.className = expanded ? "bi bi-x-lg" : "bi bi-list";
  };

  const openDrawer = () => {
    if (!isDrawerMode()) return;
    sidebar.classList.add("is-open");
    overlay.classList.add("is-visible");
    overlay.setAttribute("aria-hidden", "false");
    document.body.classList.add("sidebar-drawer-open");
    setExpanded(true);
  };

  const closeDrawer = () => {
    sidebar.classList.remove("is-open");
    overlay.classList.remove("is-visible");
    overlay.setAttribute("aria-hidden", "true");
    document.body.classList.remove("sidebar-drawer-open");
    setExpanded(false);
  };

  const toggleDrawer = () => {
    sidebar.classList.contains("is-open") ? closeDrawer() : openDrawer();
  };

  topButton.addEventListener("click", toggleDrawer);
  if (bottomButton) bottomButton.addEventListener("click", toggleDrawer);
  overlay.addEventListener("click", closeDrawer);

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeDrawer();
  });

  sidebar.querySelectorAll("a[data-menu-link]").forEach((link) => {
    link.addEventListener("click", () => {
      if (isDrawerMode()) closeDrawer();
    });
  });

  const syncBreakpoint = () => {
    if (desktopBreakpoint.matches) closeDrawer();
  };

  if (typeof desktopBreakpoint.addEventListener === "function") {
    desktopBreakpoint.addEventListener("change", syncBreakpoint);
  } else if (typeof desktopBreakpoint.addListener === "function") {
    desktopBreakpoint.addListener(syncBreakpoint);
  }

  const normalize = (path) => {
    if (!path) return "/";
    return path.length > 1 && path.endsWith("/") ? path.slice(0, -1) : path;
  };

  const currentPath = normalize(window.location.pathname);
  let best = null;
  let bestLength = -1;

  document.querySelectorAll("[data-mobile-nav]").forEach((link) => {
    const href = link.getAttribute("href");
    if (!href || href.startsWith("#")) return;

    const path = normalize(new URL(href, window.location.origin).pathname);
    const match =
      currentPath === path ||
      (path !== "/" && currentPath.startsWith(path + "/"));

    if (match && path.length > bestLength) {
      best = link;
      bestLength = path.length;
    }
  });

  if (best) best.classList.add("is-active");
})();

/* ===== origen: <script id="sin-id"> ===== */
      function toggleMenu(menuId, arrowId) {
        const menu = document.getElementById(menuId);
        const arrow = document.getElementById(arrowId);

        if (!menu || !arrow) {
          return;
        }

        const shouldOpen = !menu.classList.contains("is-open");

        menu.classList.toggle("is-open", shouldOpen);
        arrow.classList.toggle("bi-chevron-up", shouldOpen);
        arrow.classList.toggle("bi-chevron-down", !shouldOpen);
      }

      function openMenu(menuId, arrowId, toggleId) {
        const menu = document.getElementById(menuId);
        const arrow = document.getElementById(arrowId);
        const toggle = document.getElementById(toggleId);

        if (menu) {
          menu.classList.add("is-open");
        }

        if (arrow) {
          arrow.classList.remove("bi-chevron-down");
          arrow.classList.add("bi-chevron-up");
        }

        if (toggle) {
          toggle.classList.add("is-active");
        }
      }

      function toggleInventoryMenu() {
        toggleMenu("inventory-menu", "inventory-arrow");
      }

      function toggleWarehouseMenu() {
        toggleMenu("warehouse-menu", "warehouse-arrow");
      }

      function toggleSalesMenu() {
        toggleMenu("sales-menu", "sales-arrow");
      }

      function toggleMessagingMenu() {
        toggleMenu("messaging-menu", "messaging-arrow");
      }

      function toggleAccountingMenu() {
        toggleMenu("accounting-menu", "accounting-arrow");
      }

      function toggleTeamMenu() {
        toggleMenu("team-menu", "team-arrow");
      }

      function togglePromotionsMenu() {
        toggleMenu("promotions-menu", "promotions-arrow");
      }

      function normalizePath(path) {
        if (!path) {
          return "/";
        }

        if (path.length > 1 && path.endsWith("/")) {
          return path.slice(0, -1);
        }

        return path;
      }

      function activateCurrentMenu() {
        const path = normalizePath(window.location.pathname);

        const currentSearch = window.location.search || "";

        /*
         * Los combos pertenecen al menú Promociones.
         * El resto de /inventory pertenece a Inventario.
         */
        if (
          path.startsWith("/inventory/combos") ||
          path.startsWith("/promociones")
        ) {
          openMenu("promotions-menu", "promotions-arrow", "promotions-toggle");
        } else if (path.startsWith("/inventory")) {
          openMenu("inventory-menu", "inventory-arrow", "inventory-toggle");
        }

        if (path.startsWith("/warehouse")) {
          openMenu("warehouse-menu", "warehouse-arrow", "warehouse-toggle");
        }

        if (path.startsWith("/sales")) {
          openMenu("sales-menu", "sales-arrow", "sales-toggle");
        }

        if (path.startsWith("/messaging")) {
          openMenu("messaging-menu", "messaging-arrow", "messaging-toggle");
        }

        if (path.startsWith("/finanzas") || path.startsWith("/accounting")) {
          openMenu("accounting-menu", "accounting-arrow", "accounting-toggle");
        }

        if (path.startsWith("/accounts/equipo")) {
          openMenu("team-menu", "team-arrow", "team-toggle");
        }

        let bestLink = null;
        let bestScore = 0;

        document.querySelectorAll("[data-menu-link]").forEach(function (link) {
          link.classList.remove("is-active");

          const rawHref = link.getAttribute("href");

          if (
            !rawHref ||
            rawHref.startsWith("#") ||
            rawHref.startsWith("javascript:")
          ) {
            return;
          }

          const hrefUrl = new URL(rawHref, window.location.origin);

          const hrefPath = normalizePath(hrefUrl.pathname);

          const hrefSearch = hrefUrl.search || "";

          let isMatch = false;

          if (hrefSearch) {
            isMatch = path === hrefPath && currentSearch === hrefSearch;
          } else {
            isMatch =
              path === hrefPath ||
              (hrefPath !== "/" && path.startsWith(hrefPath + "/"));
          }

          if (!isMatch) {
            return;
          }

          const score =
            hrefPath.length + (hrefSearch ? hrefSearch.length + 1000 : 0);

          if (score > bestScore) {
            bestLink = link;
            bestScore = score;
          }
        });

        if (bestLink) {
          bestLink.classList.add("is-active");
        }
      }

      function initializeToasts() {
        const stack = document.getElementById("toast-stack");

        if (!stack) {
          return;
        }

        setTimeout(function () {
          stack.style.opacity = "0";
          stack.style.transform = "translateY(-8px)";
          stack.style.transition = "all .25s ease";

          setTimeout(function () {
            stack.remove();
          }, 300);
        }, 7000);
      }

      document.addEventListener("DOMContentLoaded", function () {
        activateCurrentMenu();
        initializeToasts();
      });

      function openPremiumModal() {
    document.getElementById('premiumModal').classList.add('active');
  }
  function closePremiumModal() {
    document.getElementById('premiumModal').classList.remove('active');
  }
    

/* ===== origen: <script id="sin-id"> ===== */
  (function () {
    "use strict";

    const idElement = document.getElementById(
      "notification-sound-id"
    );

    const areaElement = document.getElementById(
      "notification-sound-area"
    );

    const levelElement = document.getElementById(
      "notification-sound-level"
    );

    const storageElement = document.getElementById(
      "notification-storage-key"
    );

    if (
      !idElement ||
      !areaElement ||
      !levelElement ||
      !storageElement
    ) {
      return;
    }

    const notificationId = Number(
      JSON.parse(idElement.textContent) || 0
    );

    const sourceApp = String(
      JSON.parse(areaElement.textContent) || ""
    ).toLowerCase();

    const notificationLevel = String(
      JSON.parse(levelElement.textContent) || "info"
    ).toLowerCase();

    const storageKey = String(
      JSON.parse(storageElement.textContent) || ""
    );

    if (!storageKey) {
      return;
    }

    /*
     * Cuando no existen alertas, dejamos el navegador preparado
     * para que la primera alerta nueva sí produzca sonido.
     */
    if (!notificationId) {
      try {
        localStorage.setItem(storageKey, "0");
      } catch (error) {
        // La página continúa funcionando.
      }

      return;
    }

    let previousNotificationId = 0;

    try {
      previousNotificationId = Number(
        localStorage.getItem(storageKey) || 0
      );
    } catch (error) {
      previousNotificationId = 0;
    }

    if (notificationId <= previousNotificationId) {
      return;
    }

    /*
     * Marcamos la alerta antes de reproducir para impedir sonidos
     * duplicados por doble carga o recarga rápida.
     */
    try {
      localStorage.setItem(
        storageKey,
        String(notificationId)
      );
    } catch (error) {
      // El audio todavía puede reproducirse.
    }

    const AREA_SOUNDS = {
      payments: {
        wave: "sine",
        frequencies: [740, 988]
      },
      accounting: {
        wave: "sine",
        frequencies: [698, 880]
      },
      inventory: {
        wave: "triangle",
        frequencies: [880, 659]
      },
      purchases: {
        wave: "triangle",
        frequencies: [587, 784]
      },
      warehouse: {
        wave: "square",
        frequencies: [330, 440]
      },
      shipping_hub: {
        wave: "sawtooth",
        frequencies: [523, 784]
      },
      sales: {
        wave: "sine",
        frequencies: [659, 880]
      },
      crm: {
        wave: "sine",
        frequencies: [622, 831]
      },
      messaging: {
        wave: "triangle",
        frequencies: [988, 1175]
      },
      dashboard: {
        wave: "sine",
        frequencies: [523, 659]
      }
    };

    const URGENCY_PATTERNS = {
      info: {
        repetitions: 1,
        duration: 0.18,
        pause: 0.08,
        volume: 0.10
      },
      success: {
        repetitions: 2,
        duration: 0.14,
        pause: 0.07,
        volume: 0.12
      },
      warning: {
        repetitions: 2,
        duration: 0.20,
        pause: 0.11,
        volume: 0.16
      },
      danger: {
        repetitions: 3,
        duration: 0.16,
        pause: 0.07,
        volume: 0.21
      },
      critical: {
        repetitions: 3,
        duration: 0.18,
        pause: 0.05,
        volume: 0.23
      },
      error: {
        repetitions: 3,
        duration: 0.18,
        pause: 0.05,
        volume: 0.23
      }
    };

    const areaSound = AREA_SOUNDS[sourceApp] || {
      wave: "sine",
      frequencies: [660, 880]
    };

    const urgency = URGENCY_PATTERNS[notificationLevel] ||
      URGENCY_PATTERNS.info;

    let soundPlayed = false;

    function createTone(
      audioContext,
      frequency,
      startTime,
      duration,
      volume
    ) {
      const oscillator = audioContext.createOscillator();
      const gainNode = audioContext.createGain();
      const endTime = startTime + duration;

      oscillator.type = areaSound.wave;

      oscillator.frequency.setValueAtTime(
        frequency,
        startTime
      );

      gainNode.gain.setValueAtTime(
        0.0001,
        startTime
      );

      gainNode.gain.exponentialRampToValueAtTime(
        volume,
        startTime + 0.025
      );

      gainNode.gain.exponentialRampToValueAtTime(
        0.0001,
        endTime
      );

      oscillator.connect(gainNode);
      gainNode.connect(audioContext.destination);

      oscillator.start(startTime);
      oscillator.stop(endTime + 0.02);
    }

    function playAuthorizedNotificationSound() {
      if (soundPlayed) {
        return;
      }

      const AudioContextClass =
        window.AudioContext || window.webkitAudioContext;

      if (!AudioContextClass) {
        return;
      }

      const audioContext = new AudioContextClass();

      const playSequence = function () {
        const startTime = audioContext.currentTime + 0.03;
        const noteDuration = urgency.duration / 2;

        for (
          let repetition = 0;
          repetition < urgency.repetitions;
          repetition += 1
        ) {
          const repetitionStart =
            startTime +
            repetition * (
              urgency.duration + urgency.pause
            );

          areaSound.frequencies.forEach(
            function (frequency, noteIndex) {
              createTone(
                audioContext,
                frequency,
                repetitionStart +
                  noteIndex * noteDuration,
                noteDuration,
                urgency.volume
              );
            }
          );
        }

        soundPlayed = true;

        document.removeEventListener(
          "pointerdown",
          playAuthorizedNotificationSound
        );

        document.removeEventListener(
          "keydown",
          playAuthorizedNotificationSound
        );
      };

      if (audioContext.state === "suspended") {
        audioContext.resume()
          .then(playSequence)
          .catch(function () {
            soundPlayed = false;
          });

        return;
      }

      playSequence();
    }

    /*
     * Chrome puede bloquear el audio hasta que exista una interacción.
     * Si sucede, el aviso se reproducirá con el primer clic o tecla.
     */
    try {
      playAuthorizedNotificationSound();
    } catch (error) {
      soundPlayed = false;
    }

    if (!soundPlayed) {
      document.addEventListener(
        "pointerdown",
        playAuthorizedNotificationSound,
        { once: true }
      );

      document.addEventListener(
        "keydown",
        playAuthorizedNotificationSound,
        { once: true }
      );
    }
  })();

/* ===== Antes eran atributos onclick/onchange en la plantilla ===== */
document.addEventListener("click", (event) => {
  const trigger = event.target.closest("[data-accion]");
  if (!trigger) return;
  const action = window[trigger.dataset.accion];
  if (typeof action === "function") action();
});
document.addEventListener("change", (event) => {
  const field = event.target.closest("[data-enviar-al-cambiar]");
  if (field && field.form) field.form.submit();
});
