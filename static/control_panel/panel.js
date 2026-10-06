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
})();
