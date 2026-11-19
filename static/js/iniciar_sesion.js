// Inicio de sesion (templates/accounts/login.html)
(function () {
  "use strict";
  const form = document.getElementById("login-form");
  const passwordInput = document.getElementById("password");
  const passwordToggle = document.getElementById("password-toggle");
  const submitButton = document.getElementById("submit-button");
  const submitText = document.getElementById("submit-text");

  if (passwordInput && passwordToggle) {
    passwordToggle.addEventListener("click", function () {
      const show = passwordInput.type === "password";
      passwordInput.type = show ? "text" : "password";
      passwordToggle.setAttribute("aria-pressed", String(show));
      passwordToggle.setAttribute("aria-label", show ? "Ocultar contraseña" : "Mostrar contraseña");
      passwordToggle.textContent = show ? "Ocultar" : "Mostrar";
      passwordInput.focus();
    });
  }

  if (form && submitButton && submitText) {
    form.addEventListener("submit", function (event) {
      if (!form.checkValidity()) {
        event.preventDefault();
        form.reportValidity();
        return;
      }
      submitButton.disabled = true;
      submitText.textContent = "Entrando...";
    });
  }
  })();
