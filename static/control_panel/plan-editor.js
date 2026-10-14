(() => {
  const form = document.querySelector("[data-plan-editor]");
  if (!form) return;

  const name = form.querySelector('[name="name"]');
  const keyField = form.querySelector("#plan-key-preview");
  if (name && keyField && !keyField.hasAttribute("data-fixed-key")) {
    const makeKey = (value) => {
      const ascii = String(value || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "");
      return ascii.toLowerCase().replace(/[^a-z0-9_\s-]/g, "")
        .replace(/[-\s]+/g, "-").replace(/^[-_]+|[-_]+$/g, "")
        .slice(0, 80).replace(/^[-_]+|[-_]+$/g, "") || "plan";
    };
    const renderKey = () => {
      keyField.value = name.value.trim() ? makeKey(name.value) : "";
    };
    name.addEventListener("input", renderKey);
    renderKey();
  }

  const counter = form.querySelector("[data-feature-counter]");
  const boxes = Array.from(form.querySelectorAll(".plan-feature-choices input[type=checkbox]"));
  if (counter) {
    const updateCount = () => {
      const count = boxes.filter(box => box.checked).length;
      counter.textContent = count + (count === 1 ? " feature selected" : " features selected");
    };
    boxes.forEach(box => box.addEventListener("change", updateCount));
    updateCount();
  }
})();
