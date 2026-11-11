// Architecture explorer: select a part of the cell, in the scene or from the list, and read about it.

import { createCell } from "./scene.js";
import { PARTS, PART_ORDER } from "./data.js";
import { el, src } from "./lib/dom.js";

const ZONE_TAG = { trusted: "tag--trusted", enforced: "tag--enforced", untrusted: "tag--untrusted" };

export function initExplorer(root) {
  const canvas = root.querySelector("[data-xp-canvas]");
  const list = root.querySelector("[data-xp-parts]");
  const panel = root.querySelector("[data-xp-panel]");
  const buttons = new Map();

  const cell = createCell(canvas, { mode: "explore", onSelect: select });

  for (const id of PART_ORDER) {
    const part = PARTS[id];
    const button = el("button", { type: "button", "data-zone": part.zone, "aria-pressed": "false", text: part.name });
    button.addEventListener("click", () => select(id));
    buttons.set(id, button);
    list.append(button);
  }

  function links(paths) {
    return paths.map((path) => el("a", { href: src(path), rel: "noopener", text: path }));
  }

  function select(id) {
    const part = PARTS[id];
    for (const [key, button] of buttons) button.setAttribute("aria-pressed", String(key === id));
    cell.select(id);
    panel.replaceChildren(
      el("div", { class: "xp__tags" },
        el("span", { class: "tag tag--plain", text: part.status }),
        el("span", { class: `tag ${ZONE_TAG[part.zone]}`, text: part.zoneLabel }),
      ),
      el("h3", { text: part.name }),
      el("p", { class: "xp__job", text: part.job }),
      el("ul", { class: "ticks" }, part.facts.map((fact) => el("li", { text: fact }))),
      el("dl", { class: "xp__refs" },
        el("div", {}, el("dt", { text: "Code" }), el("dd", {}, links(part.code))),
        el("div", {}, el("dt", { text: "Tests" }), el("dd", {}, links(part.tests))),
      ),
    );
  }

  select("runtime");
}
