// CRM · inicio (templates/crm/home.html): buscador de clientes en vivo.
document.addEventListener("DOMContentLoaded", function () {
const input = document.getElementById("customerLiveSearch");
const form = document.getElementById("smartCustomerSearchForm");
const rows = Array.from(document.querySelectorAll(".customer-row"));
const noResultsRow = document.getElementById("customerNoResults");
const counter = document.getElementById("customerSearchCounter");

function normalizeText(value) {
    return (value || "")
        .toString()
        .toLowerCase()
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .trim();
}

function onlyNumbers(value) {
    return (value || "").toString().replace(/\D/g, "");
}

function getVisibleRows() {
    return rows.filter(function (row) {
        return !row.classList.contains("is-hidden");
    });
}

function updateCounter(visibleCount) {
    if (!counter) {
        return;
    }

    if (visibleCount === 1) {
        counter.textContent = "1 cliente encontrado";
    } else {
        counter.textContent = "Mostrando " + visibleCount + " clientes";
    }
}

function filterCustomers() {
    const rawSearch = input.value || "";
    const search = normalizeText(rawSearch);
    const searchNumbers = onlyNumbers(rawSearch);
    const words = search.split(/\s+/).filter(Boolean);

    let visibleCount = 0;

    rows.forEach(function (row) {
        const rowText = normalizeText(row.dataset.search);
        const rowNumbers = onlyNumbers(row.dataset.search);

        let matchesText = true;

        if (words.length > 0) {
            matchesText = words.every(function (word) {
                return rowText.includes(word);
            });
        }

        let matchesPhone = false;

        if (searchNumbers.length > 0) {
            matchesPhone = rowNumbers.includes(searchNumbers);
        }

        const shouldShow = search === "" || matchesText || matchesPhone;

        if (shouldShow) {
            row.classList.remove("is-hidden");
            visibleCount += 1;
        } else {
            row.classList.add("is-hidden");
        }
    });

    if (noResultsRow) {
        noResultsRow.hidden = visibleCount !== 0;
    }

    updateCounter(visibleCount);
}

if (input) {
    input.addEventListener("input", filterCustomers);

    input.addEventListener("keydown", function (event) {
        if (event.key === "Enter") {
            const visibleRows = getVisibleRows();

            if (visibleRows.length === 1 && input.value.trim() !== "") {
                event.preventDefault();
                window.location.href = visibleRows[0].dataset.profileUrl;
            }
        }
    });
}

if (form) {
    form.addEventListener("submit", function (event) {
        const visibleRows = getVisibleRows();

        if (visibleRows.length === 1 && input.value.trim() !== "") {
            event.preventDefault();
            window.location.href = visibleRows[0].dataset.profileUrl;
            return;
        }

        if (input.value.trim() === "") {
            event.preventDefault();
            input.focus();
        }
    });
}

filterCustomers();
});
