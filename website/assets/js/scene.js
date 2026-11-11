// The workcell, drawn as a live isometric scene.
//
// mode "play":    the arm carries one question through the cell, in a loop. Used by the hero.
// mode "explore": the cell stands still and each part can be selected. Used by the architecture section.
//
// Every object stands for a real part of the project. The names match docs/architecture.md.

import { createStage, clamp, lerp, ease, easeOut, span, mix, INK, reduced } from "./lib/iso.js";

const C = {
  floor: "#ffffff",
  grid: "#e6e9f0",
  steel: "#d9dee8",
  belt: "#2a2f3b",
  blue: "#1f44ff",
  cyan: "#19c3e6",
  orange: "#ff5b24",
  yellow: "#ffc400",
  row: "#e4e8f0",
};

const L1 = 2.7;
const L2 = 2.5;
const H0 = 1.15;
const HOME = [1.5, 1.7, 2.5];
const AT = {
  card: [-0.6, 2.9, 1.05],
  board: [-3.95, -1.0, 1.7],
  rack: [2.9, -2.41, 1.3],
  gate: [0, -4.3, 1.35],
  tray: [0, -4.62, 1.2],
  out: [3.3, 0, 1.05],
};

// Where the arm's tip is at each moment of the loop.
const LOOP = 15;
const INTRO = 2.3;
const KEYS = [
  [0, HOME], [1.2, HOME], [2.4, AT.card], [3.0, AT.card], [4.2, AT.board], [5.0, AT.board],
  [6.2, AT.rack], [6.6, AT.rack], [7.9, AT.gate], [9.3, AT.gate], [9.7, AT.tray], [10.0, AT.tray],
  [11.3, AT.out], [11.7, AT.out], [12.9, AT.rack], [13.3, AT.rack], [14.4, HOME], [LOOP, HOME],
];
const STEP_AT = [0, 3.0, 5.6, 7.9, 11.0];

export const PARTS = [
  { id: "host", label: "Host application", a: [-0.6, 6.7, 0.55], d: [-44, 40] },
  { id: "server", label: "Answer server", a: [-3.85, 3.05, 2.3], d: [-62, -40] },
  { id: "runtime", label: "Claude Code", a: [0, 0, 0.35], d: [74, 62] },
  { id: "knowledge", label: "Knowledge graph", a: [-4.36, -1.0, 2.85], d: [-58, -44] },
  { id: "tools", label: "Tools over MCP", a: [2.9, -3.3, 1.0], d: [96, -30] },
  { id: "guard", label: "Data guard", a: [1.3, -5, 2.8], d: [84, -34] },
  { id: "db", label: "Business database", a: [-0.6, -7.0, 2.5], d: [-104, -22] },
  { id: "audit", label: "Audit", a: [4.05, 2.05, 0.95], d: [70, 30] },
  { id: "memory", label: "Memory", a: [-3.95, -3.95, 1.0], d: [-66, -34], minor: true },
  { id: "eval", label: "Evaluation", a: [4.2, 6.6, 1.95], d: [70, 10], minor: true },
];

function tipAt(t) {
  for (let i = 0; i < KEYS.length - 1; i++) {
    const [t0, a] = KEYS[i];
    const [t1, b] = KEYS[i + 1];
    if (t <= t1) {
      const u = ease(span(t, t0, t1));
      const arc = a === b ? 0 : Math.sin(Math.PI * u) * 0.7;
      return [lerp(a[0], b[0], u), lerp(a[1], b[1], u), lerp(a[2], b[2], u) + arc];
    }
  }
  return HOME;
}

// Two-link arm: turn to face the target, then bend shoulder and elbow to reach it.
function solve([x, y, z]) {
  const yaw = Math.atan2(y, x);
  let r = Math.hypot(x, y);
  let dz = z - H0;
  let d = Math.hypot(r, dz);
  const max = L1 + L2 - 0.05;
  if (d > max) {
    r *= max / d;
    dz *= max / d;
    d = max;
  }
  const bend = Math.acos(clamp((L1 * L1 + d * d - L2 * L2) / (2 * L1 * d), -1, 1));
  const phi = Math.atan2(dz, r) + bend;
  const er = L1 * Math.cos(phi);
  const c = Math.cos(yaw);
  const s = Math.sin(yaw);
  return { elbow: [c * er, s * er, H0 + L1 * Math.sin(phi)], tip: [c * r, s * r, H0 + dz] };
}

function limb(st, a, b, w, color) {
  const { ctx } = st;
  const [x1, y1] = st.P(...a);
  const [x2, y2] = st.P(...b);
  const px = w * st.s;
  ctx.lineCap = "round";
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.strokeStyle = INK;
  ctx.lineWidth = px + 2.6;
  ctx.stroke();
  ctx.strokeStyle = color;
  ctx.lineWidth = px;
  ctx.stroke();
  ctx.beginPath();
  ctx.moveTo(x1, y1 - px * 0.22);
  ctx.lineTo(x2, y2 - px * 0.22);
  ctx.strokeStyle = "rgba(255,255,255,0.4)";
  ctx.lineWidth = px * 0.2;
  ctx.stroke();
}

function joint(st, p, r) {
  const { ctx } = st;
  const [x, y] = st.P(...p);
  ctx.beginPath();
  ctx.arc(x, y, r * st.s, 0, Math.PI * 2);
  ctx.fillStyle = INK;
  ctx.fill();
  ctx.beginPath();
  ctx.arc(x, y, r * st.s * 0.42, 0, Math.PI * 2);
  ctx.fillStyle = "#ffffff";
  ctx.fill();
}

function fence(st, [ax, ay], [bx, by], h) {
  const len = Math.hypot(bx - ax, by - ay);
  const at = (u, z) => [lerp(ax, bx, u), lerp(ay, by, u), z];
  st.poly([at(0, 0), at(1, 0), at(1, h), at(0, h)], "rgba(255,196,0,0.08)", null);
  const m = Math.max(2, Math.round(len / 0.5));
  for (let i = 0; i < m; i++) {
    st.line(at(i / m, 0), at((i + 1) / m, h), "rgba(11,13,18,0.14)", 0.8);
    st.line(at((i + 1) / m, 0), at(i / m, h), "rgba(11,13,18,0.14)", 0.8);
  }
  st.line(at(0, h), at(1, h), INK, 3.6);
  st.line(at(0, h), at(1, h), C.yellow, 2);
  const n = Math.max(1, Math.round(len / 2.5));
  for (let i = 0; i <= n; i++) {
    st.line(at(i / n, 0), at(i / n, h + 0.1), INK, 5);
    st.line(at(i / n, 0), at(i / n, h + 0.1), C.yellow, 3);
  }
}

function belt(st, x, y, w, d, time, along) {
  st.box(x, y, 0, w, d, 0.5, C.steel);
  const m = 0.14;
  st.poly([[x + m, y + m, 0.5], [x + w - m, y + m, 0.5], [x + w - m, y + d - m, 0.5], [x + m, y + d - m, 0.5]], C.belt, null);
  const len = along === "y" ? d : w;
  for (let k = 0; k < len / 0.8; k++) {
    const p = (k * 0.8 + time * 0.9) % (len - 2 * m);
    const a = along === "y" ? [x + m + 0.1, y + d - m - p, 0.5] : [x + m + p, y + m + 0.1, 0.5];
    const b = along === "y" ? [x + w - m - 0.1, y + d - m - p, 0.5] : [x + m + p, y + d - m - 0.1, 0.5];
    st.line(a, b, "rgba(255,255,255,0.16)", 1.2);
  }
}

// A flat card: the question on its way in, the answer on its way out.
function card(st, x, y, z, stripe, tagged) {
  st.box(x - 0.45, y - 0.32, z, 0.9, 0.64, 0.07, "#ffffff");
  st.poly([[x - 0.3, y - 0.18, z + 0.07], [x + 0.3, y - 0.18, z + 0.07], [x + 0.3, y - 0.04, z + 0.07], [x - 0.3, y - 0.04, z + 0.07]], stripe, null);
  st.line([x - 0.3, y + 0.12, z + 0.07], [x + 0.14, y + 0.12, z + 0.07], "rgba(11,13,18,0.35)", 1.2);
  if (tagged) st.box(x + 0.18, y + 0.06, z + 0.07, 0.24, 0.24, 0.1, C.blue);
}

export function createCell(canvas, { mode = "play", onStep, onSelect } = {}) {
  const play = mode === "play";
  let selected = null;
  let hover = null;
  let step = -1;
  let hits = [];

  const st = createStage(canvas, { units: 21, cx: 0.5, cy: 0.555, draw });
  if (reduced) st.time = INTRO + 8.9;

  function draw(stage, time) {
    const { ctx } = stage;
    const t = play ? (time < INTRO ? 0 : (time - INTRO) % LOOP) : 0;
    const enter = (i) => easeOut(span(time, 0.1 + i * 0.11, 0.75 + i * 0.11));
    const focus = hover || selected;
    // Parts are queued in drawing order, far to near, and drawn at the end.
    const queue = [];
    const part = (i, id, fn) => queue.push([i, id, fn]);
    const paint = ([i, , fn]) => {
      const u = enter(i);
      if (u <= 0) return;
      ctx.globalAlpha = u;
      stage.lift = (1 - u) * 2.4;
      fn();
      ctx.globalAlpha = 1;
      stage.lift = 0;
    };

    if (play) {
      const now = STEP_AT.filter((s) => t >= s).length - 1;
      if (now !== step && time >= INTRO) {
        step = now;
        if (onStep) onStep(step);
      }
    }

    // Moments of the loop, each from 0 to 1.
    const scan = span(t, 0.8, 1.3) * (1 - span(t, 1.5, 2.0));
    const read = span(t, 4.2, 5.0);
    const boardOn = read * (1 - span(t, 5.6, 6.2));
    const holdTool = play && t >= 6.4 && t < 13.1;
    const gateOn = span(t, 8.2, 8.6) * (1 - span(t, 10.2, 10.8));
    const rowsOn = span(t, 8.3, 8.8) * (1 - span(t, 10.6, 11.4));
    const slide = ease(span(t, 8.7, 9.5));
    const holdRow = play && t >= 9.85 && t < 11.5;
    const leave = ease(span(t, 11.7, 13.4));
    const tape = span(t, 11.7, 12.4) * (1 - span(t, 14.3, 14.9));

    // Soft shadow under the whole cell.
    part(0, null, () => {
      stage.poly([[-5.3, -5.3, -0.7], [5.5, -5.3, -0.7], [5.5, 5.5, -0.7], [-5.3, 5.5, -0.7]], "rgba(24,36,90,0.08)", null);
    });

    // The business database stands outside the cell, behind the guard.
    part(4, "db", () => {
      stage.box(-1.85, -8.05, 0, 3.7, 2.2, 0.16, C.steel);
      for (let i = 0; i < 7; i++) {
        const allowed = i === 1 || i === 3 || i === 4;
        stage.box(-1.5, -7.75, 0.16 + i * 0.33, 3, 1.5, 0.27, allowed ? mix(C.row, C.cyan, rowsOn) : C.row);
      }
    });

    // Floor, with the reach of the arm marked on it.
    part(0, null, () => {
      stage.box(-5, -5, -0.35, 10, 10, 0.35, C.floor);
      for (let i = -4; i <= 4; i++) {
        stage.line([i, -5, 0], [i, 5, 0], C.grid, 1);
        stage.line([-5, i, 0], [5, i, 0], C.grid, 1);
      }
      ctx.setLineDash([3, 6]);
      stage.disc(0, 0, 0, 4.82, null, "rgba(255,91,36,0.55)", 1.3);
      ctx.setLineDash([]);
    });

    // Back fences and the one gate: the data guard.
    part(1, "guard", () => {
      fence(stage, [-5, -5], [-5, 5], 2.1);
      fence(stage, [-5, -5], [-1.42, -5], 2.1);
      fence(stage, [1.42, -5], [5, -5], 2.1);
    });
    part(5, "guard", () => {
      stage.poly([[-1.2, -5, 1.4], [1.2, -5, 1.4], [1.2, -5, 2.46], [-1.2, -5, 2.46]], `rgba(25,195,230,${0.14 + gateOn * 0.3})`, "rgba(11,13,18,0.5)", 1);
      stage.box(-1.44, -5.1, 0, 0.24, 0.24, 2.8, C.yellow);
      stage.box(1.2, -5.1, 0, 0.24, 0.24, 2.8, C.yellow);
      stage.box(-1.44, -5.1, 2.46, 2.88, 0.24, 0.36, C.yellow);
      for (let i = 0; i < 6; i++) {
        const x = -1.3 + i * 0.46;
        stage.poly([[x, -4.86, 2.46], [x + 0.2, -4.86, 2.46], [x + 0.4, -4.86, 2.82], [x + 0.2, -4.86, 2.82]], INK, null);
      }
      stage.box(-0.8, -4.96, 0.92, 1.6, 0.62, 0.1, C.steel);
      const [lx, ly] = stage.P(0, -4.86, 3.02);
      ctx.beginPath();
      ctx.arc(lx, ly, 0.13 * stage.s, 0, Math.PI * 2);
      ctx.fillStyle = mix("#ffffff", C.cyan, gateOn);
      ctx.fill();
      ctx.strokeStyle = INK;
      ctx.lineWidth = 1.2;
      ctx.stroke();
    });

    // Memory: a small drawer of notes.
    part(3, "memory", () => {
      stage.box(-4.5, -4.5, 0, 1.1, 1.1, 0.95, C.steel);
      stage.box(-4.32, -4.3, 0.95, 0.4, 0.4, 0.05, C.yellow);
      stage.box(-3.98, -4.02, 0.95, 0.4, 0.4, 0.05, "#ffe27a");
    });

    // Knowledge graph: a board the arm can read.
    part(3, "knowledge", () => {
      stage.line([-4.36, -2.3, 0], [-4.36, -2.3, 0.6], INK, 3);
      stage.line([-4.36, 0.3, 0], [-4.36, 0.3, 0.6], INK, 3);
      stage.box(-4.45, -2.6, 0.55, 0.18, 3.2, 2.3, "#ffffff");
      const n = [[-2.25, 2.45], [-1.05, 2.5], [0.15, 2.3], [-2.05, 1.25], [-0.85, 1.15], [0.2, 1.5]];
      const edges = [[0, 1], [1, 2], [0, 3], [1, 4], [3, 4], [4, 5], [5, 2]];
      edges.forEach(([a, b], i) => {
        const lit = i >= 4 ? span(boardOn, (i - 4) * 0.3, (i - 4) * 0.3 + 0.4) : 0;
        stage.line([-4.26, n[a][0], n[a][1]], [-4.26, n[b][0], n[b][1]], lit > 0 ? mix("#b9c1d0", C.blue, lit) : "#b9c1d0", 1.4 + lit * 1.4);
      });
      n.forEach(([y, z], i) => {
        const [sx, sy] = stage.P(-4.26, y, z);
        const lit = i >= 2 ? boardOn : 0;
        ctx.beginPath();
        ctx.arc(sx, sy, 0.13 * stage.s, 0, Math.PI * 2);
        ctx.fillStyle = mix("#ffffff", C.blue, lit);
        ctx.fill();
        ctx.strokeStyle = lit > 0.5 ? C.blue : INK;
        ctx.lineWidth = 1.2;
        ctx.stroke();
      });
    });

    // Tool rack: five tools, and nothing else in reach.
    part(4, "tools", () => {
      stage.box(2.5, -3.75, 0, 0.8, 2.75, 0.75, C.steel);
      for (let i = 0; i < 5; i++) {
        if (i === 2 && holdTool) {
          stage.poly([[2.69, -2.62, 0.75], [3.11, -2.62, 0.75], [3.11, -2.2, 0.75], [2.69, -2.2, 0.75]], "rgba(11,13,18,0.12)", null);
        } else {
          stage.box(2.69, -3.66 + i * 0.52, 0.75, 0.42, 0.42, 0.42, i === 2 ? C.cyan : C.blue);
        }
      }
    });

    // Answer server: the controller that knows who is asking.
    part(2, "server", () => {
      stage.box(-4.5, 2.3, 0, 1.3, 1.5, 2.3, C.blue);
      for (let i = 0; i < 3; i++) {
        const [sx, sy] = stage.P(-3.2, 2.65 + i * 0.3, 1.95);
        ctx.beginPath();
        ctx.arc(sx, sy, 0.07 * stage.s, 0, Math.PI * 2);
        ctx.fillStyle = i === 0 && scan > 0.3 ? C.cyan : "#ffffff";
        ctx.fill();
      }
      stage.poly([[-3.2, 2.55, 0.5], [-3.2, 3.55, 0.5], [-3.2, 3.55, 1.5], [-3.2, 2.55, 1.5]], "rgba(255,255,255,0.16)", null);
    });

    // Host application: the question arrives with the person's identity.
    part(2, "host", () => {
      belt(stage, -1.2, 2.4, 1.2, 5.4, play ? time : 0, "y");
      stage.box(-1.52, 4.93, 0, 0.14, 0.14, 1.9, C.blue);
      stage.box(0.18, 4.93, 0, 0.14, 0.14, 1.9, C.blue);
      stage.box(-1.52, 4.93, 1.76, 1.84, 0.14, 0.2, mix(C.blue, C.cyan, scan));
      if (scan > 0) {
        stage.poly([[-1.2, 5, 0.5], [0, 5, 0.5], [0, 5, 1.76], [-1.2, 5, 1.76]], `rgba(25,195,230,${scan * 0.28})`, null);
      }
      const gone = span(t, 13.6, 14.4);
      const cy = play ? lerp(7.6, 2.9, easeOut(span(t, 0, 2.0))) : 3.6;
      if (gone < 1) {
        ctx.globalAlpha = enter(2) * (1 - gone);
        card(stage, -0.6, cy, 0.5, C.blue, !play || t > 1.1);
        ctx.globalAlpha = enter(2);
      }
    });

    // The way out, and the audit recorder beside it.
    part(5, "audit", () => {
      belt(stage, 2.7, -0.6, 5.1, 1.2, play ? time : 0, "x");
      stage.box(3.6, 1.6, 0, 0.9, 0.9, 0.9, C.steel);
      const h = play ? tape * 1.1 : 0.9;
      if (h > 0.02) {
        stage.poly([[4.05, 1.78, 0.9], [4.05, 2.32, 0.9], [4.05, 2.32, 0.9 + h], [4.05, 1.78, 0.9 + h]], "#ffffff", INK, 1);
        for (let z = 0.16; z < h - 0.05; z += 0.18) {
          stage.line([4.05, 1.88, 0.9 + z], [4.05, 2.22, 0.9 + z], "rgba(11,13,18,0.45)", 1);
        }
      }
    });
    part(5, null, () => {
      if (!(play && t >= 11.5 && leave < 1)) return;
      ctx.globalAlpha = 1 - span(leave, 0.8, 1);
      card(stage, lerp(3.3, 7.3, leave), 0, 0.5, C.cyan, false);
    });

    // The row that the guard lets through.
    part(5, null, () => {
      if (play && slide > 0 && t < 9.85) stage.box(-0.6, lerp(-7.4, -5.0, slide), 1.02, 1.2, 0.75, 0.2, C.cyan);
    });

    // Claude Code, the reused agent runtime: an arm.
    const tip = play ? tipAt(t) : [2.5 + Math.sin(time * 0.6) * 0.2, 0.9, 1.7 + Math.sin(time * 0.9) * 0.12];
    const arm = solve(tip);
    part(1, "runtime", () => {
      stage.cylinder(0, 0, 0, 0.78, 0.3, "#2a2f3b");
      stage.cylinder(0, 0, 0.3, 0.46, H0 - 0.45, C.orange);
      limb(stage, [0, 0, H0], arm.elbow, 0.44, C.orange);
      limb(stage, arm.elbow, arm.tip, 0.32, C.orange);
      joint(stage, [0, 0, H0], 0.3);
      joint(stage, arm.elbow, 0.25);
      const [gx, gy, gz] = arm.tip;
      if (holdRow) stage.box(gx - 0.6, gy - 0.38, gz - 0.42, 1.2, 0.75, 0.2, C.cyan);
      if (holdTool) stage.box(gx - 0.21, gy - 0.21, gz - 0.22, 0.42, 0.42, 0.42, C.cyan);
      else stage.box(gx - 0.14, gy - 0.14, gz - 0.2, 0.28, 0.28, 0.2, "#2a2f3b");
      joint(stage, arm.tip, 0.17);
    });

    // Low front rails, so the cell stays open to view.
    part(1, "guard", () => {
      fence(stage, [-5, 5], [-1.6, 5], 0.7);
      fence(stage, [0.35, 5], [5, 5], 0.7);
      fence(stage, [5, -5], [5, -0.85], 0.7);
      fence(stage, [5, 0.85], [5, 5], 0.7);
    });

    // Evaluation watches from outside, as a host application would.
    part(6, "eval", () => {
      stage.line([4.2, 6.6, 0], [4.2, 6.6, 1.6], INK, 3);
      stage.line([3.8, 6.9, 0], [4.2, 6.6, 0.5], INK, 2);
      stage.line([4.6, 6.4, 0], [4.2, 6.6, 0.5], INK, 2);
      stage.box(3.92, 6.32, 1.6, 0.56, 0.56, 0.36, "#2a2f3b");
      if (focus === "eval") stage.line([4.2, 6.32, 1.78], [3.4, 0.4, 0.7], C.blue, 1.4, [4, 5]);
    });

    // Draw everything. With a part in focus, veil the scene and draw that part again on top.
    queue.forEach(paint);
    if (focus) {
      ctx.fillStyle = "rgba(255,255,255,0.76)";
      ctx.fillRect(0, 0, stage.W, stage.H);
      queue.filter((entry) => entry[1] === focus).forEach(paint);
    }

    // Labels.
    hits = [];
    const small = stage.W < 520;
    const labelIn = enter(9);
    if (labelIn > 0) {
      for (const p of PARTS) {
        if (play && p.minor) continue;
        if (play && small && !["runtime", "guard", "db", "knowledge"].includes(p.id)) continue;
        ctx.globalAlpha = labelIn;
        const text = play && p.id === "runtime" ? "Claude Code, reused" : p.label;
        const box = stage.tag(text, p.a, p.d[0], p.d[1], { active: focus === p.id, dim: !!focus && focus !== p.id });
        const [ax, ay] = stage.P(...p.a);
        hits.push({ id: p.id, ax, ay, ...box });
        ctx.globalAlpha = 1;
      }
    }
  }

  function pick(event) {
    const r = canvas.getBoundingClientRect();
    const x = event.clientX - r.left;
    const y = event.clientY - r.top;
    let best = null;
    let bestD = 46;
    for (const h of hits) {
      if (Math.abs(x - h.x) < h.w / 2 + 4 && Math.abs(y - h.y) < h.h / 2 + 4) return h.id;
      const d = Math.hypot(x - h.ax, y - h.ay);
      if (d < bestD) {
        bestD = d;
        best = h.id;
      }
    }
    return best;
  }

  if (!play) {
    canvas.addEventListener("pointermove", (event) => {
      const id = pick(event);
      if (id !== hover) {
        hover = id;
        canvas.classList.toggle("is-pointing", !!id);
        st.render();
      }
    });
    canvas.addEventListener("pointerleave", () => {
      hover = null;
      st.render();
    });
    canvas.addEventListener("click", (event) => {
      const id = pick(event);
      if (id && onSelect) onSelect(id);
    });
  }

  return {
    select(id) {
      selected = id;
      st.render();
    },
  };
}
