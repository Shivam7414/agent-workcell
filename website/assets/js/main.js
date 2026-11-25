// Entry point. The text of the page reads in full without this script: the two canvas scenes stay empty,
// the small figures show their end state, and the steps of the answer are a plain numbered list.

import { initShift } from "./shift.js";
import { initFlow } from "./flow.js";
import { initCases, initGuard, initInView, initStagger } from "./figures.js";

document.documentElement.classList.add("js");

const starts = [
  ["[data-stagger]", initStagger],
  ["[data-inview]", initInView],
  ["[data-shift]", initShift],
  ["[data-flow]", initFlow],
  ["[data-guard]", initGuard],
  ["[data-cases]", initCases],
];

for (const [selector, init] of starts) {
  for (const root of document.querySelectorAll(selector)) {
    try {
      init(root);
    } catch (error) {
      // One broken figure must not stop the others.
      console.error(`Figure ${selector} failed to start`, error);
    }
  }
}

// The copy buttons on the command blocks.
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
