// Hero: the cell runs one question in a loop, and the caption follows it.

import { createCell } from "./scene.js";
import { reduced } from "./lib/iso.js";

export function initHero(root) {
  const canvas = root.querySelector("[data-scene]");
  const list = root.querySelector("[data-hero-steps]");
  const items = [...list.children];

  // With reduced motion the scene is one still frame, so the whole list stays visible.
  if (!reduced) {
    list.dataset.live = "";
    items[0].classList.add("is-on");
  }

  createCell(canvas, {
    mode: "play",
    onStep(step) {
      if (reduced) return;
      items.forEach((item, i) => item.classList.toggle("is-on", i === step));
    },
  });
}
