// Navigation: the mobile menu, the scroll progress line and the current-section mark.

export function initNav(nav) {
  const toggle = nav.querySelector("[data-nav-toggle]");
  const sheet = nav.querySelector("[data-nav-sheet]");
  const progress = nav.querySelector("[data-nav-progress]");
  const links = [...nav.querySelectorAll(".nav__links a")];

  const close = () => {
    sheet.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
  };

  toggle.addEventListener("click", () => {
    const open = sheet.hidden;
    sheet.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
  });
  sheet.addEventListener("click", (event) => {
    if (event.target.closest("a")) close();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") close();
  });

  let queued = false;
  const update = () => {
    queued = false;
    const max = document.documentElement.scrollHeight - window.innerHeight;
    progress.style.transform = `scaleX(${max > 0 ? window.scrollY / max : 0})`;
    nav.dataset.scrolled = String(window.scrollY > 8);
  };
  window.addEventListener(
    "scroll",
    () => {
      if (!queued) {
        queued = true;
        requestAnimationFrame(update);
      }
    },
    { passive: true },
  );
  update();

  const sections = links.map((link) => document.querySelector(link.getAttribute("href"))).filter(Boolean);
  const watcher = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        for (const link of links) {
          const on = link.getAttribute("href") === `#${entry.target.id}`;
          if (on) link.setAttribute("aria-current", "true");
          else link.removeAttribute("aria-current");
        }
      }
    },
    { rootMargin: "-45% 0px -50% 0px" },
  );
  sections.forEach((section) => watcher.observe(section));
}
