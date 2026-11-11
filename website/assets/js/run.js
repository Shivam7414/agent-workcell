// "Run it": the tabs of commands, and the copy buttons on every code block of the page.

export function initRun(root) {
  const tabs = [...root.querySelectorAll("[role=tab]")];

  function show(tab) {
    for (const other of tabs) {
      const on = other === tab;
      other.setAttribute("aria-selected", String(on));
      other.tabIndex = on ? 0 : -1;
      document.getElementById(other.getAttribute("aria-controls")).hidden = !on;
    }
  }

  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => show(tab));
    tab.addEventListener("keydown", (event) => {
      const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[event.key];
      if (!step) return;
      event.preventDefault();
      const next = tabs[(i + step + tabs.length) % tabs.length];
      next.focus();
      show(next);
    });
  });

  for (const button of document.querySelectorAll("[data-copy]")) {
    button.addEventListener("click", async () => {
      // Copy the commands without their trailing comments.
      const pre = button.closest(".code").querySelector("pre").cloneNode(true);
      pre.querySelectorAll(".c").forEach((comment) => comment.remove());
      const text = pre.textContent.split("\n").map((line) => line.trimEnd()).join("\n");
      try {
        await navigator.clipboard.writeText(text);
        button.textContent = "Copied";
      } catch {
        button.textContent = "Copy failed";
      }
      setTimeout(() => (button.textContent = "Copy"), 1600);
    });
  }
}
