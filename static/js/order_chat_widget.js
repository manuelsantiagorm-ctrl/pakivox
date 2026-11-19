// ============================================================
// PAKIVOX - WhatsApp flotante en detalle de pedido
// Archivo: static/sales/js/order_chat_widget.js
// ============================================================

document.addEventListener("DOMContentLoaded", function () {
  initWmsOrderChatWidget();
  initWmsChatProductPicker();
  scrollWmsOrderChatToBottom();

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      closeWmsChatProductPicker();
    }
  });
});

function normalizeWmsText(value) {
  return (value || "")
    .toString()
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .trim();
}

function scrollWmsOrderChatToBottom() {
  const chatBox = document.getElementById("wmsOrderChatMessages");

  if (chatBox) {
    chatBox.scrollTop = chatBox.scrollHeight;
  }
}

function initWmsOrderChatWidget() {
  const widget = document.getElementById("wmsOrderChatWidget");
  const handle = document.getElementById("wmsOrderChatDragHandle");
  const resizeHandle = document.getElementById("wmsOrderChatResizeHandle");
  const minimized = document.getElementById("wmsOrderChatMinimized");

  const toggleSizeBtn = document.getElementById("wmsOrderChatToggleSize");
  const resetBtn = document.getElementById("wmsOrderChatReset");
  const minimizeBtn = document.getElementById("wmsOrderChatMinimize");
  const restoreBtn =
    document.getElementById("wmsOrderChatRestore") || minimized;

  if (!widget || !handle) {
    return;
  }

  const storageKey =
    widget.dataset.storageKey ||
    "wms360_order_chat_float_" + window.location.pathname;

  function isMobileViewport() {
    return window.matchMedia("(max-width: 768px)").matches;
  }

  function saveState() {
    const rect = widget.getBoundingClientRect();

    const state = {
      left: rect.left,
      top: rect.top,
      width: rect.width,
      height: rect.height,
      isLarge: widget.classList.contains("is-large"),
      isMinimized: widget.style.display === "none",
    };

    localStorage.setItem(storageKey, JSON.stringify(state));
  }

  function clampPosition(left, top, width, height) {
    const margin = 8;
    const maxLeft = window.innerWidth - width - margin;
    const maxTop = window.innerHeight - height - margin;

    return {
      left: Math.max(margin, Math.min(left, maxLeft)),
      top: Math.max(margin, Math.min(top, maxTop)),
    };
  }

  function applySavedState() {
    const rawState = localStorage.getItem(storageKey);

    if (!rawState) {
      return;
    }

    try {
      const state = JSON.parse(rawState);

      if (state.isMinimized) {
        widget.style.display = "none";

        if (minimized) {
          minimized.classList.add("is-open");
        }

        return;
      }

      if (state.isLarge && !isMobileViewport()) {
        widget.classList.add("is-large");
      }

      const width = Math.max(
        330,
        Math.min(state.width || 460, window.innerWidth - 16),
      );

      const height = Math.max(
        390,
        Math.min(state.height || 680, window.innerHeight - 16),
      );

      if (isMobileViewport()) {
        widget.style.removeProperty("left");
        widget.style.removeProperty("top");
        widget.style.removeProperty("right");
        widget.style.removeProperty("bottom");
        widget.style.removeProperty("width");
        widget.style.removeProperty("height");
        return;
      }

      const position = clampPosition(
        state.left || window.innerWidth - width - 24,
        state.top || 96,
        width,
        height,
      );

      widget.style.left = position.left + "px";
      widget.style.top = position.top + "px";
      widget.style.right = "auto";
      widget.style.bottom = "auto";
      widget.style.width = width + "px";
      widget.style.height = height + "px";
    } catch (error) {
      localStorage.removeItem(storageKey);
    }
  }

  applySavedState();

  let isDragging = false;
  let dragStartX = 0;
  let dragStartY = 0;
  let dragStartLeft = 0;
  let dragStartTop = 0;

  handle.addEventListener("mousedown", function (event) {
    if (isMobileViewport()) {
      return;
    }
    const blockedTags = ["BUTTON", "A", "TEXTAREA", "INPUT", "SELECT", "I"];

    if (blockedTags.includes(event.target.tagName)) {
      return;
    }

    const rect = widget.getBoundingClientRect();

    isDragging = true;
    dragStartX = event.clientX;
    dragStartY = event.clientY;
    dragStartLeft = rect.left;
    dragStartTop = rect.top;

    widget.classList.add("is-dragging");
    widget.style.left = rect.left + "px";
    widget.style.top = rect.top + "px";
    widget.style.right = "auto";
    widget.style.bottom = "auto";

    document.body.style.userSelect = "none";
    event.preventDefault();
  });

  document.addEventListener("mousemove", function (event) {
    if (!isDragging) {
      return;
    }

    const rect = widget.getBoundingClientRect();

    const nextLeft = dragStartLeft + (event.clientX - dragStartX);
    const nextTop = dragStartTop + (event.clientY - dragStartY);

    const position = clampPosition(nextLeft, nextTop, rect.width, rect.height);

    widget.style.left = position.left + "px";
    widget.style.top = position.top + "px";
  });

  document.addEventListener("mouseup", function () {
    if (!isDragging) {
      return;
    }

    isDragging = false;
    widget.classList.remove("is-dragging");
    document.body.style.userSelect = "";

    saveState();
  });

  if (resizeHandle) {
    let isResizing = false;
    let resizeStartX = 0;
    let resizeStartY = 0;
    let resizeStartWidth = 0;
    let resizeStartHeight = 0;

    resizeHandle.addEventListener("mousedown", function (event) {
      if (isMobileViewport()) {
        return;
      }
      const rect = widget.getBoundingClientRect();

      isResizing = true;
      resizeStartX = event.clientX;
      resizeStartY = event.clientY;
      resizeStartWidth = rect.width;
      resizeStartHeight = rect.height;

      widget.style.left = rect.left + "px";
      widget.style.top = rect.top + "px";
      widget.style.right = "auto";
      widget.style.bottom = "auto";

      document.body.style.userSelect = "none";
      event.preventDefault();
      event.stopPropagation();
    });

    document.addEventListener("mousemove", function (event) {
      if (!isResizing) {
        return;
      }

      const nextWidth = resizeStartWidth + (event.clientX - resizeStartX);
      const nextHeight = resizeStartHeight + (event.clientY - resizeStartY);

      const width = Math.max(330, Math.min(nextWidth, window.innerWidth - 16));
      const height = Math.max(
        390,
        Math.min(nextHeight, window.innerHeight - 16),
      );

      widget.style.width = width + "px";
      widget.style.height = height + "px";
    });

    document.addEventListener("mouseup", function () {
      if (!isResizing) {
        return;
      }

      isResizing = false;
      document.body.style.userSelect = "";

      saveState();
    });
  }

  function toggleSize() {
    if (isMobileViewport()) {
      widget.classList.toggle("is-large");
      saveState();
      return;
    }
    if (!widget.classList.contains("is-large")) {
      const width = Math.min(880, window.innerWidth - 48);
      const height = Math.min(780, window.innerHeight - 48);

      widget.classList.add("is-large");
      widget.style.width = width + "px";
      widget.style.height = height + "px";
      widget.style.left = Math.max(24, window.innerWidth - width - 24) + "px";
      widget.style.top = Math.max(22, window.innerHeight - height - 22) + "px";
      widget.style.right = "auto";
      widget.style.bottom = "auto";
    } else {
      widget.classList.remove("is-large");
      widget.style.height = Math.min(640, window.innerHeight - 130) + "px";
      widget.style.width = "420px";
      widget.style.left = Math.max(22, window.innerWidth - 420 - 22) + "px";
      widget.style.top = Math.max(22, window.innerHeight - Math.min(640, window.innerHeight - 130) - 22) + "px";
      widget.style.right = "auto";
      widget.style.bottom = "auto";
    }

    saveState();
  }

  function resetPosition() {
    widget.classList.remove("is-large");
    widget.style.display = "grid";
    widget.style.left = "auto";
    widget.style.top = "auto";
    widget.style.right = "22px";
    widget.style.bottom = "22px";
    widget.style.width = "420px";
    widget.style.height = "min(640px, calc(100dvh - 130px))";

    if (minimized) {
      minimized.classList.remove("is-open");
    }

    localStorage.removeItem(storageKey);
    scrollWmsOrderChatToBottom();
  }

  function minimizeWidget() {
    widget.style.display = "none";

    if (minimized) {
      minimized.classList.add("is-open");
    }

    saveState();
  }

  function restoreWidget() {
    widget.style.display = "grid";

    if (minimized) {
      minimized.classList.remove("is-open");
    }

    saveState();
    scrollWmsOrderChatToBottom();
  }

  if (toggleSizeBtn) {
    toggleSizeBtn.addEventListener("click", toggleSize);
  }

  if (resetBtn) {
    resetBtn.addEventListener("click", resetPosition);
  }

  if (minimizeBtn) {
    minimizeBtn.addEventListener("click", minimizeWidget);
  }

  if (restoreBtn) {
    restoreBtn.addEventListener("click", restoreWidget);
  }

  window.addEventListener("resize", function () {
    if (isMobileViewport()) {
      widget.style.removeProperty("left");
      widget.style.removeProperty("top");
      widget.style.removeProperty("right");
      widget.style.removeProperty("bottom");
      widget.style.removeProperty("width");
      widget.style.removeProperty("height");
      return;
    }
    const rect = widget.getBoundingClientRect();

    const position = clampPosition(
      rect.left,
      rect.top,
      rect.width,
      rect.height,
    );

    widget.style.left = position.left + "px";
    widget.style.top = position.top + "px";

    saveState();
  });
}

function initWmsChatProductPicker() {
  const modal = document.getElementById("wmsChatProductPickerModal");
  const openBtn = document.getElementById("wmsOpenChatProductPicker");
  const closeBtn = document.getElementById("wmsCloseChatProductPicker");
  const searchInput = document.getElementById("wmsChatProductSearchInput");
  const selectedInput = document.getElementById("wmsChatSelectedVariantInput");
  const selectedText = document.getElementById("wmsChatSelectedVariantText");
  const priceInput = document.getElementById("wmsChatQuickPrice");
  const form = document.getElementById("wmsOrderChatProductForm");
  const emptyMessage = document.getElementById("wmsChatProductPickerEmpty");

  if (!modal || !openBtn || !searchInput || !selectedInput || !selectedText) {
    return;
  }

  const options = Array.from(
    modal.querySelectorAll(".wms-chat-product-option"),
  );

  function openModal() {
    modal.hidden = false;
    searchInput.value = "";
    filterProducts();

    setTimeout(function () {
      searchInput.focus();
    }, 80);
  }

  function closeModal() {
    modal.hidden = true;
  }

  function filterProducts() {
    const query = normalizeWmsText(searchInput.value);
    let visibleCount = 0;

    options.forEach(function (option) {
      const text = normalizeWmsText(option.dataset.search);

      if (!query || text.includes(query)) {
        option.hidden = false;
        visibleCount += 1;
      } else {
        option.hidden = true;
      }
    });

    if (emptyMessage) {
      emptyMessage.hidden = visibleCount !== 0;
    }
  }

  openBtn.addEventListener("click", openModal);

  if (closeBtn) {
    closeBtn.addEventListener("click", closeModal);
  }

  searchInput.addEventListener("input", filterProducts);

  options.forEach(function (option) {
    option.addEventListener("click", function () {
      selectedInput.value = option.dataset.variantId;
      selectedText.textContent = option.dataset.label;

      if (priceInput && option.dataset.price) {
        priceInput.value = Number(option.dataset.price || 0).toFixed(2);
      }

      closeModal();
    });
  });

  modal.addEventListener("click", function (event) {
    if (event.target === modal) {
      closeModal();
    }
  });

  if (form) {
    form.addEventListener("submit", function (event) {
      if (!selectedInput.value) {
        event.preventDefault();
        openModal();
      }
    });
  }
}

function closeWmsChatProductPicker() {
  const modal = document.getElementById("wmsChatProductPickerModal");

  if (modal && !modal.hidden) {
    modal.hidden = true;
  }
}
