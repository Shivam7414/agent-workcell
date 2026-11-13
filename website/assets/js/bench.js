// Evaluation: the 28 benchmark cases with the 10 that were run, and the guard latency pairs.
// The numbers are in data.js and come from the files under bench/results/.

import { CASES, LATENCY } from "./data.js";
import { el } from "./lib/dom.js";

const KIND = { ok: "graded, correct", probe: "probe, no violation", none: "not run" };

export function initBench(root) {
  const grid = root.querySelector("[data-bench-cases]");
  const caption = root.querySelector("[data-bench-case]");
  const lat = root.querySelector("[data-bench-lat]");
  const note = root.querySelector("[data-bench-lat-note]");
  const buttons = [];

  function pick(i) {
    const c = CASES[i];
    buttons.forEach((button, n) => button.setAttribute("aria-pressed", String(n === i)));
    const state = c.run ? (c.kind === "probe" ? KIND.probe : KIND.ok) : KIND.none;
    caption.replaceChildren(el("b", { text: `${c.id} · ${c.role} · ${state}` }), c.question);
  }

  CASES.forEach((c, i) => {
    const kind = c.run ? (c.kind === "probe" ? "probe" : "ok") : "none";
    const button = el("button", { type: "button", "data-kind": kind, "aria-pressed": "false", "aria-label": `${c.id}: ${KIND[kind]}` });
    button.addEventListener("click", () => pick(i));
    button.addEventListener("pointerenter", () => pick(i));
    buttons.push(button);
    grid.append(button);
  });
  pick(CASES.findIndex((c) => c.id === "buyer-overdue-invoices"));

  const ms = (value) => `${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ms`;

  function bar(kind, label, value, max) {
    const fill = el("div", { class: "lat__fill" });
    fill.style.width = `${(value / max) * 100}%`;
    return el("div", { class: `lat__bar lat__bar--${kind}`, "aria-label": `${label}: ${ms(value)}` },
      el("div", { class: "lat__track" }, fill),
      el("span", { class: "lat__val", text: ms(value) }),
    );
  }

  function showScale(scale) {
    const set = LATENCY[scale];
    lat.replaceChildren(
      ...set.rows.map(([name, plain, guarded]) => {
        const max = Math.max(plain, guarded);
        return el("div", { class: "lat__row" },
          el("div", { class: "lat__name", text: name }),
          el("div", { class: "lat__bars" }, bar("plain", "Unguarded", plain, max), bar("guarded", "Through the guard", guarded, max)),
        );
      }),
    );
    note.textContent = set.note;
  }

  root.querySelector("[data-bench-scale]").addEventListener("change", (event) => showScale(event.target.value));
  showScale("900000");
}
