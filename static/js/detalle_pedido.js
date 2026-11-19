// ============================================================
// PAKIVOX - Detalle de pedido
// Archivo: static/js/detalle_pedido.js
// ============================================================

let productToastTimeout = null;
let variantSearchDebounce = null;
let variantSearchController = null;
let variantChatSearchDebounce = null;
let variantChatSearchController = null;

document.addEventListener("DOMContentLoaded", function () {
  initProductTools();

  const chatBox = document.getElementById("wmsOrderChatMessages");

  if (chatBox) {
    chatBox.scrollTop = chatBox.scrollHeight;
  }
});

document.addEventListener("keydown", function (event) {
  if (event.key === "Escape") {
    closeProductModal();
  }
});

function showProductToast(message, type = "info", title = "Aviso") {
  const toast = document.getElementById("productToast");
  const toastTitle = document.getElementById("productToastTitle");
  const toastMessage = document.getElementById("productToastMessage");

  if (!toast || !toastTitle || !toastMessage) {
    return;
  }

  toast.classList.remove("success", "error", "warning", "info");
  toast.classList.add(type);

  toastTitle.textContent = title;
  toastMessage.textContent = message;

  toast.classList.add("is-open");

  if (productToastTimeout) {
    clearTimeout(productToastTimeout);
  }

  productToastTimeout = setTimeout(function () {
    toast.classList.remove("is-open");
  }, 3200);
}

function cleanNumber(value) {
  const number = Number(value || 0);

  if (!Number.isFinite(number)) {
    return 0;
  }

  if (Number.isInteger(number)) {
    return number;
  }

  return Number(number.toFixed(3));
}

function getSelectedStockInfo() {
  const stockInput = document.getElementById("selectedVariantStock");

  if (!stockInput) {
    return {
      stock: 0,
      rawStock: 0,
      stockMode: "unit",
      baseUnit: "Pieza",
      commercialUnit: "Pieza",
      conversionFactor: 1,
    };
  }

  return {
    stock: Number(stockInput.value || 0),
    rawStock: Number(stockInput.dataset.rawStock || stockInput.value || 0),
    stockMode: stockInput.dataset.stockMode || "unit",
    baseUnit: stockInput.dataset.baseUnit || "Pieza",
    commercialUnit: stockInput.dataset.commercialUnit || "Pieza",
    conversionFactor: Number(stockInput.dataset.conversionFactor || 1),
  };
}

// ------------------------------------------------------------
// Búsqueda de variantes en vivo contra el servidor
// ------------------------------------------------------------

function getOrderVariantSearchUrl() {
  const modal = document.getElementById("productModal");
  return modal ? modal.dataset.searchUrl : "";
}

function renderVariantCard(variant) {
  const outOfStock = variant.available_internal <= 0;

    const stockBadge = outOfStock
      ? `<span class="stock-pill danger">Sin stock</span>`
      : `<span class="stock-pill success">${variant.available_display} ${variant.is_bulk ? variant.commercial_unit + " disp." : "disp."}</span>`;

    const volumeChip = variant.is_bulk
      ? `<div class="volume-chip"><i class="bi bi-truck"></i> 1 ${variant.commercial_unit} = ${variant.conversion_factor} ${variant.base_unit}</div>`
      : `<div class="unit-chip"><i class="bi bi-box-seam"></i> Producto unitario</div>`;

  const wholesale = variant.wholesale_price
    ? `<span class="wholesale-price">Mayoreo $${Number(variant.wholesale_price).toFixed(2)}</span>`
    : "";

  const image = variant.image_url
    ? `<img src="${variant.image_url}" alt="${variant.product_name}">`
    : `<div class="product-card-image-empty"><i class="bi bi-image"></i><span>Sin imagen</span></div>`;

  const button = document.createElement("button");
  button.type = "button";
  button.className =
    "product-option professional-product-card" +
    (outOfStock ? " out-of-stock" : "");
  button.disabled = outOfStock;
  button.dataset.variantId = variant.id;
  button.dataset.sku = variant.sku;
  button.dataset.productName = variant.product_name;
  button.dataset.stock = variant.available_internal;
  button.dataset.displayStock = variant.available_display;
  button.dataset.stockMode = variant.is_bulk ? "bulk" : "unit";
  button.dataset.baseUnit = variant.base_unit;
  button.dataset.commercialUnit = variant.commercial_unit;
  button.dataset.conversionFactor = variant.conversion_factor;
  button.dataset.price = variant.sale_price;
  button.dataset.wholesalePrice = variant.wholesale_price || "";
  button.dataset.image = variant.image_url || "";

  button.innerHTML = `
    <div class="product-card-image">${image}</div>
    <div class="product-card-content">
      <div class="product-card-top">
        <div><strong>${variant.sku}</strong><span>${variant.product_name}</span></div>
        ${stockBadge}
      </div>
      <div class="product-option-meta">
        <span>Talla: <b>${variant.talla || "-"}</b></span>
        <span>Color: <b>${variant.color || "-"}</b></span>
        <span>Corte: <b>${variant.corte || "-"}</b></span>
      </div>
      ${volumeChip}
      <div class="product-option-footer">
        <small>${variant.category || "Sin categoría"}</small>
        <div class="product-footer-right">
          ${wholesale}
          <strong>$${Number(variant.sale_price).toFixed(2)}</strong>
        </div>
      </div>
    </div>
  `;
  button.addEventListener("click", function (event) {
      selectProductVariant(button);
    });

  return button;
}

async function runVariantSearch(query) {
  const searchUrl = getOrderVariantSearchUrl();
  const body = document.getElementById("productModalBody");
  const empty = document.getElementById("productModalEmpty");
  const loader = document.getElementById("productSearchLoader");

  if (!searchUrl || !body) {
    return;
  }

  if (variantSearchController) {
    variantSearchController.abort();
  }
  variantSearchController = new AbortController();

  if (loader) loader.classList.add("is-active");

  try {
    const response = await fetch(
      searchUrl + "?q=" + encodeURIComponent(query),
      {
        headers: { "X-Requested-With": "XMLHttpRequest" },
        signal: variantSearchController.signal,
      },
    );
    const data = await response.json();

    body.querySelectorAll(".product-option-grid").forEach((el) => el.remove());

    if (!data.ok || !data.results.length) {
      if (empty) empty.hidden = false;
      return;
    }

    if (empty) empty.hidden = true;

    const grid = document.createElement("div");
    grid.className = "product-option-grid";

    data.results.forEach(function (variant) {
      grid.appendChild(renderVariantCard(variant));
    });

    body.appendChild(grid);
  } catch (error) {
    if (error.name === "AbortError") return;
    console.error("Error buscando productos:", error);
  } finally {
    if (loader) loader.classList.remove("is-active");
  }
}

function openProductModal() {
  const modal = document.getElementById("productModal");
  const searchInput = document.getElementById("productSearchInput");

  if (!modal) {
    return;
  }

  modal.classList.add("is-open");

  if (searchInput) {
    searchInput.value = "";
  }

  runVariantSearch("");

  setTimeout(function () {
    if (searchInput) {
      searchInput.focus();
    }
  }, 80);
}

function closeProductModal() {
  const modal = document.getElementById("productModal");

  if (!modal) {
    return;
  }

  modal.classList.remove("is-open");
}

function setSelectedProductImage(imageUrl, productName) {
  const imageBox = document.getElementById("selectedProductImageBox");

  if (!imageBox) {
    return;
  }

  imageBox.innerHTML = "";

  if (imageUrl) {
    const img = document.createElement("img");
    img.src = imageUrl;
    img.alt = productName || "Producto";
    imageBox.appendChild(img);
    return;
  }

  const icon = document.createElement("i");
  icon.className = "bi bi-box-seam";
  imageBox.appendChild(icon);
}

function selectProductVariant(button) {
  const rawStock = Number(button.dataset.stock || "0");
  const price = button.dataset.price || "";
  const variantId = button.dataset.variantId || "";
  const sku = button.dataset.sku || "";
  const productName = button.dataset.productName || sku;
  const imageUrl = button.dataset.image || "";

  const stockMode = button.dataset.stockMode || "unit";
  const baseUnit = button.dataset.baseUnit || "Pieza";
  const commercialUnit = button.dataset.commercialUnit || "Pieza";
  const conversionFactor = Number(button.dataset.conversionFactor || 1);

  if (rawStock <= 0 || button.disabled) {
    showProductToast(
      "Este producto no tiene stock disponible.",
      "error",
      "Sin stock",
    );
    return;
  }

  let stockForUser = rawStock;
  let stockLabel = rawStock + " unidades disponibles";

  if (stockMode === "bulk" && conversionFactor > 0) {
    stockForUser = cleanNumber(rawStock / conversionFactor);
    stockLabel =
      stockForUser +
      " " +
      commercialUnit +
      " disponibles (" +
      rawStock +
      " " +
      baseUnit +
      " internas)";
  } else {
    stockForUser = rawStock;
    stockLabel = rawStock + " " + baseUnit + " disponibles";
  }

  const input = document.getElementById("selectedVariantInput");
  const stockInput = document.getElementById("selectedVariantStock");
  const skuInput = document.getElementById("selectedVariantSku");
  const nameInput = document.getElementById("selectedVariantName");
  const imageInput = document.getElementById("selectedVariantImage");

  const text = document.getElementById("selectedVariantText");
  const help = document.getElementById("selectedVariantHelp");
  const priceInput = document.getElementById("unitPriceInput");

  if (input) {
    input.value = variantId;
  }

  if (stockInput) {
    stockInput.value = stockForUser;
    stockInput.dataset.rawStock = rawStock;
    stockInput.dataset.stockMode = stockMode;
    stockInput.dataset.baseUnit = baseUnit;
    stockInput.dataset.commercialUnit = commercialUnit;
    stockInput.dataset.conversionFactor = conversionFactor;
  }

  if (skuInput) {
    skuInput.value = sku;
  }

  if (nameInput) {
    nameInput.value = productName;
  }

  if (imageInput) {
    imageInput.value = imageUrl;
  }

  if (text) {
    text.textContent = sku;
  }

  if (help) {
    help.textContent = productName + " · " + stockLabel;
  }

  if (priceInput && price) {
    priceInput.value = Number(price).toFixed(2);
  }

  const selectedBox = document.getElementById("selectedProductBox");
  const selectedName = document.getElementById("selectedProductName");
  const selectedSku = document.getElementById("selectedProductSku");
  const selectedStock = document.getElementById("selectedProductStock");
  const selectedPrice = document.getElementById("selectedProductPrice");

  if (selectedBox) {
    selectedBox.hidden = false;
  }

  setSelectedProductImage(imageUrl, productName);

  if (selectedName) {
    selectedName.textContent = productName;
  }

  if (selectedSku) {
    selectedSku.textContent = sku;
  }

  if (selectedStock) {
    selectedStock.textContent = "Disponible: " + stockLabel;
  }

  if (selectedPrice) {
    selectedPrice.textContent = "$" + Number(price || 0).toFixed(2);
  }

  closeProductModal();

  const quantityInput = document.getElementById("orderQuantityInput");

  if (quantityInput) {
    quantityInput.focus();
    quantityInput.select();
  }

  validateSelectedQuantity();
  calculateLiveSubtotal();

  showProductToast(
    sku + " seleccionado correctamente.",
    "success",
    "Producto seleccionado",
  );
}

function validateSelectedQuantity() {
  const quantityInput = document.getElementById("orderQuantityInput");
  const help = document.getElementById("quantityStockHelp");
  const submitBtn = document.getElementById("addProductSubmitBtn");

  if (!quantityInput || !help || !submitBtn) {
    return;
  }

  const quantity = Number(quantityInput.value || "0");
  const stockInfo = getSelectedStockInfo();
  const stock = stockInfo.stock;

  help.classList.remove("success", "warning", "danger");

  if (stock <= 0) {
    help.textContent = "Selecciona un producto para validar disponibilidad.";
    submitBtn.disabled = false;
    return;
  }

  if (quantity <= 0) {
    help.textContent = "La cantidad debe ser mayor a cero.";
    help.classList.add("danger");
    submitBtn.disabled = true;
    return;
  }

  if (quantity > stock) {
    help.textContent =
      "No hay stock suficiente. Disponible: " +
      stock +
      " " +
      (stockInfo.stockMode === "bulk"
        ? stockInfo.commercialUnit
        : stockInfo.baseUnit) +
      ".";
    help.classList.add("danger");
    submitBtn.disabled = true;
    return;
  }

  if (quantity === stock) {
    help.textContent =
      "Usarás todo el stock disponible: " +
      stock +
      " " +
      (stockInfo.stockMode === "bulk"
        ? stockInfo.commercialUnit
        : stockInfo.baseUnit) +
      ".";
    help.classList.add("warning");
    submitBtn.disabled = false;
    return;
  }

  if (stockInfo.stockMode === "bulk") {
    const internalQuantity = cleanNumber(quantity * stockInfo.conversionFactor);

    help.textContent =
      "Cantidad válida: " +
      quantity +
      " " +
      stockInfo.commercialUnit +
      " = " +
      internalQuantity +
      " " +
      stockInfo.baseUnit +
      " internas.";
  } else {
    help.textContent =
      "Disponible: " + stock + " " + stockInfo.baseUnit + ". Cantidad válida.";
  }

  help.classList.add("success");
  submitBtn.disabled = false;
}

function calculateLiveSubtotal() {
  const quantityInput = document.getElementById("orderQuantityInput");
  const priceInput = document.getElementById("unitPriceInput");
  const subtotalAmount = document.getElementById("liveSubtotalAmount");
  const subtotalHelp = document.getElementById("liveSubtotalHelp");

  if (!quantityInput || !priceInput || !subtotalAmount || !subtotalHelp) {
    return;
  }

  const quantity = Number(quantityInput.value || "0");
  const price = Number(priceInput.value || "0");
  const stockInfo = getSelectedStockInfo();

  if (quantity <= 0 || price <= 0) {
    subtotalAmount.textContent = "$0.00";
    subtotalHelp.textContent = "Selecciona producto y cantidad para calcular.";
    return;
  }

  const subtotal = quantity * price;
  const unitLabel =
    stockInfo.stockMode === "bulk"
      ? stockInfo.commercialUnit
      : stockInfo.baseUnit;

  subtotalAmount.textContent = "$" + subtotal.toFixed(2);
  subtotalHelp.textContent =
    quantity +
    " " +
    unitLabel +
    " × $" +
    price.toFixed(2) +
    " = $" +
    subtotal.toFixed(2);
}

// ------------------------------------------------------------
// Widget de chat: selector rápido de producto (misma búsqueda)
// ------------------------------------------------------------

function getChatVariantSearchUrl() {
  const modal = document.getElementById("productModal");
  return modal ? modal.dataset.searchUrl : "";
}

function renderChatVariantCard(variant) {
  const outOfStock = variant.available_internal <= 0;
  const stockBadge = outOfStock
    ? variant.unlocated_internal > 0
      ? `<span class="wms-chat-product-stock warning">${variant.unlocated_display} sin ubicación</span>`
      : `<span class="wms-chat-product-stock danger">Sin stock</span>`
    : `<span class="wms-chat-product-stock">${variant.available_display} ${variant.is_bulk ? variant.commercial_unit + " disp." : "disp."}</span>`;

  const volumeChip = variant.is_bulk
    ? `<div class="wms-chat-volume-chip"><i class="bi bi-truck"></i> 1 ${variant.commercial_unit} = ${variant.conversion_factor} ${variant.base_unit}</div>`
    : `<div class="wms-chat-unit-chip"><i class="bi bi-box-seam"></i> Producto unitario</div>`;

  const wholesale = variant.wholesale_price
    ? `<span>Mayoreo $${Number(variant.wholesale_price).toFixed(2)}</span>`
    : "";

  const image = variant.image_url
    ? `<img src="${variant.image_url}" alt="${variant.product_name}">`
    : `<div class="wms-chat-product-img-fallback"><i class="bi bi-box-seam"></i></div>`;

  const button = document.createElement("button");
  button.type = "button";
  button.className =
    "wms-chat-product-option" + (outOfStock ? " out-of-stock" : "");
  button.disabled = outOfStock;
  button.dataset.variantId = variant.id;
  button.dataset.label = variant.sku + " · " + variant.product_name;
  button.dataset.price = variant.sale_price;

  button.innerHTML = `
    <div class="wms-chat-product-img">${image}</div>
    <div class="wms-chat-product-info">
      <div class="wms-chat-product-top">
        <div><strong>${variant.sku}</strong><h4>${variant.product_name}</h4></div>
        ${stockBadge}
      </div>
      <div class="wms-chat-product-tags">
        <span>Talla: ${variant.talla || "-"}</span>
        <span>Color: ${variant.color || "-"}</span>
        <span>Corte: ${variant.corte || "-"}</span>
      </div>
      ${volumeChip}
      <div class="wms-chat-product-bottom">
        <small>${variant.category || "Sin categoría"}</small>
        <div class="wms-chat-product-prices">${wholesale}<b>$${Number(variant.sale_price).toFixed(2)}</b></div>
      </div>
    </div>
  `;

  button.addEventListener("click", function () {
    selectChatProductVariant(button);
  });

  return button;
}

async function runChatVariantSearch(query) {
  const searchUrl = getChatVariantSearchUrl();
  const grid = document.getElementById("wmsChatProductGrid");
  const empty = document.getElementById("wmsChatProductPickerEmpty");
  const countLabel = document.getElementById("wmsChatProductCount");

  if (!searchUrl || !grid) {
    return;
  }

  if (variantChatSearchController) {
    variantChatSearchController.abort();
  }
  variantChatSearchController = new AbortController();

  try {
    const response = await fetch(
      searchUrl + "?q=" + encodeURIComponent(query),
      {
        headers: { "X-Requested-With": "XMLHttpRequest" },
        signal: variantChatSearchController.signal,
      },
    );
    const data = await response.json();

    grid.innerHTML = "";

    if (!data.ok || !data.results.length) {
      if (empty) empty.hidden = false;
      if (countLabel) countLabel.textContent = "Sin resultados";
      return;
    }

    if (empty) empty.hidden = true;
    if (countLabel)
      countLabel.textContent = data.results.length + " resultados";

    data.results.forEach(function (variant) {
      grid.appendChild(renderChatVariantCard(variant));
    });
  } catch (error) {
    if (error.name === "AbortError") return;
    console.error("Error buscando productos (chat):", error);
  }
}

function selectChatProductVariant(button) {
  const variantId = button.dataset.variantId || "";
  const label = button.dataset.label || "";
  const price = button.dataset.price || "";

  const input = document.getElementById("wmsChatSelectedVariantInput");
  const text = document.getElementById("wmsChatSelectedVariantText");
  const priceInput = document.getElementById("wmsChatQuickPrice");

  if (input) input.value = variantId;
  if (text) text.textContent = label;
  if (priceInput && price) priceInput.value = Number(price).toFixed(2);

  closeWmsChatProductPicker();
}

function openWmsChatProductPicker() {
  const modal = document.getElementById("wmsChatProductPickerModal");
  const searchInput = document.getElementById("wmsChatProductSearchInput");

  if (!modal) return;

  modal.hidden = false;

  if (searchInput) {
    searchInput.value = "";
  }

  runChatVariantSearch("");

  setTimeout(function () {
    if (searchInput) searchInput.focus();
  }, 80);
}

function closeWmsChatProductPicker() {
  const modal = document.getElementById("wmsChatProductPickerModal");
  if (modal) modal.hidden = true;
}// ------------------------------------------------------------
// AJAX: agregar producto sin recargar la página completa
// ------------------------------------------------------------

function applyOrderUpdatePayload(data) {
  const kpisGrid = document.getElementById("orderKpisGrid");
  const nextStepContainer = document.getElementById("nextStepContainer");
  const productosList = document.getElementById("productosPedidoList");
  const financeDynamic = document.getElementById("financePanelDynamic");
  const validationsBlock = document.getElementById("sidebarValidationsBlock");
  const itemsCount = document.getElementById("sidebarOrderItemsCount");
  const markReadyModalContainer = document.getElementById(
    "markReadyModalContainer",
  );

  if (kpisGrid && data.kpis_html) kpisGrid.innerHTML = data.kpis_html;
  if (nextStepContainer && data.next_step_html)
    nextStepContainer.innerHTML = data.next_step_html;
  if (productosList && data.productos_html)
    productosList.innerHTML = data.productos_html;
  if (financeDynamic && data.finance_html)
    financeDynamic.innerHTML = data.finance_html;
  if (validationsBlock && data.validations_html)
    validationsBlock.innerHTML = data.validations_html;
  if (itemsCount && typeof data.order_items_count !== "undefined")
    itemsCount.textContent = data.order_items_count;
  if (markReadyModalContainer && data.mark_ready_modal_html)
    markReadyModalContainer.innerHTML = data.mark_ready_modal_html;
}

function resetMainAddProductForm() {
  const form = document.getElementById("addProductForm");
  if (!form) return;

  const variantInput = document.getElementById("selectedVariantInput");
  const stockInput = document.getElementById("selectedVariantStock");
  const skuInput = document.getElementById("selectedVariantSku");
  const nameInput = document.getElementById("selectedVariantName");
  const imageInput = document.getElementById("selectedVariantImage");
  const text = document.getElementById("selectedVariantText");
  const help = document.getElementById("selectedVariantHelp");
  const priceInput = document.getElementById("unitPriceInput");
  const quantityInput = document.getElementById("orderQuantityInput");
  const selectedBox = document.getElementById("selectedProductBox");

  if (variantInput) variantInput.value = "";
  if (stockInput) stockInput.value = "0";
  if (skuInput) skuInput.value = "";
  if (nameInput) nameInput.value = "";
  if (imageInput) imageInput.value = "";
  if (text) text.textContent = "Seleccionar producto";
  if (help) help.textContent = "Buscar por SKU, nombre, talla, color o categoría";
  if (priceInput) priceInput.value = "";
  if (quantityInput) quantityInput.value = "1";
  if (selectedBox) selectedBox.hidden = true;

  calculateLiveSubtotal();
}

function resetChatAddProductForm() {
  const input = document.getElementById("wmsChatSelectedVariantInput");
  const text = document.getElementById("wmsChatSelectedVariantText");
  const priceInput = document.getElementById("wmsChatQuickPrice");
  const form = document.getElementById("wmsOrderChatProductForm");

  if (input) input.value = "";
  if (text) text.textContent = "Seleccionar producto";
  if (priceInput) priceInput.value = "";

  if (form) {
    const quantityInput = form.querySelector('input[name="quantity"]');
    if (quantityInput) quantityInput.value = "1";
  }
}

async function submitAddProductFormAjax(form, options) {
  const settings = options || {};
  const submitBtn = form.querySelector('button[type="submit"]');

  if (submitBtn) submitBtn.disabled = true;

  try {
    const formData = new FormData(form);
    const response = await fetch(form.getAttribute("action"), {
      method: "POST",
      body: formData,
      headers: { "X-Requested-With": "XMLHttpRequest" },
    });

    let data;
    try {
      data = await response.json();
    } catch (parseError) {
      // Respuesta no-JSON inesperada (ej. página de login por sesión
      // caducada): mejor recargar la página normal que dejar al
      // usuario en un estado incierto.
      window.location.reload();
      return;
    }

    if (data.reload) {
      window.location.reload();
      return;
    }

    if (!data.ok) {
      showProductToast(
        data.error || "No se pudo agregar el producto.",
        "error",
        "Error",
      );
      return;
    }

    applyOrderUpdatePayload(data);

    if (settings.resetForm) {
      settings.resetForm();
    }

    showProductToast(
      data.message || "Producto agregado.",
      "success",
      "Producto agregado",
    );
  } catch (networkError) {
    console.error("Error de red agregando producto:", networkError);
    // Sin conexión o falla real de red: recurrimos al comportamiento
    // clásico (submit normal con recarga) para no perder el intento.
    showProductToast(
      "No se pudo conectar. Reintentando con recarga normal…",
      "warning",
      "Sin conexión",
    );
    form.submit();
  } finally {
    if (submitBtn) submitBtn.disabled = false;
  }
}

// ------------------------------------------------------------
// Inicialización
// ------------------------------------------------------------

function initProductTools() {
  const form = document.getElementById("addProductForm");
  const quantityInput = document.getElementById("orderQuantityInput");
  const priceInput = document.getElementById("unitPriceInput");
  const searchInput = document.getElementById("productSearchInput");

  if (quantityInput) {
    quantityInput.addEventListener("input", function () {
      validateSelectedQuantity();
      calculateLiveSubtotal();
    });
  }

  if (priceInput) {
    priceInput.addEventListener("input", calculateLiveSubtotal);
  }

  calculateLiveSubtotal();

  if (searchInput) {
    searchInput.addEventListener("input", function () {
      window.clearTimeout(variantSearchDebounce);
      variantSearchDebounce = window.setTimeout(function () {
        runVariantSearch(searchInput.value.trim());
      }, 300);
    });
  }

  const openChatPickerBtn = document.getElementById("wmsOpenChatProductPicker");
  const closeChatPickerBtn = document.getElementById(
    "wmsCloseChatProductPicker",
  );
  const chatSearchInput = document.getElementById("wmsChatProductSearchInput");
  const chatBackdrop = document.querySelector(".wms-chat-product-backdrop");

  if (openChatPickerBtn) {
    openChatPickerBtn.addEventListener("click", openWmsChatProductPicker);
  }

  if (closeChatPickerBtn) {
    closeChatPickerBtn.addEventListener("click", closeWmsChatProductPicker);
  }

  if (chatBackdrop) {
    chatBackdrop.addEventListener("click", closeWmsChatProductPicker);
  }

  if (chatSearchInput) {
    chatSearchInput.addEventListener("input", function () {
      window.clearTimeout(variantChatSearchDebounce);
      variantChatSearchDebounce = window.setTimeout(function () {
        runChatVariantSearch(chatSearchInput.value.trim());
      }, 300);
    });
  }

  if (form) {
    form.addEventListener("submit", function (event) {
      event.preventDefault();

      const variantInput = document.getElementById("selectedVariantInput");
      const quantityInput = document.getElementById("orderQuantityInput");

      const variantId = variantInput ? variantInput.value : "";
      const quantity = Number(quantityInput ? quantityInput.value : "0");
      const stockInfo = getSelectedStockInfo();
      const stock = stockInfo.stock;

      if (!variantId) {
        showProductToast(
          "Selecciona un producto antes de agregarlo.",
          "warning",
          "Producto requerido",
        );
        return;
      }

      if (stock <= 0) {
        showProductToast(
          "El producto seleccionado no tiene stock disponible.",
          "error",
          "Sin stock",
        );
        return;
      }

      if (quantity <= 0) {
        showProductToast(
          "La cantidad debe ser mayor a cero.",
          "error",
          "Cantidad inválida",
        );
        return;
      }

      if (quantity > stock) {
        showProductToast(
          "No hay stock suficiente. Disponible: " +
            stock +
            " " +
            (stockInfo.stockMode === "bulk"
              ? stockInfo.commercialUnit
              : stockInfo.baseUnit) +
            ".",
          "error",
          "Stock insuficiente",
        );
        return;
      }

      submitAddProductFormAjax(form, { resetForm: resetMainAddProductForm });
    });
  }

  const chatForm = document.getElementById("wmsOrderChatProductForm");

  if (chatForm) {
    chatForm.addEventListener("submit", function (event) {
      event.preventDefault();

      const chatVariantInput = document.getElementById(
        "wmsChatSelectedVariantInput",
      );
      const chatVariantId = chatVariantInput ? chatVariantInput.value : "";

      if (!chatVariantId) {
        showProductToast(
          "Selecciona un producto antes de agregarlo.",
          "warning",
          "Producto requerido",
        );
        return;
      }

      submitAddProductFormAjax(chatForm, { resetForm: resetChatAddProductForm });
    });
  }

}

function openMarkReadyModal() {
  const modal = document.getElementById("markReadyModal");
  if (modal) modal.hidden = false;
}

function closeMarkReadyModal() {
  const modal = document.getElementById("markReadyModal");
  if (modal) modal.hidden = true;
}

function openReturnToCaptureModal() {
  const modal = document.getElementById("returnToCaptureModal");
  if (modal) modal.hidden = false;
}

function closeReturnToCaptureModal() {
  const modal = document.getElementById("returnToCaptureModal");
  if (modal) modal.hidden = true;
}
