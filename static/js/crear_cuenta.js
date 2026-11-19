// Creacion de cuenta (templates/accounts/register.html)
(function () {
  "use strict";
  const form = document.getElementById("registration-form");
  const submitButton = document.getElementById("submit-button");
  const submitText = document.getElementById("submit-text");
  const passwordInput = form ? document.getElementById(form.dataset.passwordInput) : null;
  const strengthBars = Array.from(document.querySelectorAll("#password-strength .strength-bar"));
  const strengthColors = ["#A3262F", "#C98A1A", "#5483B3", "#235347"];

  document.querySelectorAll("[data-password-toggle]").forEach(function (button) {
    button.addEventListener("click", function () {
      const input = document.getElementById(button.getAttribute("data-password-toggle"));
      if (!input) return;
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      button.textContent = show ? "Ocultar" : "Mostrar";
      button.setAttribute("aria-label", show ? "Ocultar contraseña" : "Mostrar contraseña");
      input.focus();
    });
  });

  function passwordScore(value) {
    let score = 0;
    if (value.length >= 8) score += 1;
    if (/[a-z]/.test(value) && /[A-Z]/.test(value)) score += 1;
    if (/\d/.test(value)) score += 1;
    if (/[^A-Za-z0-9]/.test(value)) score += 1;
    return score;
  }

  function updatePasswordStrength() {
    if (!passwordInput) return;
    const score = passwordScore(passwordInput.value);
    strengthBars.forEach(function (bar, index) {
      bar.style.background = index < score ? strengthColors[Math.max(score - 1, 0)] : "";
    });
  }

  if (passwordInput) {
    passwordInput.addEventListener("input", updatePasswordStrength);
    updatePasswordStrength();
  }

  if (form && submitButton && submitText) {
    form.addEventListener("submit", function (event) {
      if (!form.checkValidity()) {
        event.preventDefault();
        form.reportValidity();
        return;
      }
      submitButton.disabled = true;
      submitText.textContent = "Creando tu cuenta...";
    });
  }
  })();
