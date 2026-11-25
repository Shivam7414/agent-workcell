// The small figures: when each one starts, and what answers a click.
// The drawing and the motion are in sections.css. This file only sets state.

import { reduced } from "./lib/iso.js";

// Calls back with true when the node comes into view and false when it leaves.
// "In view" means its top edge has passed the lower quarter of the window,
// so a figure taller than a phone screen still starts.
export function watchView(node, callback) {
  const watcher = new IntersectionObserver(
    (entries) => entries.forEach((entry) => callback(entry.isIntersecting)),
    { rootMargin: "0px 0px -25% 0px" },
  );
  watcher.observe(node);
}

// A figure marked data-inview holds its first frame until it is seen once.
export function initInView(figure) {
  watchView(figure, (seen) => {
    if (seen) figure.classList.add("is-in");
  });
}

// data-stagger="selector": number the matching items, so the style sheet can delay each one in turn.
export function initStagger(list) {
  list.querySelectorAll(list.dataset.stagger).forEach((item, i) => item.style.setProperty("--i", i));
}

// The data guard figure: step through the three people until the reader picks one.
export function initGuard(figure) {
  const people = [...figure.querySelectorAll("input[type=radio]")];
  let timer = null;
  let touched = false;

  const stop = () => {
    clearInterval(timer);
    timer = null;
  };

  figure.addEventListener("change", () => {
    touched = true;
    stop();
  });

  if (reduced) return;
  watchView(figure, (seen) => {
    stop();
    if (!seen || touched) return;
    timer = setInterval(() => {
      const next = (people.findIndex((person) => person.checked) + 1) % people.length;
      people[next].checked = true; // set from script, so no "change" event fires
    }, 2800);
  });
}

// The test cases: a square shows its question when it is pointed at, focused or pressed.
export function initCases(figure) {
  const line = figure.querySelector("[data-cases-q]");
  const squares = [...figure.querySelectorAll("button")];

  function show(square) {
    for (const other of squares) other.setAttribute("aria-pressed", String(other === square));
    const who = document.createElement("small");
    who.textContent = `${square.dataset.who}. ${square.dataset.state}.`;
    line.replaceChildren(who, square.dataset.q);
  }

  for (const square of squares) {
    square.setAttribute("aria-pressed", "false");
    square.addEventListener("click", () => show(square));
    square.addEventListener("focus", () => show(square));
    square.addEventListener("pointerenter", () => show(square));
  }
}
