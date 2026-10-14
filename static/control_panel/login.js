/* Login-only progressive enhancement: password visibility toggle. */
(() => {
  "use strict";

  const toggle = document.querySelector("[data-password-toggle]");
  const password = document.getElementById("id_password");

  if (!toggle || !password) return;

  toggle.addEventListener("click", () => {
    const showing = password.type === "password";
    password.type = showing ? "text" : "password";
    toggle.classList.toggle("is-visible", showing);
    toggle.setAttribute("aria-pressed", String(showing));
    toggle.setAttribute("aria-label", showing ? "Hide password" : "Show password");
    password.focus({ preventScroll: true });
  });
})();
