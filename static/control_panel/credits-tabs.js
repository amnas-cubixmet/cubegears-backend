/* Progressive, keyboard-accessible tabs for the CubeGears Credit Control page.
   With JS disabled the sections remain visible; all POST actions are unchanged. */
(() => {
  "use strict";
  const root = document.querySelector(".credit-panel-wrap");
  const navigation = root?.querySelector("[data-credit-tab-navigation]");
  if (!root || !navigation) return;

  // Unlimited uses automatic ordering without a numeric limit in the form.
  // Custom order enables the number field. Django also enforces this choice,
  // so the behavior remains safe if JavaScript is disabled.
  const orderMode = root.querySelector("[data-credit-order-mode]");
  const orderValue = root.querySelector("[data-credit-order-value]");
  if (orderMode && orderValue) {
    const syncOrderMode = () => {
      const manual = orderMode.value === "manual";
      orderValue.disabled = !manual;
      orderValue.required = manual;
      orderValue.setAttribute("aria-disabled", String(!manual));
    };
    orderMode.addEventListener("change", syncOrderMode);
    syncOrderMode();
  }

  const buttons = Array.from(navigation.querySelectorAll("[data-credit-tab]"));
  const panels = Array.from(root.querySelectorAll("[data-credit-panel]"));
  const keys = buttons.map(button => button.dataset.creditTab);
  const resumeKey = "cubegears-credit-return-tab";

  if (buttons.length !== 6 || panels.length !== buttons.length) return;
  if (!buttons.every(button => panels.some(panel =>
    panel.id === button.getAttribute("aria-controls") &&
    panel.dataset.creditPanel === button.dataset.creditTab
  ))) return;

  const savedTab = (() => {
    try {
      const key = window.sessionStorage.getItem(resumeKey);
      window.sessionStorage.removeItem(resumeKey);
      return key;
    } catch (_) {
      return null;
    }
  })();

  const tabFromLocation = () => {
    const hash = window.location.hash.replace(/^#/, "");
    const candidate = hash.startsWith("credit-panel-")
      ? hash.slice("credit-panel-".length) : null;
    return keys.includes(candidate) ? candidate : null;
  };

  const selectTab = (key, options = {}) => {
    const { focus = false, updateUrl = false } = options;
    if (!keys.includes(key)) return;

    for (const button of buttons) {
      const selected = button.dataset.creditTab === key;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
      if (selected && focus) button.focus();
    }
    for (const panel of panels) {
      panel.hidden = panel.dataset.creditPanel !== key;
    }

    if (updateUrl) {
      const url = window.location.pathname + window.location.search +
        "#credit-panel-" + key;
      window.history.pushState(null, "", url);
    }
  };

  const queryHasCompany = new URLSearchParams(window.location.search).has("company");
  const initialTab = tabFromLocation() ||
    (keys.includes(savedTab) ? savedTab : null) ||
    (queryHasCompany ? "ledger" : "packages");

  selectTab(initialTab);
  navigation.hidden = false;
  root.classList.add("credit-tabs-enabled");

  buttons.forEach((button, index) => {
    button.addEventListener("click", () => {
      selectTab(button.dataset.creditTab, { updateUrl: true });
    });

    button.addEventListener("keydown", event => {
      let nextIndex = -1;
      if (event.key === "ArrowRight" || event.key === "ArrowDown") {
        nextIndex = (index + 1) % buttons.length;
      } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
        nextIndex = (index + buttons.length - 1) % buttons.length;
      } else if (event.key === "Home") {
        nextIndex = 0;
      } else if (event.key === "End") {
        nextIndex = buttons.length - 1;
      }
      if (nextIndex < 0) return;
      event.preventDefault();
      selectTab(buttons[nextIndex].dataset.creditTab,
        { focus: true, updateUrl: true });
    });
  });

  // Preserve the relevant tab when the server redirects after a form POST.
  root.querySelectorAll(".credit-tab-panel form[method='post']").forEach(form => {
    form.addEventListener("submit", () => {
      const panel = form.closest("[data-credit-panel]");
      if (!panel) return;
      try {
        window.sessionStorage.setItem(resumeKey, panel.dataset.creditPanel);
      } catch (_) {
        // The form still submits securely if session storage is unavailable.
      }
    });
  });

  const syncFromUrl = () => {
    const selected = tabFromLocation();
    if (selected) selectTab(selected);
  };
  window.addEventListener("popstate", syncFromUrl);
  window.addEventListener("hashchange", syncFromUrl);
})();
