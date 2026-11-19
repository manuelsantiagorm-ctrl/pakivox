// Dashboard CEO (templates/dashboard/ceo.html) · codigo general del tablero
// Codigo movido desde la plantilla, en el mismo orden en que estaba.
// Los datos de las graficas llegan con json_script (ids "ceo-data-<nombre>").
const ceoData = (name) => {
  const node = document.getElementById("ceo-data-" + name);
  if (!node) return [];
  try {
    let value = JSON.parse(node.textContent);
    // Algunas vistas mandan el dato ya convertido a JSON (texto): se vuelve a leer.
    if (typeof value === "string") { try { value = JSON.parse(value); } catch (error) { /* texto normal */ } }
    return value ?? [];
  } catch (error) { return []; }
};
const ceoNumber = (name) => { const value = Number(ceoData(name)); return Number.isFinite(value) ? value : 0; };

/* ===== origen: <script id="sin-id"> ===== */
        document.addEventListener("DOMContentLoaded", function () {
          const funnelCtx = document.getElementById("funnelChart");
          const ordersCtx = document.getElementById("ordersChart");
          const warehouseCtx = document.getElementById("warehouseChart");
          const inventoryCtx = document.getElementById("inventoryChart");
          const purchaseOrdersCtx = document.getElementById("purchaseOrdersChart");
          const supplierPaymentsCtx = document.getElementById("supplierPaymentsChart");
          const supplierPaymentStatusCtx = document.getElementById("supplierPaymentStatusChart");

          if (funnelCtx) {
            new Chart(funnelCtx, {
              type: "bar",
              data: {
                labels: ceoData("funnel_labels"),
                datasets: [
                  {
                    label: "Oportunidades",
                    data: ceoData("funnel_counts"),
                    borderRadius: 12,
                    backgroundColor: "rgba(56, 189, 248, .75)",
                    borderColor: "rgba(56, 189, 248, 1)",
                    borderWidth: 1
                  }
                ]
              },
              options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                  legend: { display: false }
                },
                scales: {
                  x: {
                    ticks: { color: "#cbd5e1" },
                    grid: { color: "rgba(255,255,255,.05)" }
                  },
                  y: {
                    beginAtZero: true,
                    ticks: { color: "#cbd5e1" },
                    grid: { color: "rgba(255,255,255,.05)" }
                  }
                }
              }
            });
          }

          if (ordersCtx) {
            new Chart(ordersCtx, {
              type: "doughnut",
              data: {
                labels: ceoData("orders_status_labels"),
                datasets: [
                  {
                    data: ceoData("orders_status_counts"),
                    backgroundColor: [
                      "rgba(148, 163, 184, .8)",
                      "rgba(245, 158, 11, .8)",
                      "rgba(34, 197, 94, .8)",
                      "rgba(56, 189, 248, .8)",
                      "rgba(168, 85, 247, .8)",
                      "rgba(6, 182, 212, .8)",
                      "rgba(16, 185, 129, .8)",
                      "rgba(239, 68, 68, .8)"
                    ],
                    borderColor: "#111827",
                    borderWidth: 2
                  }
                ]
              },
              options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                  legend: {
                    position: "bottom",
                    labels: {
                      color: "#cbd5e1"
                    }
                  }
                }
              }
            });
          }

          if (warehouseCtx) {
            new Chart(warehouseCtx, {
              type: "bar",
              data: {
                labels: ceoData("warehouse_status_labels"),
                datasets: [
                  {
                    label: "Órdenes",
                    data: ceoData("warehouse_status_counts"),
                    borderRadius: 12,
                    backgroundColor: "rgba(34, 197, 94, .72)",
                    borderColor: "rgba(34, 197, 94, 1)",
                    borderWidth: 1
                  }
                ]
              },
              options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                  legend: { display: false }
                },
                scales: {
                  x: {
                    ticks: { color: "#cbd5e1" },
                    grid: { color: "rgba(255,255,255,.05)" }
                  },
                  y: {
                    beginAtZero: true,
                    ticks: { color: "#cbd5e1" },
                    grid: { color: "rgba(255,255,255,.05)" }
                  }
                }
              }
            });
          }

          if (inventoryCtx) {
            new Chart(inventoryCtx, {
              type: "doughnut",
              data: {
                labels: ceoData("inventory_status_labels"),
                datasets: [
                  {
                    data: ceoData("inventory_status_counts"),
                    backgroundColor: [
                      "rgba(56, 189, 248, .8)",
                      "rgba(245, 158, 11, .8)",
                      "rgba(168, 85, 247, .8)",
                      "rgba(34, 197, 94, .8)"
                    ],
                    borderColor: "#111827",
                    borderWidth: 2
                  }
                ]
              },
              options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                  legend: {
                    position: "bottom",
                    labels: {
                      color: "#cbd5e1"
                    }
                  }
                }
              }
            });
          }

          if (purchaseOrdersCtx) {
            new Chart(purchaseOrdersCtx, {
              type: "bar",
              data: {
                labels: ceoData("purchase_status_labels"),
                datasets: [
                  {
                    label: "Órdenes de compra",
                    data: ceoData("purchase_status_counts"),
                    borderRadius: 12,
                    backgroundColor: "rgba(168, 85, 247, .72)",
                    borderColor: "rgba(168, 85, 247, 1)",
                    borderWidth: 1
                  }
                ]
              },
              options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                  legend: { display: false }
                },
                scales: {
                  x: {
                    ticks: { color: "#cbd5e1" },
                    grid: { color: "rgba(255,255,255,.05)" }
                  },
                  y: {
                    beginAtZero: true,
                    ticks: { color: "#cbd5e1" },
                    grid: { color: "rgba(255,255,255,.05)" }
                  }
                }
              }
            });
          }

          if (supplierPaymentsCtx) {
            new Chart(supplierPaymentsCtx, {
              type: "doughnut",
              data: {
                labels: ["Pagado aprobado", "Pendiente aprobación", "Saldo proveedor"],
                datasets: [
                  {
                    data: [
                      ceoNumber("supplier_paid_total"),
                      ceoNumber("supplier_pending_approval_total"),
                      ceoNumber("supplier_balance_due_total")
                    ],
                    backgroundColor: [
                      "rgba(34, 197, 94, .8)",
                      "rgba(245, 158, 11, .8)",
                      "rgba(239, 68, 68, .8)"
                    ],
                    borderColor: "#111827",
                    borderWidth: 2
                  }
                ]
              },
              options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                  legend: {
                    position: "bottom",
                    labels: {
                      color: "#cbd5e1"
                    }
                  }
                }
              }
            });
          }

          if (supplierPaymentStatusCtx) {
            new Chart(supplierPaymentStatusCtx, {
              type: "doughnut",
              data: {
                labels: ceoData("supplier_payment_status_labels"),
                datasets: [
                  {
                    data: ceoData("supplier_payment_status_counts"),
                    backgroundColor: [
                      "rgba(245, 158, 11, .8)",
                      "rgba(34, 197, 94, .8)",
                      "rgba(239, 68, 68, .8)",
                      "rgba(148, 163, 184, .8)"
                    ],
                    borderColor: "#111827",
                    borderWidth: 2
                  }
                ]
              },
              options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                  legend: {
                    position: "bottom",
                    labels: {
                      color: "#cbd5e1"
                    }
                  }
                }
              }
            });
          }

        });

  document.addEventListener("DOMContentLoaded", function () {
    const form = document.getElementById("ceoAiForm");

    if (!form) {
      return;
    }

    const modeInput = document.getElementById("ceoAiMode");
    const questionInput = document.getElementById("ceoAiQuestion");
    const submitButton = document.getElementById("ceoAiSubmit");
    const quotaBox = document.getElementById("ceoAiQuota");
    const quotaStatus = document.getElementById("ceoAiQuotaStatus");
    const quotaRemaining = document.getElementById("ceoAiQuotaRemaining");
    const quotaLimit = document.getElementById("ceoAiQuotaLimit");
    const quotaUsed = document.getElementById("ceoAiQuotaUsed");
    const quotaProgress = document.getElementById("ceoAiQuotaProgress");
    const quotaBar = document.getElementById("ceoAiQuotaBar");

    let aiQuotaAllowed = (
    !quotaBox
    || quotaBox.dataset.allowed !== "false"
  );
    const loadingBox = document.getElementById("ceoAiLoading");
    const errorBox = document.getElementById("ceoAiError");
    const resultsBox = document.getElementById("ceoAiResults");
    const dateFromInput = document.getElementById("ceoAiDateFrom");
    const dateToInput = document.getElementById("ceoAiDateTo");
    const clearDatesButton = document.getElementById("ceoAiClearDates");
    const resolvedPeriodBox = document.getElementById("ceoAiResolvedPeriod");
    const warehouseScopeBox = document.getElementById("ceoAiWarehouseScope");
    const fallbackBox = document.getElementById("ceoAiFallback");
    const fallbackTitle = document.getElementById("ceoAiFallbackTitle");
    const fallbackOptions = document.getElementById("ceoAiFallbackOptions");
    const exportExcelButton = document.getElementById(
      "ceoAiExportExcel"
    );
    const exportExcelHint = document.getElementById(
      "ceoAiExportHint"
    );

    let ceoAiChartInstance = null;
    let ceoAiExportToken = null;
    let ceoAiDeepChartInstances = [];

    // =========================================================
    // BOTONES DE MODO
    // =========================================================

    const modeButtons = form.querySelectorAll(".ceo-ai-mode");

    modeButtons.forEach(function (button) {
      button.addEventListener("click", function () {
        modeButtons.forEach(function (item) {
          item.classList.remove("is-active");
        });

        button.classList.add("is-active");

        if (modeInput) {
          modeInput.value = button.dataset.mode || "full";
        }
      });
    });

    if (clearDatesButton) {
      clearDatesButton.addEventListener("click", function () {
        if (dateFromInput) {
          dateFromInput.value = "";
        }

        if (dateToInput) {
          dateToInput.value = "";
        }
      });
    }

    // =========================================================
    // HELPERS GENERALES
    // =========================================================

      function updateAiQuota(quota) {
    if (!quotaBox || !quota) {
      return;
    }

    const hasLimit = (
      quota.limit !== null
      && quota.limit !== undefined
      && quota.limit !== ""
    );

    aiQuotaAllowed = quota.allowed !== false;

    quotaBox.dataset.allowed = aiQuotaAllowed
      ? "true"
      : "false";

    quotaBox.classList.remove(
      "is-warning",
      "is-exhausted"
    );

    if (!hasLimit) {
      if (quotaStatus) {
        quotaStatus.textContent = "Consultas ilimitadas";
      }

      if (submitButton) {
        submitButton.disabled = false;
      }

      return;
    }

    const limit = Math.max(
      0,
      Number(quota.limit) || 0
    );

    const used = Math.max(
      0,
      Number(quota.used) || 0
    );

    const remaining = Math.max(
      0,
      Number(quota.remaining) || 0
    );

    const availablePercentage = limit > 0
      ? Math.min(100, Math.max(0, (remaining / limit) * 100))
      : 0;

    quotaBox.dataset.used = String(used);
    quotaBox.dataset.limit = String(limit);
    quotaBox.dataset.remaining = String(remaining);

    if (quotaRemaining) {
      quotaRemaining.textContent = String(remaining);
    }

    if (quotaLimit) {
      quotaLimit.textContent = String(limit);
    }

    if (quotaUsed) {
      quotaUsed.textContent = String(used);
    }

    if (quotaProgress) {
      quotaProgress.setAttribute(
        "aria-valuemax",
        String(limit)
      );

      quotaProgress.setAttribute(
        "aria-valuenow",
        String(remaining)
      );
    }

    if (quotaBar) {
      quotaBar.style.width = availablePercentage + "%";
    }

    if (!aiQuotaAllowed || remaining <= 0) {
      aiQuotaAllowed = false;
      quotaBox.classList.add("is-exhausted");

      if (quotaStatus) {
        quotaStatus.textContent = "Límite agotado";
      }

      if (submitButton) {
        submitButton.disabled = true;
        submitButton.title = (
          "Agotaste las consultas de IA incluidas en tu plan."
        );
      }

      return;
    }

    if (remaining <= 5) {
      quotaBox.classList.add("is-warning");

      if (quotaStatus) {
        quotaStatus.textContent = (
          remaining === 1
            ? "Última consulta"
            : remaining + " disponibles"
        );
      }
    } else if (quotaStatus) {
      quotaStatus.textContent = (
        remaining + " disponibles"
      );
    }

    if (submitButton) {
      submitButton.disabled = false;
      submitButton.removeAttribute("title");
    }
  }

  if (quotaBox) {
    updateAiQuota({
      allowed: quotaBox.dataset.allowed === "true",
      used: quotaBox.dataset.used,
      limit: quotaBox.dataset.limit || null,
      remaining: quotaBox.dataset.remaining
    });
  }

    function setText(id, value) {
      const element = document.getElementById(id);

      if (!element) {
        return;
      }

      element.textContent = (
        value === null || value === undefined
          ? ""
          : String(value)
      );
    }

    function showError(message) {
      if (!errorBox) {
        return;
      }

      errorBox.textContent = (
        message || "Ocurrió un error inesperado."
      );

      errorBox.hidden = false;
    }

    function hideError() {
      if (!errorBox) {
        return;
      }

      errorBox.textContent = "";
      errorBox.hidden = true;
    }

    function renderList(elementId, values) {
      const list = document.getElementById(elementId);

      if (!list) {
        return;
      }

      list.replaceChildren();

      (values || []).forEach(function (value) {
        const item = document.createElement("li");
        item.textContent = value;
        list.appendChild(item);
      });
    }

    function createRiskBadge(level) {
      const normalizedLevel = (
        level || "medio"
      ).toLowerCase();

      const badge = document.createElement("span");

      badge.className = (
        "ceo-ai-risk "
        + normalizedLevel
      );

      badge.textContent = normalizedLevel;

      return badge;
    }

    function formatMoney(value) {
      if (
        value === null
        || value === undefined
        || Number.isNaN(Number(value))
      ) {
        return "-";
      }

      return new Intl.NumberFormat(
        "es-MX",
        {
          style: "currency",
          currency: "MXN",
          minimumFractionDigits: 2,
          maximumFractionDigits: 2
        }
      ).format(Number(value));
    }

    function formatPercent(value) {
      if (
        value === null
        || value === undefined
        || Number.isNaN(Number(value))
      ) {
        return "-";
      }

      return (
        Number(value).toFixed(2)
        + "%"
      );
    }

    function getCsrfToken() {
      const input = form.querySelector(
        "[name=csrfmiddlewaretoken]"
      );

      return input ? input.value : "";
    }

    async function parseJsonResponse(response) {
      const responseText = await response.text();

      let data;

      try {
        data = JSON.parse(responseText);
      } catch (parseError) {
        console.error(
          "Respuesta no JSON del servidor:",
          responseText
        );

        throw new Error(
          "El servidor devolvió una respuesta inválida. "
          + "Revisa la terminal de Django."
        );
      }

      return data;
    }

    // =========================================================
    // COPIAR TEXTO
    // =========================================================

    async function copyText(value, button) {
      const text = String(value || "").trim();

      if (!text) {
        throw new Error(
          "No existe un mensaje para copiar."
        );
      }

      const originalText = button.textContent;

      try {
        await navigator.clipboard.writeText(text);

        button.textContent = "Copiado";

        setTimeout(function () {
          button.textContent = originalText;
        }, 1500);

      } catch (error) {
        console.error(
          "No fue posible copiar con Clipboard API:",
          error
        );

        const temporaryTextarea = document.createElement(
          "textarea"
        );

        temporaryTextarea.value = text;
        temporaryTextarea.setAttribute(
          "readonly",
          ""
        );

        temporaryTextarea.style.position = "fixed";
        temporaryTextarea.style.opacity = "0";

        document.body.appendChild(
          temporaryTextarea
        );

        temporaryTextarea.select();

        const copied = document.execCommand(
          "copy"
        );

        temporaryTextarea.remove();

        if (!copied) {
          throw new Error(
            "No fue posible copiar el mensaje."
          );
        }

        button.textContent = "Copiado";

        setTimeout(function () {
          button.textContent = originalText;
        }, 1500);
      }
    }

    // =========================================================
    // CREAR BORRADOR PREMIUM
    // =========================================================

    function resolveDraftSource() {
      const currentMode = modeInput
        ? modeInput.value
        : "full";

      if (currentMode === "sales") {
        return "ceo_ai_sales";
      }

      if (currentMode === "full") {
        return "ceo_ai_full";
      }

      return "ceo_ai_churn";
    }

    async function createPremiumDraft(
      customer,
      button
    ) {
      const endpoint = form.dataset.draftEndpoint;

      if (!endpoint) {
        throw new Error(
          "No se configuró la ruta de borradores Premium."
        );
      }

      if (!customer.customer_key) {
        throw new Error(
          "La recomendación no contiene una clave de cliente válida."
        );
      }

      if (!customer.mensaje_whatsapp) {
        throw new Error(
          "La recomendación no contiene un mensaje de WhatsApp."
        );
      }

      if (button.dataset.draftCreated === "true") {
        return;
      }

      const originalText = button.textContent;

      button.disabled = true;
      button.textContent = "Creando borrador...";

      try {
        const response = await fetch(
          endpoint,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": getCsrfToken(),
              "X-Requested-With": "XMLHttpRequest"
            },
            credentials: "same-origin",
            body: JSON.stringify({
              customer_key: customer.customer_key,
              message: customer.mensaje_whatsapp,
              source: resolveDraftSource()
            })
          }
        );

        const data = await parseJsonResponse(
          response
        );

        if (!response.ok || !data.ok) {
          throw new Error(
            data.error
            || (
              "No fue posible crear el borrador. "
              + "Código HTTP: "
              + response.status
            )
          );
        }

        button.dataset.draftCreated = "true";
        button.textContent = "Borrador creado";
        button.disabled = true;

        const actionsContainer = button.parentElement;

        if (
          actionsContainer
          && data.review_url
          && !actionsContainer.querySelector(
            '[data-review-draft="true"]'
          )
        ) {
          const reviewLink = document.createElement("a");

          reviewLink.className = (
            "ceo-ai-mini-button premium"
          );

          reviewLink.dataset.reviewDraft = "true";
          reviewLink.target = "_blank";
          reviewLink.rel = "noopener noreferrer";
          reviewLink.href = data.review_url;
          reviewLink.textContent = "Revisar borrador";

          actionsContainer.appendChild(
            reviewLink
          );
        }

        hideError();

      } catch (error) {
        button.disabled = false;
        button.textContent = originalText;

        throw error;
      }
    }

    // =========================================================
    // CLIENTES EN RIESGO
    // =========================================================

    function renderCustomers(customers) {
      const container = document.getElementById(
        "ceoAiCustomers"
      );

      if (!container) {
        return;
      }

      container.replaceChildren();

      if (!customers || !customers.length) {
        const empty = document.createElement("div");

        empty.className = "ceo-ai-empty";
        empty.textContent = (
          "No se detectaron clientes en riesgo "
          + "con los criterios actuales."
        );

        container.appendChild(empty);
        return;
      }

      customers.forEach(function (customer) {
        const card = document.createElement("article");
        card.className = "ceo-ai-item";

        // Encabezado
        const header = document.createElement("div");
        header.className = "ceo-ai-item-header";

        const titleBox = document.createElement("div");

        const title = document.createElement("h4");
        title.textContent = (
          customer.customer_name
          || customer.customer_key
          || "Cliente"
        );

        const subtitle = document.createElement("small");

        const businessText = customer.business_name
          ? " · " + customer.business_name
          : "";

        const inactiveDays = (
          customer.days_inactive !== null
          && customer.days_inactive !== undefined
        )
          ? customer.days_inactive
          : 0;

        subtitle.textContent = (
          inactiveDays
          + " días sin compra"
          + businessText
        );



        header.appendChild(titleBox);
        header.appendChild(
          createRiskBadge(
            customer.nivel_riesgo
          )
        );

        // Motivo
        const reason = document.createElement("p");
        reason.textContent = (
          customer.motivo
          || "Sin motivo adicional."
        );

        // Oferta
        const offer = document.createElement("p");
        offer.textContent = (
          "Oferta: "
          + (
            customer.oferta
            || "Sin oferta sugerida."
          )
        );

        // Mensaje
        const message = document.createElement("div");
        message.className = "ceo-ai-message";
        message.textContent = (
          customer.mensaje_whatsapp
          || "Sin mensaje preparado."
        );

        // Acciones
        const actions = document.createElement("div");
        actions.className = "ceo-ai-item-actions";

        // Copiar
        const copyButton = document.createElement("button");

        copyButton.type = "button";
        copyButton.className = "ceo-ai-mini-button";
        copyButton.textContent = "Copiar mensaje";

        copyButton.addEventListener(
          "click",
          async function () {
            try {
              hideError();

              await copyText(
                customer.mensaje_whatsapp,
                copyButton
              );
            } catch (error) {
              showError(error.message);
            }
          }
        );

        actions.appendChild(copyButton);

        // Crear borrador Premium
        const premiumButton = document.createElement(
          "button"
        );

        premiumButton.type = "button";
        premiumButton.className = (
          "ceo-ai-mini-button premium"
        );

        premiumButton.textContent = (
          "Crear borrador Premium"
        );

        premiumButton.addEventListener(
          "click",
          async function () {
            try {
              hideError();

              await createPremiumDraft(
                customer,
                premiumButton
              );
            } catch (error) {
              showError(error.message);
            }
          }
        );

        actions.appendChild(
          premiumButton
        );

        // Abrir WhatsApp Web
        const cleanPhone = String(
          customer.whatsapp || ""
        ).replace(/\D/g, "");

        if (cleanPhone.length >= 10) {
          const whatsappLink = document.createElement(
            "a"
          );

          whatsappLink.className = (
            "ceo-ai-mini-button"
          );

          whatsappLink.target = "_blank";
          whatsappLink.rel = "noopener noreferrer";
          whatsappLink.textContent = "Enviar por WhatsApp";

          whatsappLink.href = (
            "https://wa.me/"
            + cleanPhone
            + "?text="
            + encodeURIComponent(
              customer.mensaje_whatsapp || ""
            )
          );

          actions.appendChild(
            whatsappLink
          );
        }

        card.appendChild(header);
        card.appendChild(reason);
        card.appendChild(offer);
        card.appendChild(message);
        card.appendChild(actions);

        container.appendChild(card);
      });
    }

    // =========================================================
    // STOCK CRÍTICO
    // =========================================================

    function renderStock(stockItems) {
      const container = document.getElementById(
        "ceoAiStock"
      );

      if (!container) {
        return;
      }

      container.replaceChildren();

      if (!stockItems || !stockItems.length) {
        const empty = document.createElement("div");

        empty.className = "ceo-ai-empty";
        empty.textContent = (
          "No se detectaron quiebres proyectados "
          + "ni stock crítico dentro del alcance."
        );

        container.appendChild(empty);
        return;
      }

      stockItems.forEach(function (stock) {
        const card = document.createElement("article");
        card.className = "ceo-ai-item";

        const header = document.createElement("div");
        header.className = "ceo-ai-item-header";

        const titleBox = document.createElement("div");

        const title = document.createElement("h4");

        title.textContent = (
          (
            stock.warehouse_name
              ? stock.warehouse_name + " · "
              : ""
          )
          + (stock.sku || "Sin SKU")
          + " · "
          + (
            stock.product_name
            || "Sin producto"
          )
        );

        const subtitle = document.createElement("small");

        let daysText = "Sin velocidad suficiente";

        if (
          stock.days_remaining !== null
          && stock.days_remaining !== undefined
        ) {
          daysText = (
            Number(
              stock.days_remaining
            ).toFixed(1)
            + " días estimados"
          );
        }

        subtitle.textContent = (
          "Disponible: "
          + (
            stock.available_internal ?? 0
          )
          + " piezas internas · "
          + daysText
        );

        titleBox.appendChild(title);
        titleBox.appendChild(subtitle);

        const forecast = document.createElement("small");
        forecast.className = "ceo-ai-stock-forecast";

        forecast.textContent = (
          "Pronóstico 15 días: demanda "
          + Number(
            stock.expected_demand_internal_15d ?? 0
          ).toFixed(1)
          + " · compras esperadas "
          + Number(
            stock.incoming_internal_15d ?? 0
          ).toFixed(0)
          + " · saldo proyectado "
          + Number(
            stock.projected_available_internal_15d ?? 0
          ).toFixed(1)
          + " piezas"
        );

        if (
          Number(
            stock.overdue_purchase_internal ?? 0
          ) > 0
        ) {
          forecast.textContent += (
            " · compra vencida pendiente "
            + Number(
              stock.overdue_purchase_internal
            ).toFixed(0)
          );
        }

        titleBox.appendChild(forecast);

        header.appendChild(titleBox);
        header.appendChild(
          createRiskBadge(
            stock.nivel_riesgo
          )
        );

        const reason = document.createElement("p");

        reason.textContent = (
          stock.motivo
          || "Sin observaciones adicionales."
        );

        const action = document.createElement("div");
        action.className = "ceo-ai-highlight";

        const actionTitle = document.createElement(
          "strong"
        );

        actionTitle.textContent = (
          "Recomendación: ordenar "
          + (
            stock.reorder_qty_internal ?? 0
          )
          + " piezas internas"
        );

        const actionText = document.createElement("p");

        actionText.textContent = (
          stock.accion
          || "No se requiere acción inmediata."
        );

        action.appendChild(actionTitle);
        action.appendChild(actionText);

        card.appendChild(header);
        card.appendChild(reason);
        card.appendChild(action);

        container.appendChild(card);
      });
    }

    // =========================================================
    // GRÁFICA DINÁMICA DE GEMINI
    // =========================================================

    function renderChart(graph) {
      const canvas = document.getElementById(
        "ceoAiChart"
      );

      const empty = document.getElementById(
        "ceoAiChartEmpty"
      );

      if (!canvas || !empty) {
        return;
      }

      if (ceoAiChartInstance) {
        ceoAiChartInstance.destroy();
        ceoAiChartInstance = null;
      }

      if (
        !graph
        || !Array.isArray(graph.etiquetas)
        || !Array.isArray(graph.valores)
        || !graph.etiquetas.length
        || graph.etiquetas.length
          !== graph.valores.length
      ) {
        canvas.hidden = true;
        empty.hidden = false;
        return;
      }

      canvas.hidden = false;
      empty.hidden = true;

      const validTypes = [
        "bar",
        "line",
        "pie",
        "doughnut"
      ];

      const chartType = validTypes.includes(
        graph.tipo_grafica
      )
        ? graph.tipo_grafica
        : "bar";

      setText(
        "ceoAiChartTitle",
        graph.titulo || "Comparación ejecutiva"
      );

      ceoAiChartInstance = new Chart(
        canvas,
        {
          type: chartType,
          data: {
            labels: graph.etiquetas,
            datasets: [
              {
                label: (
                  graph.titulo_eje
                  || "Valor"
                ),
                data: graph.valores,
                borderWidth: 2,
                borderRadius: 8,
                backgroundColor: [
                  "rgba(139, 92, 246, .68)",
                  "rgba(56, 189, 248, .68)",
                  "rgba(34, 197, 94, .68)",
                  "rgba(245, 158, 11, .68)",
                  "rgba(239, 68, 68, .68)",
                  "rgba(6, 182, 212, .68)",
                  "rgba(236, 72, 153, .68)",
                  "rgba(99, 102, 241, .68)",
                  "rgba(20, 184, 166, .68)",
                  "rgba(244, 63, 94, .68)",
                  "rgba(132, 204, 22, .68)",
                  "rgba(251, 146, 60, .68)"
                ],
                borderColor: (
                  "rgba(196, 181, 253, 1)"
                )
              }
            ]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: {
                labels: {
                  color: "#cbd5e1"
                }
              }
            },
            scales: (
              chartType === "pie"
              || chartType === "doughnut"
            )
              ? {}
              : {
                  x: {
                    ticks: {
                      color: "#cbd5e1"
                    },
                    grid: {
                      color: (
                        "rgba(255,255,255,.05)"
                      )
                    }
                  },
                  y: {
                    beginAtZero: true,
                    ticks: {
                      color: "#cbd5e1"
                    },
                    grid: {
                      color: (
                        "rgba(255,255,255,.05)"
                      )
                    }
                  }
                }
          }
        }
      );
    }


    // =========================================================
    // PASO 12: ANÁLISIS EJECUTIVO AMPLIADO
    // =========================================================

    function clearDeepCharts() {
      ceoAiDeepChartInstances.forEach(
        function (chart) {
          try {
            chart.destroy();
          } catch (error) {
            console.warn(
              "No se pudo destruir una gráfica ampliada.",
              error
            );
          }
        }
      );

      ceoAiDeepChartInstances = [];
    }

    function createDeepList(
      elementId,
      values
    ) {
      const element = document.getElementById(
        elementId
      );

      if (!element) {
        return;
      }

      element.replaceChildren();

      (Array.isArray(values) ? values : []).forEach(
        function (value) {
          const item = document.createElement("li");
          item.textContent = String(value || "");
          element.appendChild(item);
        }
      );
    }

    function renderDeepAnalysis(deep) {
      const card = document.getElementById(
        "ceoAiDeepAnalysisCard"
      );

      if (!card) {
        return;
      }

      clearDeepCharts();

      const visible = Boolean(
        deep
        && typeof deep === "object"
      );

      card.hidden = !visible;
      card.style.display = visible ? "" : "none";

      if (!visible) {
        return;
      }

      setText(
        "ceoAiDeepTitle",
        deep.title || "Análisis ejecutivo ampliado"
      );

      setText(
        "ceoAiDeepSubtitle",
        deep.subtitle || ""
      );

      const kpiContainer = document.getElementById(
        "ceoAiDeepKpis"
      );

      if (kpiContainer) {
        kpiContainer.replaceChildren();

        (
          Array.isArray(deep.kpis)
            ? deep.kpis
            : []
        ).forEach(function (kpi) {
          const box = document.createElement("article");
          box.className = (
            "ceo-ai-deep-kpi "
            + (
              kpi.tone
              || "info"
            )
          );

          const label = document.createElement("span");
          label.textContent = kpi.label || "Indicador";

          const value = document.createElement("strong");
          value.textContent = (
            kpi.value === null
            || kpi.value === undefined
          )
            ? "-"
            : String(kpi.value);

          const detail = document.createElement("small");
          detail.textContent = kpi.detail || "";

          box.appendChild(label);
          box.appendChild(value);
          box.appendChild(detail);
          kpiContainer.appendChild(box);
        });
      }

      const narrative = document.getElementById(
        "ceoAiDeepNarrative"
      );

      if (narrative) {
        narrative.replaceChildren();

        (
          Array.isArray(deep.narrative)
            ? deep.narrative
            : []
        ).forEach(function (paragraph) {
          const p = document.createElement("p");
          p.textContent = String(paragraph || "");
          narrative.appendChild(p);
        });
      }

      createDeepList(
        "ceoAiDeepFindings",
        deep.findings
      );

      createDeepList(
        "ceoAiDeepActions",
        deep.actions
      );

      const tableSection = document.getElementById(
        "ceoAiDeepTableSection"
      );
      const tableHead = document.getElementById(
        "ceoAiDeepTableHead"
      );
      const tableBody = document.getElementById(
        "ceoAiDeepTableBody"
      );

      const table = (
        deep.table
        && Array.isArray(deep.table.headers)
        && Array.isArray(deep.table.rows)
      )
        ? deep.table
        : null;

      if (
        tableSection
        && tableHead
        && tableBody
      ) {
        const showTable = Boolean(
          table
          && table.headers.length
          && table.rows.length
        );

        tableSection.hidden = !showTable;
        tableSection.style.display = (
          showTable
            ? ""
            : "none"
        );

        tableHead.replaceChildren();
        tableBody.replaceChildren();

        if (showTable) {
          setText(
            "ceoAiDeepTableTitle",
            table.title || "Detalle operativo"
          );

          const headerRow = document.createElement("tr");

          table.headers.forEach(
            function (header) {
              const th = document.createElement("th");
              th.textContent = String(header || "");
              headerRow.appendChild(th);
            }
          );

          tableHead.appendChild(headerRow);

          table.rows.forEach(
            function (row) {
              const tr = document.createElement("tr");

              (
                Array.isArray(row)
                  ? row
                  : []
              ).forEach(function (cell) {
                const td = document.createElement("td");
                td.textContent = (
                  cell === null
                  || cell === undefined
                )
                  ? "-"
                  : String(cell);
                tr.appendChild(td);
              });

              tableBody.appendChild(tr);
            }
          );
        }
      }

      const chartsContainer = document.getElementById(
        "ceoAiDeepCharts"
      );

      if (!chartsContainer) {
        return;
      }

      chartsContainer.replaceChildren();

      (
        Array.isArray(deep.graphs)
          ? deep.graphs
          : []
      ).slice(0, 4).forEach(
        function (graph, index) {
          if (
            !graph
            || !Array.isArray(graph.etiquetas)
            || !Array.isArray(graph.valores)
            || !graph.etiquetas.length
            || graph.etiquetas.length
              !== graph.valores.length
          ) {
            return;
          }

          const article = document.createElement("article");
          article.className = "ceo-ai-deep-chart-card";

          const title = document.createElement("h4");
          title.textContent = (
            graph.titulo
            || "Visualización ejecutiva"
          );

          const box = document.createElement("div");
          box.className = "ceo-ai-deep-chart-box";

          const canvas = document.createElement("canvas");
          canvas.id = (
            "ceoAiDeepChart"
            + index
          );

          box.appendChild(canvas);
          article.appendChild(title);
          article.appendChild(box);
          chartsContainer.appendChild(article);

          const validTypes = [
            "bar",
            "line",
            "pie",
            "doughnut"
          ];

          const chartType = validTypes.includes(
            graph.tipo_grafica
          )
            ? graph.tipo_grafica
            : "bar";

          const instance = new Chart(
            canvas,
            {
              type: chartType,
              data: {
                labels: graph.etiquetas,
                datasets: [
                  {
                    label: (
                      graph.titulo_eje
                      || "Valor"
                    ),
                    data: graph.valores,
                    borderWidth: 2,
                    borderRadius: 7,
                    backgroundColor: [
                      "rgba(139, 92, 246, .70)",
                      "rgba(56, 189, 248, .70)",
                      "rgba(34, 197, 94, .70)",
                      "rgba(245, 158, 11, .70)",
                      "rgba(239, 68, 68, .70)",
                      "rgba(6, 182, 212, .70)",
                      "rgba(236, 72, 153, .70)",
                      "rgba(99, 102, 241, .70)",
                      "rgba(20, 184, 166, .70)",
                      "rgba(244, 63, 94, .70)",
                      "rgba(132, 204, 22, .70)",
                      "rgba(251, 146, 60, .70)"
                    ],
                    borderColor: (
                      "rgba(196, 181, 253, 1)"
                    )
                  }
                ]
              },
              options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                  legend: {
                    labels: {
                      color: "#cbd5e1"
                    }
                  }
                },
                scales: (
                  chartType === "pie"
                  || chartType === "doughnut"
                )
                  ? {}
                  : {
                      x: {
                        ticks: {
                          color: "#cbd5e1"
                        },
                        grid: {
                          color: (
                            "rgba(255,255,255,.05)"
                          )
                        }
                      },
                      y: {
                        beginAtZero: true,
                        ticks: {
                          color: "#cbd5e1"
                        },
                        grid: {
                          color: (
                            "rgba(255,255,255,.05)"
                          )
                        }
                      }
                    }
              }
            }
          );

          ceoAiDeepChartInstances.push(
            instance
          );
        }
      );
    }

    // =========================================================
    // ADVERTENCIAS
    // =========================================================

    function renderWarnings(warnings) {
      const card = document.getElementById(
        "ceoAiWarningsCard"
      );

      if (!card) {
        return;
      }

      const hasWarnings = Array.isArray(warnings)
        && warnings.length > 0;

      card.hidden = !hasWarnings;
      card.style.display = hasWarnings ? "" : "none";

      renderList(
        "ceoAiWarnings",
        hasWarnings ? warnings : []
      );
    }

    // =========================================================
    // VALIDACIÓN DE PRECIO Y MARGEN
    // =========================================================

    function renderPricingValidation(strategy) {
      const pricingBox = document.getElementById(
        "ceoAiPricingValidation"
      );

      if (!pricingBox) {
        return;
      }

      if (
        strategy.sku_principal
        && !strategy.requiere_validacion_precio
      ) {
        pricingBox.hidden = false;

        setText(
          "ceoAiPricingSku",
          strategy.sku_principal
        );

        setText(
          "ceoAiPricingAveragePrice",
          formatMoney(
            strategy.average_sale_price
          )
        );

        setText(
          "ceoAiPricingAverageCost",
          formatMoney(
            strategy.average_unit_cost
          )
        );

        setText(
          "ceoAiPricingDiscount",
          formatPercent(
            strategy.descuento_propuesto_percent
          )
        );

        setText(
          "ceoAiPricingFinalPrice",
          formatMoney(
            strategy.precio_final_estimado
          )
        );

        setText(
          "ceoAiPricingFinalMargin",
          formatPercent(
            strategy.margen_final_estimado_percent
          )
        );

        return;
      }

      pricingBox.hidden = true;
    }

    // =========================================================
    // VISIBILIDAD DE TARJETAS
    // =========================================================

    function setCardVisibility(
      cardId,
      visible,
      fullWidth
    ) {
      const card = document.getElementById(
        cardId
      );

      if (!card) {
        return;
      }

      card.hidden = !visible;
      card.style.display = visible ? "" : "none";
      card.style.gridColumn = (
        visible && fullWidth
      )
        ? "1 / -1"
        : "";
    }

    // =========================================================
    // RENDERIZAR RESPUESTA COMPLETA
    // =========================================================

    function renderResult(result) {
      const isFallback = result.is_fallback === true;

      if (fallbackBox) {
        fallbackBox.hidden = !isFallback;
      }

      if (fallbackTitle) {
        fallbackTitle.textContent = result.fallback_title || "";
      }

      if (fallbackOptions) {
        fallbackOptions.replaceChildren();

        (Array.isArray(result.suggestions) ? result.suggestions : [])
          .slice(0, 4)
          .forEach(function (suggestion) {
            const question = String(suggestion.question || "").trim();

            if (!question) {
              return;
            }

            const button = document.createElement("button");
            button.type = "button";
            button.className = "ceo-ai-fallback-option";
            button.textContent = suggestion.label || question;
            button.title = question;

            button.addEventListener("click", function () {
              if (!questionInput) {
                showError("No se encontró el campo de pregunta.");
                return;
              }

              questionInput.value = question;
              questionInput.dispatchEvent(
                new Event("input", { bubbles: true })
              );

              if (!aiQuotaAllowed) {
                showError(
                  "Tu plan no tiene consultas disponibles en este momento."
                );
                return;
              }

              if (!submitButton) {
                showError("No se encontró el botón para generar la consulta.");
                return;
              }

              // Usamos el mismo botón real que el usuario pulsa para generar
              // una estrategia. Esto activa el flujo canónico del formulario
              // y evita depender de requestSubmit() o eventos sintéticos.
              submitButton.disabled = false;
              submitButton.click();
            });

            fallbackOptions.appendChild(button);
          });
      }

      const resolvedPeriod = result.resolved_period || {};
      const warehouseScope = result.warehouse_scope || {};

      if (warehouseScopeBox) {
        const scopeLabel = warehouseScope.label || "";
        const analyzedCount = Number(
          warehouseScope.analyzed_count || 0
        );

        warehouseScopeBox.textContent = scopeLabel
          ? (
              "Alcance: "
              + scopeLabel
              + (
                analyzedCount > 1
                  ? " · " + analyzedCount + " bodegas"
                  : ""
              )
            )
          : "";

        warehouseScopeBox.hidden = !scopeLabel;
      }

      if (resolvedPeriodBox) {
        const periodLabel = resolvedPeriod.label || "";

        resolvedPeriodBox.textContent = periodLabel
          ? "Periodo analizado: " + periodLabel
          : "";

        resolvedPeriodBox.hidden = !periodLabel;
      }

      const focus = (
        result.focus
        || (
          modeInput
            ? modeInput.value
            : "full"
        )
      );

      const strategy = (
        result.estrategia_venta
        || null
      );

      const customers = Array.isArray(
        result.clientes_riesgo
      )
        ? result.clientes_riesgo
        : [];

      const stockItems = Array.isArray(
        result.quiebres_stock
      )
        ? result.quiebres_stock
        : [];

      const graph = (
        result.grafica
        || null
      );

      setText(
        "ceoAiDirectAnswer",
        result.respuesta_directa
      );

      setText(
        "ceoAiSummary",
        result.resumen_ejecutivo
      );

      if (isFallback) {
        [
          "ceoAiStrategyCard",
          "ceoAiChartCard",
          "ceoAiCustomersCard",
          "ceoAiStockCard"
        ].forEach(function (cardId) {
          setCardVisibility(cardId, false, false);
        });

        if (warehouseScopeBox) {
          warehouseScopeBox.hidden = true;
        }

        if (resolvedPeriodBox) {
          resolvedPeriodBox.hidden = true;
        }

        if (resultsBox) {
          resultsBox.hidden = false;
          resultsBox.style.display = "";
        }

        return;
      }

      const showStrategy = Boolean(
        strategy
      ) && (
        focus === "sales"
        || focus === "full"
      );

      const showChart = Boolean(
        graph
      );

      const showCustomers = (
        focus === "churn"
        || focus === "full"
      );

      const showStock = (
        focus === "stock"
        || focus === "full"
      );

      setCardVisibility(
        "ceoAiStrategyCard",
        showStrategy,
        showStrategy && !showChart
      );

      setCardVisibility(
        "ceoAiChartCard",
        showChart,
        showChart && !showStrategy
      );

      setCardVisibility(
        "ceoAiCustomersCard",
        showCustomers,
        showCustomers && !showStock
      );

      setCardVisibility(
        "ceoAiStockCard",
        showStock,
        showStock && !showCustomers
      );

      if (showStrategy) {
        setText(
          "ceoAiStrategyTitle",
          strategy.titulo
        );

        setText(
          "ceoAiStrategyObjective",
          strategy.objetivo
        );

        setText(
          "ceoAiStrategyPromotion",
          strategy.promocion
        );

        setText(
          "ceoAiStrategyGoal",
          strategy.meta
        );

        setText(
          "ceoAiStrategyReason",
          strategy.justificacion
        );

        renderList(
          "ceoAiStrategyActions",
          strategy.acciones || []
        );

        renderPricingValidation(
          strategy
        );
      } else {
        setText(
          "ceoAiStrategyTitle",
          ""
        );

        setText(
          "ceoAiStrategyObjective",
          ""
        );

        setText(
          "ceoAiStrategyPromotion",
          ""
        );

        setText(
          "ceoAiStrategyGoal",
          ""
        );

        setText(
          "ceoAiStrategyReason",
          ""
        );

        renderList(
          "ceoAiStrategyActions",
          []
        );

        renderPricingValidation(
          {}
        );
      }

      if (showCustomers) {
        renderCustomers(
          customers
        );
      } else {
        const customersContainer = document.getElementById(
          "ceoAiCustomers"
        );

        if (customersContainer) {
          customersContainer.replaceChildren();
        }
      }

      if (showStock) {
        renderStock(
          stockItems
        );
      } else {
        const stockContainer = document.getElementById(
          "ceoAiStock"
        );

        if (stockContainer) {
          stockContainer.replaceChildren();
        }
      }

      renderChart(
        showChart
          ? graph
          : null
      );

      renderDeepAnalysis(
        result.deep_analysis || null
      );

      renderWarnings(
        result.advertencias || []
      );

      if (resultsBox) {
        resultsBox.hidden = false;
        resultsBox.style.display = "";

        resultsBox.scrollIntoView({
          behavior: "smooth",
          block: "start"
        });
      }
    }


    // =========================================================
    // EXPORTAR EXCEL PROFESIONAL
    // =========================================================

    function resetExcelExport() {
      ceoAiExportToken = null;

      if (exportExcelButton) {
        exportExcelButton.hidden = true;
        exportExcelButton.style.display = "none";
        exportExcelButton.disabled = false;

        const label = exportExcelButton.querySelector("span");
        if (label) {
          label.textContent = "Exportar Excel profesional";
        }
      }

      if (exportExcelHint) {
        exportExcelHint.hidden = true;
        exportExcelHint.style.display = "none";
      }
    }

    function enableExcelExport(token) {
      ceoAiExportToken = String(token || "").trim();
      const enabled = Boolean(ceoAiExportToken);

      if (exportExcelButton) {
        exportExcelButton.hidden = !enabled;
        exportExcelButton.style.display = enabled ? "inline-flex" : "none";
        exportExcelButton.disabled = false;
      }

      if (exportExcelHint) {
        exportExcelHint.hidden = !enabled;
        exportExcelHint.style.display = enabled ? "" : "none";
      }
    }

    function resolveDownloadFilename(response) {
      const disposition = response.headers.get("Content-Disposition") || "";
      const match = disposition.match(/filename="?([^";]+)"?/i);
      return match ? match[1] : "PAKIVOX_Copiloto_CEO.xlsx";
    }

    if (exportExcelButton) {
      exportExcelButton.addEventListener("click", async function () {
        hideError();

        if (!ceoAiExportToken) {
          showError("Genera nuevamente el análisis antes de exportar.");
          return;
        }

        const endpoint = form.dataset.exportEndpoint;
        if (!endpoint) {
          showError("No se configuró la ruta de exportación Excel.");
          return;
        }

        const label = exportExcelButton.querySelector("span");
        const originalText = label
          ? label.textContent
          : "Exportar Excel profesional";

        exportExcelButton.disabled = true;
        if (label) {
          label.textContent = "Construyendo Excel...";
        }

        try {
          const response = await fetch(endpoint, {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": getCsrfToken(),
              "X-Requested-With": "XMLHttpRequest"
            },
            credentials: "same-origin",
            body: JSON.stringify({
              export_token: ceoAiExportToken
            })
          });

          if (!response.ok) {
            let message = "No fue posible generar el Excel.";
            try {
              const errorData = await response.json();
              message = errorData.error || message;
            } catch (jsonError) {
              console.error("Error no JSON en exportación.", jsonError);
            }
            throw new Error(message);
          }

          const blob = await response.blob();
          const filename = resolveDownloadFilename(response);
          const downloadUrl = URL.createObjectURL(blob);
          const link = document.createElement("a");
          link.href = downloadUrl;
          link.download = filename;
          document.body.appendChild(link);
          link.click();
          link.remove();

          setTimeout(function () {
            URL.revokeObjectURL(downloadUrl);
          }, 1000);

          if (label) {
            label.textContent = "Excel descargado";
          }

          setTimeout(function () {
            if (label) {
              label.textContent = originalText;
            }
          }, 1800);

        } catch (error) {
          console.error("Error exportando Excel:", error);
          showError(error.message || "No fue posible generar el Excel.");
          if (label) {
            label.textContent = originalText;
          }
        } finally {
          exportExcelButton.disabled = false;
        }
      });
    }

    // =========================================================
    // ENVIAR CONSULTA AL COPILOTO
    // =========================================================

    form.addEventListener(
      "submit",
      async function (event) {
        event.preventDefault();

        hideError();
        resetExcelExport();

        if (loadingBox) {
          loadingBox.hidden = false;
          loadingBox.style.display = "";
        }

        if (resultsBox) {
          resultsBox.hidden = true;
          resultsBox.style.display = "none";
        }

        if (submitButton) {
          submitButton.disabled = true;
        }

        try {
          const endpoint = form.dataset.endpoint;

          if (!endpoint) {
            throw new Error(
              "No se configuró la ruta del Copiloto CEO."
            );
          }

          const manualDateFrom = dateFromInput
            ? dateFromInput.value
            : "";

          const manualDateTo = dateToInput
            ? dateToInput.value
            : "";

          if (
            manualDateFrom
            && manualDateTo
            && manualDateFrom > manualDateTo
          ) {
            throw new Error(
              "La fecha Desde no puede ser posterior a la fecha Hasta."
            );
          }

          const response = await fetch(
            endpoint,
            {
              method: "POST",
              headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCsrfToken(),
                "X-Requested-With": "XMLHttpRequest"
              },
              credentials: "same-origin",
              body: JSON.stringify({
                mode: modeInput
                  ? modeInput.value
                  : "full",
                question: questionInput
                  ? questionInput.value.trim()
                  : "",
                date_from: manualDateFrom || null,
                date_to: manualDateTo || null
              })
            }
          );

          const data = await parseJsonResponse(
            response
          );
          if (data.ai_quota) {
            updateAiQuota(data.ai_quota);
        }

          if (!response.ok || !data.ok) {
            if (data.result && data.result.is_fallback === true) {
              renderResult(data.result);
              return;
            }

            throw new Error(
              data.error
              || (
                "No fue posible generar el análisis. "
                + "Código HTTP: "
                + response.status
              )
            );
          }

          renderResult(
            data.result || {}
          );

          enableExcelExport(
            data.export_token || ""
          );

        } catch (error) {
          console.error(
            "Error en Copiloto CEO:",
            error
          );

          showError(
            error.message
            || "No fue posible generar el análisis."
          );

        } finally {
          if (loadingBox) {
            loadingBox.hidden = true;
            loadingBox.style.display = "none";
          }
          if (submitButton) {
            submitButton.disabled = !aiQuotaAllowed;
        }
        }
      }
    );
  });

/* ===== origen: <script id="pakivox-finance-executive-v8-script"> ===== */
document.addEventListener("DOMContentLoaded",()=>{const button=document.querySelector(".finance-v8-toggle"),details=document.getElementById("financeV8Details");if(!button||!details)return;button.addEventListener("click",()=>{const open=button.getAttribute("aria-expanded")==="true";button.setAttribute("aria-expanded",String(!open));details.hidden=open;button.querySelector("span").innerHTML=open?'<i class="bi bi-list-check"></i> Ver análisis completo':'<i class="bi bi-x-lg"></i> Cerrar análisis'})});

/* ===== origen: <script id="pakivox-dashboard-architecture-v2"> ===== */
(()=>{"use strict";const root=document.querySelector(".ceo-dashboard");if(!root)return;const tabs=[...root.querySelectorAll("[data-ceo-view]")],jumps=[...root.querySelectorAll("[data-ceo-jump]")],valid=new Set(["summary","operation","finance","activity","gemini"]);const activate=(view,scroll=false)=>{if(!valid.has(view))view="summary";root.dataset.ceoActiveView=view;tabs.forEach(tab=>{const active=tab.dataset.ceoView===view;tab.classList.toggle("is-active",active);tab.setAttribute("aria-selected",active?"true":"false")});window.setTimeout(()=>window.dispatchEvent(new Event("resize")),70);if(scroll){const bar=root.querySelector(".ceo-view-tabs");if(bar)bar.scrollIntoView({behavior:"smooth",block:"start"})}};activate("summary");tabs.forEach(tab=>tab.addEventListener("click",()=>activate(tab.dataset.ceoView||"summary")));jumps.forEach(btn=>btn.addEventListener("click",()=>activate(btn.dataset.ceoJump||"summary",true)))})();

/* ===== origen: <script id="pakivox-operation-disclosure-v4"> ===== */
document.addEventListener("DOMContentLoaded",()=>{const details=document.getElementById("ceoOperationDetails"),stock=document.querySelector(".operation-stock-detail"),toggle=document.querySelector("[data-operation-details-toggle]"),openers=[...document.querySelectorAll("[data-operation-details-open]")],closer=document.querySelector("[data-operation-details-close]");if(!details||!toggle)return;const setOpen=(open,scroll=false)=>{details.hidden=!open;if(stock)stock.hidden=!open;toggle.setAttribute("aria-expanded",open?"true":"false");const label=toggle.querySelector("span");if(label)label.textContent=open?"Ocultar desglose operativo":"Ver desglose operativo";if(open&&scroll)window.setTimeout(()=>details.scrollIntoView({behavior:"smooth",block:"start"}),80)};toggle.addEventListener("click",()=>setOpen(details.hidden,true));openers.forEach(button=>button.addEventListener("click",()=>setOpen(true,true)));if(closer)closer.addEventListener("click",()=>{setOpen(false);toggle.scrollIntoView({behavior:"smooth",block:"center"})});setOpen(false)});

/* ===== origen: <script id="pakivox-executive-analysis-v5-js"> ===== */
document.addEventListener("DOMContentLoaded",()=>{const table=document.querySelector(".inventory-executive-table tbody");if(!table)return;let filter="all",expanded=false,sortMode="urgency";const pairs=()=>[...table.querySelectorAll("[data-inventory-row]")].map(row=>({row,detail:row.nextElementSibling}));const render=()=>{const eligible=pairs().filter(({row})=>filter==="all"||row.dataset.status===filter);const visible=new Set((expanded?eligible:eligible.slice(0,5)).map(item=>item.row));pairs().forEach(({row,detail})=>{const show=visible.has(row);row.hidden=!show;if(!show&&detail){detail.hidden=true;const button=row.querySelector("[data-inventory-detail]");if(button){button.setAttribute("aria-expanded","false");button.textContent="Ver detalle"}}});const expand=document.querySelector("[data-inventory-expand]");if(expand){expand.hidden=eligible.length<=5;expand.textContent=expanded?"Mostrar solo los 5 prioritarios":"Ver análisis completo"}};document.querySelectorAll("[data-inventory-filter]").forEach(button=>button.addEventListener("click",()=>{filter=button.dataset.inventoryFilter||"all";expanded=false;document.querySelectorAll("[data-inventory-filter]").forEach(item=>item.classList.toggle("is-active",item===button));render()}));table.addEventListener("click",event=>{const button=event.target.closest("[data-inventory-detail]");if(!button)return;const row=button.closest("[data-inventory-row]"),detail=row?.nextElementSibling;if(!detail)return;const open=detail.hidden;detail.hidden=!open;button.setAttribute("aria-expanded",open?"true":"false");button.textContent=open?"Ocultar detalle":"Ver detalle"});const expand=document.querySelector("[data-inventory-expand]");if(expand)expand.addEventListener("click",()=>{expanded=!expanded;render()});const sort=document.querySelector("[data-inventory-sort]");if(sort)sort.addEventListener("click",()=>{sortMode=sortMode==="available"?"reserved":"available";const items=pairs().sort((a,b)=>Number(b.row.dataset[sortMode])-Number(a.row.dataset[sortMode]));items.forEach(({row,detail})=>{table.append(row);if(detail)table.append(detail)});expanded=false;render()});const alerts=[...document.querySelectorAll("[data-executive-alert]")],alertExpand=document.querySelector("[data-alerts-expand]");let alertsExpanded=false;const renderAlerts=()=>{alerts.forEach((alert,index)=>alert.hidden=!alertsExpanded&&index>=5);if(alertExpand)alertExpand.textContent=alertsExpanded?"Mostrar solo las 5 prioritarias":"Ver todas las alertas"};if(alertExpand)alertExpand.addEventListener("click",()=>{alertsExpanded=!alertsExpanded;renderAlerts()});render();renderAlerts()});

/* ===== origen: <script id="pakivox-inventory-sort-clarity-v6"> ===== */
document.addEventListener("DOMContentLoaded",()=>{const table=document.querySelector(".inventory-executive-table tbody"),button=document.querySelector("[data-inventory-sort]");if(!table||!button)return;let ascending=true;const pairs=()=>[...table.querySelectorAll("[data-inventory-row]")].map(row=>({row,detail:row.nextElementSibling}));const sort=()=>{pairs().sort((a,b)=>{const difference=Number(a.row.dataset.available)-Number(b.row.dataset.available);return ascending?difference:-difference}).forEach(({row,detail})=>{table.append(row);if(detail)table.append(detail)});button.innerHTML=`Disponible ${ascending?"↑":"↓"} <small>Reservado debajo</small>`;button.setAttribute("aria-label",`Ordenado por disponible, de ${ascending?"menor a mayor":"mayor a menor"}`)};button.addEventListener("click",event=>{event.preventDefault();event.stopImmediatePropagation();ascending=!ascending;sort()},{capture:true});sort()});
