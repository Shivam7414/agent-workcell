// "Watch one question get answered": the workcell scene and the five steps beside it.
//
// The scene runs the loop and reports each step as it starts. This file shows the text of that step,
// and offers a bar for each step and a pause button. The steps themselves are written in index.html.

import { createCell, STEP_SECONDS } from "./scene.js";
import { reduced } from "./lib/iso.js";
import { watchView } from "./figures.js";

// The arm moves at a little over half speed, so each step can be read while it happens.
const SPEED = 0.55;

export function initFlow(root) {
  const canvas = root.querySelector("[data-flow-canvas]");
  const steps = [...root.querySelectorAll(".flow__steps > li")];
  const controls = root.querySelector("[data-flow-ctl]");
  const dots = root.querySelector("[data-flow-dots]");
  const toggle = root.querySelector("[data-flow-toggle]");
  let playing = !reduced;

  const bars = steps.map((step, i) => {
    const bar = document.createElement("button");
    bar.type = "button";
    bar.setAttribute("aria-label", `Step ${i + 1}: ${step.querySelector("h3").textContent}`);
    bar.style.setProperty("--seconds", `${STEP_SECONDS[i] / SPEED}s`);
    bar.addEventListener("click", () => cell.seek(i));
    dots.append(bar);
    return bar;
  });

  function show(index) {
    steps.forEach((step, i) => step.classList.toggle("is-active", i === index));
    bars.forEach((bar, i) => {
      bar.classList.toggle("is-done", i < index);
      bar.classList.remove("is-active");
      if (i === index) bar.setAttribute("aria-current", "step");
      else bar.removeAttribute("aria-current");
    });
    void root.offsetWidth; // so the bar of the same step fills from the start again
    bars[index].classList.add("is-active");
  }

  const cell = createCell(canvas, { mode: "play", speed: SPEED, onStep: show });

  toggle.addEventListener("click", () => {
    playing = !playing;
    cell.setRunning(playing);
    root.classList.toggle("is-paused", !playing);
    toggle.textContent = playing ? "Pause" : "Play";
  });

  // With reduced motion nothing plays: each bar shows one still frame of its step.
  if (reduced) {
    toggle.hidden = true;
    root.classList.add("is-paused");
  }

  // The scene stops its clock while it is off screen. The bars wait with it.
  watchView(root, (seen) => root.classList.toggle("is-away", !seen));

  controls.hidden = false;
  show(0);
}
