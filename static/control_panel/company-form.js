(() => {
  const form = document.querySelector("[data-company-editor]");
  if (!form) return;

  const name = form.querySelector('[name="name"]');
  const slugPreview = document.getElementById("company-slug-preview");
  const isEdit = slugPreview?.hasAttribute("data-fixed-slug") ?? false;

  const slugify = (value) => {
    const ascii = String(value || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
    return ascii.toLowerCase().replace(/[^a-z0-9_\s-]/g, "")
      .trim().replace(/[-\s]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 160) || "company";
  };
  if (!isEdit && name && slugPreview) {
    const update = () => {
      slugPreview.value = name.value.trim() ? slugify(name.value) : "";
    };
    name.addEventListener("input", update);
    update();
  }

  const input = form.querySelector("[data-company-logo]");
  const preview = form.querySelector("[data-logo-preview]");
  const placeholder = form.querySelector("[data-logo-placeholder]");
  const message = form.querySelector("[data-logo-message]");
  const clear = form.querySelector("[data-clear-logo]");
  const originalSrc = preview?.getAttribute("src") || "";
  let objectURL = null;

  const restore = () => {
    if (objectURL) URL.revokeObjectURL(objectURL);
    objectURL = null;
    if (preview) {
      if (originalSrc) {
        preview.src = originalSrc;
        preview.hidden = false;
      } else {
        preview.removeAttribute("src");
        preview.hidden = true;
      }
    }
    if (placeholder) placeholder.hidden = Boolean(originalSrc);
    if (clear) clear.hidden = true;
  };

  input?.addEventListener("change", () => {
    const file = input.files?.[0];
    restore();
    if (message) message.textContent = "";
    if (!file) return;
    const allowed = ["image/jpeg", "image/png", "image/webp"];
    if (!allowed.includes(file.type) || file.size > 2 * 1024 * 1024) {
      input.value = "";
      if (message) message.textContent = "Choose a JPG, PNG or WebP image up to 2 MB.";
      return;
    }
    objectURL = URL.createObjectURL(file);
    if (preview) {
      preview.src = objectURL;
      preview.hidden = false;
    }
    if (placeholder) placeholder.hidden = true;
    if (clear) clear.hidden = false;
    if (message) message.textContent = file.name;
  });

  clear?.addEventListener("click", () => {
    if (input) input.value = "";
    if (message) message.textContent = "";
    restore();
  });
  window.addEventListener("pagehide", () => {
    if (objectURL) URL.revokeObjectURL(objectURL);
  });
})();
