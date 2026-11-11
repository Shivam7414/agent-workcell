// Data guard: choose who is bound and what SQL the model sends, and watch where it stops.
// Each query and outcome matches a case in tests/security/test_attack_suite.py.

import { GUARD_QUERIES, GUARD_PEOPLE } from "./data.js";
import { el, sleep } from "./lib/dom.js";
import { reduced } from "./lib/iso.js";

const GATES = ["cursor", "grants", "rows"];

export function initGuard(root) {
  const people = root.querySelector("[data-gd-people]");
  const list = root.querySelector("[data-gd-queries]");
  const track = root.querySelector("[data-gd-track]");
  const verdict = root.querySelector("[data-gd-verdict]");
  const view = root.querySelector("[data-gd-view]");
  const gates = Object.fromEntries(GATES.map((id) => [id, track.querySelector(`[data-gate="${id}"]`)]));
  const out = track.querySelector(".gd__end--out");
  let run = 0;

  GUARD_QUERIES.forEach((query, i) => {
    const input = el("input", { type: "radio", name: "gd-query", value: query.id, checked: i === 0 });
    list.append(el("label", { class: "gd__q" }, input, el("code", { text: query.sql })));
  });

  const current = () => ({
    person: people.querySelector("input:checked").value,
    query: GUARD_QUERIES.find((q) => q.id === list.querySelector("input:checked").value),
  });

  function outcome(person, query) {
    if (query.stop) return { result: "refuse", stop: query.stop, title: query.title, text: query.text };
    const empty = query.rows[person].startsWith("No rows");
    return {
      result: "pass",
      stop: null,
      title: empty ? "Runs, and returns no rows" : "Runs, inside the row rule",
      text: [query.rows[person], query.extra].filter(Boolean).join(" "),
    };
  }

  function showView(person, query, active) {
    view.replaceChildren();
    query.view.forEach(([text, who], i) => {
      const on = active && who && who.split(" ").includes(person);
      view.append(on ? el("span", { class: "hl", text }) : text);
      if (i < query.view.length - 1) view.append("\n");
    });
  }

  function showVerdict(person, result) {
    verdict.dataset.result = result.result;
    verdict.replaceChildren(
      el("h4", { text: result.title }),
      el("p", { text: result.text }),
      el("p", { class: "dim", text: `Bound to the connection: ${GUARD_PEOPLE[person]}.` }),
    );
  }

  async function play() {
    const token = ++run;
    const { person, query } = current();
    const result = outcome(person, query);
    const steps = [...GATES.map((id) => gates[id]), out];
    steps.forEach((node) => node.classList.remove("is-reached", "is-pass", "is-stop", "is-skipped"));
    showView(person, query, false);

    if (reduced) {
      settle(result);
      showView(person, query, result.result === "pass");
      showVerdict(person, result);
      return;
    }

    verdict.dataset.result = "";
    verdict.replaceChildren(el("p", { class: "dim", text: "Running the query…" }));
    await sleep(60);
    for (const id of GATES) {
      if (token !== run) return;
      gates[id].classList.add("is-reached");
      await sleep(430);
      if (token !== run) return;
      if (result.stop === id) {
        gates[id].classList.add("is-stop");
        skipAfter(id);
        showVerdict(person, result);
        return;
      }
      gates[id].classList.add("is-pass");
      if (id === "rows") showView(person, query, true);
      await sleep(160);
    }
    if (token !== run) return;
    out.classList.add("is-reached");
    showVerdict(person, result);
  }

  function skipAfter(id) {
    const from = GATES.indexOf(id) + 1;
    GATES.slice(from).forEach((later) => gates[later].classList.add("is-skipped"));
    out.classList.add("is-skipped");
  }

  // The end state, with no animation.
  function settle(result) {
    for (const id of GATES) {
      gates[id].classList.add("is-reached");
      if (result.stop === id) {
        gates[id].classList.add("is-stop");
        skipAfter(id);
        return;
      }
      gates[id].classList.add("is-pass");
    }
    out.classList.add("is-reached");
  }

  root.addEventListener("change", play);

  // The first run waits until the figure is on screen.
  const watcher = new IntersectionObserver(
    ([entry]) => {
      if (!entry.isIntersecting) return;
      watcher.disconnect();
      play();
    },
    { threshold: 0.35 },
  );
  watcher.observe(track);
  const first = current();
  showView(first.person, first.query, false);
}
