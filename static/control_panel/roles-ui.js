(() => {
  "use strict";
  const editor = document.querySelector("[data-role-editor]");
  if (!editor) return;

  const company = document.querySelector("#id_company");
  const sections = [...editor.querySelectorAll("[data-role-scope]")];
  const wildcard = editor.querySelector("#permission-wildcard");
  const summary = editor.querySelector("[data-role-selection-summary]");
  const scopeLabel = editor.querySelector("[data-role-scope-label]");
  const regular = [...editor.querySelectorAll('input[type="checkbox"][name="permissions"]:not([value="*"])')];

  function updateSummary() {
    const count = regular.filter((input) => input.checked).length;
    if (summary) summary.textContent = wildcard?.checked
      ? "Full access enabled for this role's scope."
      : count + " action" + (count === 1 ? "" : "s") + " selected.";
  }

  function syncScope(clearOther) {
    const scope = editor.dataset.fixedScope || (company?.value ? "workshop" : "platform");
    for (const section of sections) {
      const isCurrent = section.dataset.roleScope === scope;
      section.hidden = !isCurrent;
      if (clearOther && !isCurrent) {
        section.querySelectorAll('input[type="checkbox"][name="permissions"]').forEach((input) => {
          input.checked = false;
        });
      }
    }
    if (scopeLabel) scopeLabel.textContent = scope === "platform"
      ? "Scope: SaaS Platform"
      : "Scope: Workshop";
    if (clearOther && wildcard) wildcard.checked = false;
    updateSummary();
  }

  editor.addEventListener("click", (event) => {
    const button = event.target.closest("[data-role-select]");
    if (!button) return;
    const row = button.closest("[data-role-module]");
    if (!row) return;
    const enabled = button.dataset.roleSelect === "all";
    row.querySelectorAll('input[type="checkbox"][name="permissions"]').forEach((input) => {
      input.checked = enabled;
    });
    if (enabled && wildcard) wildcard.checked = false;
    updateSummary();
  });

  editor.addEventListener("change", (event) => {
    if (event.target === wildcard) {
      if (wildcard.checked) regular.forEach((input) => { input.checked = false; });
    } else if (event.target.matches('input[type="checkbox"][name="permissions"]')) {
      if (event.target.checked && wildcard) wildcard.checked = false;
    }
    updateSummary();
  });
  company?.addEventListener("change", () => syncScope(true));
  syncScope(false);
})();
