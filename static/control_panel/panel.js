(() => {
  const sidebar = document.getElementById("sidebar");
  const backdrop = document.querySelector(".sidebar-backdrop");
  const openers = document.querySelectorAll("[data-sidebar-open]");
  const closers = document.querySelectorAll("[data-sidebar-close]");

  const setExpanded = (expanded) => {
    openers.forEach((button) => button.setAttribute("aria-expanded", String(expanded)));
  };

  const open = () => {
    if (!sidebar) return;
    sidebar.classList.add("open");
    backdrop?.classList.add("show");
    document.body.classList.add("sidebar-open");
    setExpanded(true);
  };

  const close = () => {
    if (!sidebar) return;
    sidebar.classList.remove("open");
    backdrop?.classList.remove("show");
    document.body.classList.remove("sidebar-open");
    setExpanded(false);
  };

  openers.forEach((button) => {
    button.setAttribute("aria-expanded", "false");
    button.addEventListener("click", open);
  });

  closers.forEach((button) => button.addEventListener("click", close));

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
  });

  window.addEventListener("resize", () => {
    if (window.innerWidth > 800) close();
  });

  document.querySelectorAll(".sidebar .nav-item").forEach((link) => {
    link.addEventListener("click", () => {
      if (window.innerWidth <= 800) close();
    });
  });

  // Match the company dashboard's appearance switcher without touching authentication.
  const themeButtons = [...document.querySelectorAll("[data-theme-set]")];
  const themeKey = "cubixgear:admin-theme";
  const collapseKey = "cubixgear:admin-sidebar-collapsed";
  const themeMedia = window.matchMedia("(prefers-color-scheme: dark)");
  const getStored = (key) => { try { return localStorage.getItem(key); } catch { return null; } };
  const store = (key, value) => { try { localStorage.setItem(key, value); } catch { /* storage may be disabled */ } };
  const applyTheme = (choice) => {
    const selected = ["light", "dark", "system"].includes(choice) ? choice : "light";
    document.documentElement.dataset.theme = selected === "system" ? (themeMedia.matches ? "dark" : "light") : selected;
    themeButtons.forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.themeSet === selected)));
  };
  let themeChoice = getStored(themeKey) || "light";
  applyTheme(themeChoice);
  themeButtons.forEach((button) => button.addEventListener("click", () => {
    themeChoice = button.dataset.themeSet;
    store(themeKey, themeChoice);
    applyTheme(themeChoice);
  }));
  themeMedia.addEventListener?.("change", () => {
    if (themeChoice === "system") applyTheme(themeChoice);
  });

  const collapseButton = document.querySelector("[data-sidebar-toggle]");
  const setCollapsed = (collapsed) => {
    document.body.classList.toggle("sidebar-collapsed", collapsed);
    collapseButton?.setAttribute("aria-expanded", String(!collapsed));
    collapseButton?.setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
    collapseButton?.setAttribute("title", collapsed ? "Expand sidebar" : "Collapse sidebar");
    document.querySelectorAll(".sidebar .nav-item").forEach((link) => {
      if (collapsed) link.title = link.textContent.trim();
      else link.removeAttribute("title");
    });
  };
  setCollapsed(getStored(collapseKey) === "true");
  collapseButton?.addEventListener("click", () => {
    const collapsed = !document.body.classList.contains("sidebar-collapsed");
    setCollapsed(collapsed);
    store(collapseKey, String(collapsed));
  });

})();
