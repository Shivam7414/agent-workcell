// Walkthrough: follow one recorded question through the cell, step by step.
// It plays by itself while on screen. Any manual step pauses it.

import { LANES, TRACES, TRACE_RESULT } from "./data.js";
import { el, src, codeBlock } from "./lib/dom.js";
import { reduced } from "./lib/iso.js";

const STEP_MS = 7000;

export function initTrace(root) {
  const lanes = root.querySelector("[data-tr-lanes]");
  const list = root.querySelector("[data-tr-steps]");
  const view = root.querySelector("[data-tr-view]");
  const toggle = root.querySelector("[data-tr-toggle]");
  let trace = TRACES.overdue;
  let index = 0;
  let playing = !reduced;
  let visible = false;
  let timer = 0;
  let bar = null;

  const marker = el("span", { class: "tr__marker" });
  const laneNodes = LANES.map((name) => el("li", { text: name }));
  lanes.append(...laneNodes, marker);

  function buildSteps() {
    list.replaceChildren(
      ...trace.map((step, i) => {
        const button = el("button", { type: "button" },
          el("span", { class: "n", text: String(i + 1) }),
          el("span", { class: "t", text: step.title }),
          el("span", { class: "w", text: step.where }),
        );
        button.addEventListener("click", () => {
          pause();
          go(i);
        });
        return el("li", {}, button);
      }),
    );
  }

  function go(i) {
    index = (i + trace.length) % trace.length;
    const step = trace[index];
    const seen = new Set(trace.slice(0, index + 1).map((s) => s.lane));
    laneNodes.forEach((node, lane) => {
      node.classList.toggle("is-on", lane === step.lane);
      node.classList.toggle("is-seen", seen.has(lane));
    });
    marker.style.left = `${((step.lane + 0.5) / LANES.length) * 100}%`;

    [...list.children].forEach((item, n) => {
      item.classList.toggle("is-done", n < index);
      const button = item.firstElementChild;
      if (n === index) button.setAttribute("aria-current", "step");
      else button.removeAttribute("aria-current");
    });

    bar = el("span", { class: "tr__timer" });
    const parts = [
      bar,
      el("div", { class: "tr__where" },
        el("span", { class: "tag tag--plain", text: `Step ${index + 1} of ${trace.length}` }),
        el("span", { text: step.where }),
      ),
      el("h3", { text: step.title }),
      el("p", { text: step.text }),
      ...(step.blocks || []).map((block) => codeBlock(block.title, block.lines)),
      step.hostile && el("p", { class: "tr__answer tr__answer--hostile", text: step.hostile }),
      step.answer && el("p", { class: "tr__answer", text: step.answer }),
      step.meta && el("p", { class: "tr__meta" }, `${step.meta} `, el("a", { href: src(TRACE_RESULT), rel: "noopener", text: "Result file" })),
    ];
    view.replaceChildren(...parts.filter(Boolean));
    schedule();
  }

  function schedule() {
    clearTimeout(timer);
    if (!playing || !visible) return;
    bar.animate([{ transform: "scaleX(0)" }, { transform: "scaleX(1)" }], { duration: STEP_MS, easing: "linear", fill: "forwards" });
    timer = setTimeout(() => go(index + 1), STEP_MS);
  }

  function pause() {
    playing = false;
    clearTimeout(timer);
    toggle.textContent = "Play";
    if (bar) bar.getAnimations().forEach((a) => a.cancel());
  }

  toggle.textContent = playing ? "Pause" : "Play";
  toggle.addEventListener("click", () => {
    if (playing) {
      pause();
    } else {
      playing = true;
      toggle.textContent = "Pause";
      schedule();
    }
  });
  root.querySelector("[data-tr-prev]").addEventListener("click", () => {
    pause();
    go(index - 1);
  });
  root.querySelector("[data-tr-next]").addEventListener("click", () => {
    pause();
    go(index + 1);
  });
  root.querySelector("[data-tr-switch]").addEventListener("change", (event) => {
    trace = TRACES[event.target.value];
    buildSteps();
    go(0);
  });

  new IntersectionObserver(
    ([entry]) => {
      visible = entry.isIntersecting;
      if (visible) schedule();
      else clearTimeout(timer);
    },
    { threshold: 0.3 },
  ).observe(root);

  buildSteps();
  go(0);
}
