/* Progressive editing for existing credit packages.
   The server handles validation and permissions; this script only prepares the form. */
(() => {
  "use strict";
  const root = document.querySelector(".credit-panel-wrap");
  const form = root?.querySelector("[data-package-editor-form]");
  if (!form) return;

  const field = name => form.elements.namedItem(name);
  const name = field("name");
  const credits = field("credits");
  const price = field("price_inr");
  const packageId = field("package_id");
  const active = field("is_active");
  const order = field("sort_order");
  const orderMode = field("sort_order_mode");
  const formTitle = root.querySelector("[data-package-form-title]");
  const saveButton = root.querySelector("[data-package-save]");
  const cancelButton = root.querySelector("[data-package-cancel]");
  const editor = root.querySelector("#cg-package-editor");

  if (!name || !credits || !price || !packageId || !active || !order ||
      !orderMode || !formTitle || !saveButton || !cancelButton) return;

  const syncOrder = () => orderMode.dispatchEvent(new Event("change", { bubbles: true }));
  const editing = isEditing => {
    formTitle.textContent = isEditing ? "Edit credit package" : "New credit package";
    saveButton.textContent = isEditing ? "Save changes" : "Create package";
    cancelButton.hidden = !isEditing;
  };

  const focusEditor = () => {
    editor?.scrollIntoView({ block: "start" });
    name.focus({ preventScroll: true });
  };

  root.querySelectorAll("[data-package-edit]").forEach(button => {
    button.hidden = false;
    button.addEventListener("click", () => {
      packageId.value = button.dataset.id || "";
      name.value = button.dataset.name || "";
      credits.value = button.dataset.credits || "";
      price.value = button.dataset.price || "";
      orderMode.value = "manual";
      order.value = button.dataset.order || "0";
      active.checked = button.dataset.active === "true";
      syncOrder();
      editing(true);
      focusEditor();
    });
  });

  cancelButton.addEventListener("click", () => {
    form.reset();
    packageId.value = "";
    name.value = "";
    credits.value = "";
    price.value = "";
    orderMode.value = "unlimited";
    order.value = "";
    active.checked = true;
    syncOrder();
    editing(false);
    focusEditor();
  });

  // Retain the edit state and typed values when server-side validation fails.
  if (packageId.value) {
    editing(true);
  } else {
    editing(false);
  }
  syncOrder();
})();
