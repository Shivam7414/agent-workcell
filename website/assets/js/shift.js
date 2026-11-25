// The hero scene: a custom pipeline becomes one reused agent in an engineered environment.
//
// Twelve blocks start as one chained line. Four are set aside, four stack into the reused runtime,
// and the rest move outward to become the environment. The scene plays once when the page loads.
// After that the slider under it moves the scene by hand.

import { createStage, clamp, lerp, ease, span, mix, INK, reduced } from "./lib/iso.js";

const GREY = "#cdd3de";
const ORANGE = "#ff5b24";
const BLUE = "#1f44ff";
const HEIGHT = 0.72;
const HOLD = 2.4; // seconds the pipeline runs before it changes
const MOVE = 5.2; // seconds the change takes

// "to" is where a block ends; "tag" is where its label sits, in pixels from the block.
const BLOCKS = [
  { a: "Identity checks", b: "Identity", kind: "env", to: [-1.9, 4.1], tag: [-62, 18] },
  { a: "Intent detection", kind: "drop", to: [3.95, 3.95], tag: [-132, -18] },
  { a: "Question routing", kind: "drop", to: [5.0, 3.95], tag: [118, -14] },
  { a: "Prompt per question type", kind: "drop", to: [3.95, 5.0], tag: [-128, 30] },
  { a: "Query templates", kind: "drop", to: [5.0, 5.0], tag: [104, 32] },
  { a: "Context management", kind: "runtime", level: 0 },
  { a: "Retrieval pipeline", b: "Knowledge graph", kind: "env", to: [-4.3, 0.9], tag: [-58, -34] },
  { a: "Tool orchestration", kind: "runtime", level: 1 },
  { a: "Access control", b: "Data guard", kind: "env", to: [-0.9, -4.3], tag: [52, -36] },
  { a: "Multi-step execution", kind: "runtime", level: 2 },
  { a: "Retries", kind: "runtime", level: 3 },
  { a: "Logging", b: "Audit", kind: "env", to: [2.9, -3.1], tag: [58, -22] },
  { b: "Tools over MCP", kind: "new", to: [-4.7, -1.6], tag: [-46, -38] },
  { b: "Evaluation", kind: "new", to: [4.3, 0.8], tag: [60, 18] },
];

// The starting line: twelve stages in a row. Labels alternate sides so they stay readable.
const LINE = BLOCKS.filter((block) => block.a);
LINE.forEach((block, i) => {
  block.from = [-5.17 + i * 0.94, 0];
  block.tagA = i % 2 ? [-78, 34] : [78, -34];
});

export function initShift(root) {
  const canvas = root.querySelector("[data-shift-canvas]");
  const range = root.querySelector("[data-shift-range]");
  const replay = root.querySelector("[data-shift-replay]");
  const full = Number(range.max);

  let auto = !reduced; // the load sequence is running
  let from = 0; // stage time at which the load sequence started
  let target = reduced ? Number(range.value) / full : 0;
  let p = target;
  let lastTime = 0;

  const st = createStage(canvas, { units: 20.6, cx: 0.5, cy: 0.5, draw });

  range.addEventListener("input", () => {
    auto = false;
    target = Number(range.value) / full;
    st.render();
  });

  if (replay && !reduced) {
    replay.hidden = false;
    replay.addEventListener("click", () => {
      auto = true;
      from = st.time;
      st.render();
    });
  }

  function draw(stage, time) {
    const { ctx } = stage;
    const dt = clamp(time - lastTime, 0, 0.05);
    lastTime = time;

    if (auto) {
      const u = span(time - from, HOLD, HOLD + MOVE);
      target = p = ease(u);
      range.value = String(Math.round(p * full));
      if (u >= 1) auto = false;
    } else {
      p = reduced ? target : p + (target - p) * Math.min(1, dt * 7);
      if (Math.abs(target - p) < 0.0005) p = target;
    }
    root.dataset.state = p < 0.5 ? "before" : "after";

    // Floor.
    stage.poly([[-5.9, -5.9, -0.75], [6.1, -5.9, -0.75], [6.1, 6.1, -0.75], [-5.9, 6.1, -0.75]], "rgba(24,36,90,0.07)", null);
    stage.box(-5.6, -5.6, -0.3, 11.2, 11.2, 0.3, "#ffffff");
    for (let i = -5; i <= 5; i++) {
      stage.line([i, -5.6, 0], [i, 5.6, 0], "#eaedf3", 1);
      stage.line([-5.6, i, 0], [5.6, i, 0], "#eaedf3", 1);
    }

    // Before: the stages are chained, and a request works its way through all of them.
    const chain = 1 - span(p, 0, 0.22);
    if (chain > 0) {
      ctx.globalAlpha = chain;
      const pts = LINE.map((b) => [b.from[0], b.from[1], 0.02]);
      for (let i = 0; i < pts.length - 1; i++) stage.line(pts[i], pts[i + 1], "#9aa3b5", 7);
      const s = ((time * 0.16) % 1) * (pts.length - 1);
      const i = Math.floor(s);
      const u = ease(s - i);
      const a = pts[i];
      const b = pts[Math.min(i + 1, pts.length - 1)];
      const [dx, dy] = stage.P(lerp(a[0], b[0], u), lerp(a[1], b[1], u), HEIGHT + 0.5 + Math.sin(Math.PI * u) * 0.5);
      ctx.beginPath();
      ctx.arc(dx, dy, 0.2 * stage.s, 0, Math.PI * 2);
      ctx.fillStyle = ORANGE;
      ctx.fill();
      ctx.strokeStyle = INK;
      ctx.lineWidth = 1.2;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }

    // After: the cell boundary, and the lines from the runtime to what it may use.
    const cell = span(p, 0.72, 1);
    if (cell > 0) {
      ctx.globalAlpha = cell;
      ctx.setLineDash([5, 7]);
      stage.disc(0, 0, 0.01, 5.15, null, "#e0a800", 2);
      ctx.setLineDash([]);
      for (const b of BLOCKS) {
        if (b.kind === "env" || b.kind === "new") stage.line([0, 0, 0.02], [b.to[0], b.to[1], 0.02], "rgba(31,68,255,0.35)", 1.6, [2, 6]);
      }
      // After: the request goes round between the runtime and one part of the environment at a time.
      if (cell >= 1) {
        const parts = BLOCKS.filter((b) => b.kind === "env" || b.kind === "new");
        const s = (time * 0.55) % parts.length;
        const to = parts[Math.floor(s)].to;
        const u = Math.sin(Math.PI * (s % 1));
        const [dx, dy] = stage.P(to[0] * u * 0.86, to[1] * u * 0.86, 0.3);
        ctx.beginPath();
        ctx.arc(dx, dy, 0.16 * stage.s, 0, Math.PI * 2);
        ctx.fillStyle = ORANGE;
        ctx.fill();
        ctx.strokeStyle = INK;
        ctx.lineWidth = 1.2;
        ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }

    // Where each block is now.
    const items = BLOCKS.map((b, i) => {
      const u = ease(span(p, 0.06 + i * 0.024, 0.62 + i * 0.024));
      if (b.kind === "runtime") {
        return { b, u, x: lerp(b.from[0], 0, u), y: lerp(b.from[1], 0, u), z: lerp(0, b.level * (HEIGHT + 0.04), u) + Math.sin(Math.PI * u) * 1.6, h: HEIGHT, size: lerp(0.8, 1.05, u), color: mix(GREY, ORANGE, span(u, 0.35, 0.8)), alpha: 1 };
      }
      if (b.kind === "drop") {
        return { b, u, x: lerp(b.from[0], b.to[0], u), y: lerp(b.from[1], b.to[1], u), z: Math.sin(Math.PI * u) * 1.3, h: lerp(HEIGHT, 0.07, span(u, 0.5, 1)), size: 0.8, color: mix(GREY, "#ffffff", span(u, 0.4, 0.9)), alpha: lerp(1, 0.55, u), dashed: u > 0.6 };
      }
      if (b.kind === "new") {
        const v = ease(span(p, 0.62, 0.95));
        return { b, u: v, x: b.to[0], y: b.to[1], z: 0, h: HEIGHT * v, size: 1, color: BLUE, alpha: v };
      }
      return { b, u, x: lerp(b.from[0], b.to[0], u), y: lerp(b.from[1], b.to[1], u), z: Math.sin(Math.PI * u) * 0.9, h: HEIGHT, size: lerp(0.8, 1, u), color: mix(GREY, BLUE, span(u, 0.35, 0.8)), alpha: 1 };
    });

    // Far blocks first, and lower blocks before the ones stacked on them.
    items.sort((m, n) => m.x + m.y - (n.x + n.y) || m.z - n.z);
    for (const it of items) {
      if (it.alpha <= 0.01 || it.h <= 0.005) continue;
      ctx.globalAlpha = it.alpha;
      if (it.dashed) ctx.setLineDash([4, 4]);
      stage.box(it.x - it.size / 2, it.y - it.size / 2, it.z, it.size, it.size, it.h, it.color);
      ctx.setLineDash([]);
    }
    ctx.globalAlpha = 1;

    // Labels: the old name fades as a block leaves the line, the new name appears as it lands.
    // A narrow canvas has no room for twelve names on the line, so it names the line once
    // and leaves the names of the four set-aside blocks to the key under the scene.
    const small = stage.W < 600;
    for (const it of items) {
      const { b, u } = it;
      const top = [it.x, it.y, it.z + it.h];
      const before = b.a && !small ? 1 - span(u, 0.12, 0.4) : 0;
      if (before > 0.01) {
        ctx.globalAlpha = before;
        stage.tag(b.a, top, b.tagA[0], b.tagA[1]);
      }
      const after = b.kind === "drop" ? (small ? 0 : span(u, 0.7, 1) * 0.75) : b.kind === "runtime" ? 0 : span(u, 0.72, 1);
      if (after > 0.01) {
        ctx.globalAlpha = after;
        stage.tag(b.kind === "drop" ? b.a : b.b, top, b.tag[0], b.tag[1], { active: b.kind !== "drop" });
      }
    }
    if (small && chain > 0.01) {
      ctx.globalAlpha = chain;
      stage.tag("A custom pipeline: twelve stages", [0, 0, HEIGHT], 0, -70);
    }
    const tower = span(p, 0.8, 1);
    if (tower > 0.01) {
      ctx.globalAlpha = tower;
      stage.tag(small ? "Claude Code" : "Claude Code, reused", [0, 0, 4 * (HEIGHT + 0.04)], small ? 0 : 24, -36, { size: 13 });
    }
    ctx.globalAlpha = 1;
  }
}
