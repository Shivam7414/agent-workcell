// Knowledge graph: six tables of the reference ERP, and six kinds of knowledge recorded about them.

import { KG_EDGES, KG_FACETS } from "./data.js";
import { createWires } from "./lib/wires.js";
import { el, src, sleep, codeBlock } from "./lib/dom.js";
import { reduced } from "./lib/iso.js";

export function initKnowledge(root) {
  const map = root.querySelector("[data-kg-map]");
  const detail = root.querySelector("[data-kg-detail]");
  const tabs = [...root.querySelectorAll("[data-kg-tabs] [role=tab]")];
  const nodes = [...map.querySelectorAll(".kg__node")];
  const bad = root.querySelector("[data-kg-bad]");
  const good = root.querySelector("[data-kg-good]");
  const wires = createWires(map, map.querySelector("[data-wires]"), KG_EDGES);
  let run = 0;

  function paint(facet, wireStates) {
    for (const node of nodes) {
      const table = node.dataset.t;
      node.classList.toggle("is-on", facet.on.includes(table));
      node.classList.toggle("is-bad", (facet.bad || []).includes(table));
      node.classList.toggle("is-dim", !facet.on.includes(table) && !(facet.bad || []).includes(table));
    }
    wires.mark(wireStates);
  }

  // The join path is shown in two beats: the tempting short path first, then the right one.
  async function playPath(facet, token) {
    const short = { "orders-suppliers": "is-bad", "payments-suppliers": "is-bad" };
    paint({ on: ["purchase_orders", "payments"], bad: ["suppliers"] }, short);
    bad.classList.add("is-on");
    good.classList.remove("is-on");
    await sleep(1500);
    if (token !== run) return;
    const states = { ...short };
    for (const [id, table] of [["invoices-orders", "supplier_invoices"], ["alloc-invoices", "payment_allocations"], ["alloc-payments", "payments"]]) {
      states[id] = "is-on";
      wires.mark(states);
      const line = wires.get(id);
      const length = line.getTotalLength();
      line.animate([{ strokeDasharray: `0 ${length}` }, { strokeDasharray: `${length} 0` }], { duration: 420, easing: "ease-in-out" });
      nodes.find((node) => node.dataset.t === table).classList.replace("is-dim", "is-on");
      await sleep(430);
      if (token !== run) return;
    }
    paint(facet, facet.wires);
    good.classList.add("is-on");
  }

  function show(id) {
    const facet = KG_FACETS[id];
    const token = ++run;
    root.dataset.facet = id;
    for (const tab of tabs) {
      const on = tab.dataset.facet === id;
      tab.setAttribute("aria-selected", String(on));
      tab.tabIndex = on ? 0 : -1;
    }
    bad.classList.remove("is-on");
    good.classList.remove("is-on");
    if (id === "path" && !reduced) {
      playPath(facet, token);
    } else {
      paint(facet, facet.wires);
      if (id === "path") {
        bad.classList.add("is-on");
        good.classList.add("is-on");
      }
    }
    const href = facet.file.startsWith("reference_erp/") ? src(facet.file) : undefined;
    detail.replaceChildren(
      el("h3", { text: facet.title }),
      el("p", { text: facet.text }),
      codeBlock(facet.file, facet.code, { href }),
    );
  }

  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => show(tab.dataset.facet));
    tab.addEventListener("keydown", (event) => {
      const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[event.key];
      if (!step) return;
      event.preventDefault();
      const next = tabs[(i + step + tabs.length) % tabs.length];
      next.focus();
      show(next.dataset.facet);
    });
  });

  show("record");
}
