// Entry point. Each figure lives in its own module and starts only if its element is on the page.

import { initNav } from "./nav.js";
import { initHero } from "./hero.js";
import { initShift } from "./shift.js";
import { initExplorer } from "./explorer.js";
import { initRuntime } from "./runtime.js";
import { initKnowledge } from "./knowledge.js";
import { initGuard } from "./guard.js";
import { initTrace } from "./trace.js";
import { initBench } from "./bench.js";
import { initRun } from "./run.js";

document.documentElement.classList.add("js");

const figures = [
  ["[data-nav]", initNav],
  ["[data-hero]", initHero],
  ["[data-shift]", initShift],
  ["[data-explorer]", initExplorer],
  ["[data-rt]", initRuntime],
  ["[data-kg]", initKnowledge],
  ["[data-guard]", initGuard],
  ["[data-trace]", initTrace],
  ["[data-bench]", initBench],
  ["[data-run]", initRun],
];

for (const [selector, init] of figures) {
  const root = document.querySelector(selector);
  if (!root) continue;
  try {
    init(root);
  } catch (error) {
    // One broken figure must not stop the others.
    console.error(`Figure ${selector} failed to start`, error);
  }
}
