// Claude Code section: when the figure comes into view, its built-in tools are struck out one by one,
// then the seven project tools appear. The delays are set here; the motion itself is in CSS.

export function initRuntime(root) {
  const removed = [...root.querySelectorAll("[data-rt-wall] li")];
  const added = [...root.querySelectorAll(".rt__tools li")];
  removed.forEach((item, i) => item.style.setProperty("--d", `${0.25 + i * 0.045}s`));
  const after = 0.25 + removed.length * 0.045 + 0.2;
  added.forEach((item, i) => item.style.setProperty("--d", `${after + i * 0.12}s`));

  const watcher = new IntersectionObserver(
    ([entry]) => {
      if (!entry.isIntersecting) return;
      root.classList.add("is-seen");
      watcher.disconnect();
    },
    { threshold: 0.4 },
  );
  watcher.observe(root);
}
