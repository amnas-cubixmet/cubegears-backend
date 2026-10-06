(() => {
  const sidebar = document.getElementById("sidebar");
  const backdrop = document.querySelector(".sidebar-backdrop");
  const openers = document.querySelectorAll("[data-sidebar-open]");
  const closers = document.querySelectorAll("[data-sidebar-close]");

  const open = () => {
    if (!sidebar) return;
    sidebar.classList.add("open");
    backdrop?.classList.add("show");
    document.body.style.overflow = "hidden";
  };

  const close = () => {
    if (!sidebar) return;
    sidebar.classList.remove("open");
    backdrop?.classList.remove("show");
    document.body.style.overflow = "";
  };

  openers.forEach((button) => button.addEventListener("click", open));
  closers.forEach((button) => button.addEventListener("click", close));
  window.addEventListener("resize", () => {
    if (window.innerWidth > 800) close();
  });
})();