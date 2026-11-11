// A small isometric drawing stage on a 2D canvas.
// It owns sizing, the projection, the frame loop and a few drawing helpers.
// Scenes pass a draw(stage, time) function and use the helpers.

export const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

export const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
export const lerp = (a, b, t) => a + (b - a) * t;
export const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
export const easeOut = (t) => 1 - Math.pow(1 - t, 3);
export const span = (t, a, b) => clamp((t - a) / (b - a));

function rgb(hex) {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function mix(a, b, t) {
  const p = rgb(a);
  const q = rgb(b);
  const c = p.map((v, i) => Math.round(lerp(v, q[i], t)));
  return `#${c.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

export const INK = "#0b0d12";

export function createStage(canvas, { units = 20, cx = 0.5, cy = 0.5, draw }) {
  const ctx = canvas.getContext("2d");
  const st = { ctx, W: 0, H: 0, s: 1, ox: 0, oy: 0, lift: 0, running: !reduced, time: 0 };
  let raf = 0;
  let last = 0;
  let visible = false;

  function size() {
    const r = canvas.getBoundingClientRect();
    if (!r.width) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(r.width * dpr);
    canvas.height = Math.round(r.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    st.W = r.width;
    st.H = r.height;
    st.s = r.width / units;
    st.ox = r.width * cx;
    st.oy = r.height * cy;
  }

  // World to screen. +x runs to the lower right, +y to the lower left, +z up.
  st.P = (x, y, z = 0) => [
    st.ox + (x - y) * st.s * 0.866,
    st.oy + (x + y) * st.s * 0.5 - (z + st.lift) * st.s,
  ];

  st.poly = (pts, fill, stroke = INK, width = 1.2) => {
    ctx.beginPath();
    pts.forEach(([x, y, z], i) => {
      const [sx, sy] = st.P(x, y, z);
      if (i) ctx.lineTo(sx, sy);
      else ctx.moveTo(sx, sy);
    });
    ctx.closePath();
    if (fill) {
      ctx.fillStyle = fill;
      ctx.fill();
    }
    if (stroke) {
      ctx.strokeStyle = stroke;
      ctx.lineWidth = width;
      ctx.lineJoin = "round";
      ctx.stroke();
    }
  };

  st.line = (a, b, stroke = INK, width = 1.2, dash) => {
    const [x1, y1] = st.P(...a);
    const [x2, y2] = st.P(...b);
    ctx.beginPath();
    ctx.moveTo(x1, y1);
    ctx.lineTo(x2, y2);
    ctx.strokeStyle = stroke;
    ctx.lineWidth = width;
    ctx.lineCap = "round";
    ctx.setLineDash(dash || []);
    ctx.stroke();
    ctx.setLineDash([]);
  };

  // A box with its three visible faces. The colour is the top face; the sides are shaded from it.
  st.box = (x, y, z, w, d, h, color, { stroke = INK, width = 1.2 } = {}) => {
    const x1 = x + w;
    const y1 = y + d;
    const z1 = z + h;
    st.poly([[x, y1, z], [x1, y1, z], [x1, y1, z1], [x, y1, z1]], mix(color, "#0b1230", 0.1), stroke, width);
    st.poly([[x1, y, z], [x1, y1, z], [x1, y1, z1], [x1, y, z1]], mix(color, "#0b1230", 0.22), stroke, width);
    st.poly([[x, y, z1], [x1, y, z1], [x1, y1, z1], [x, y1, z1]], color, stroke, width);
  };

  // A flat circle on the ground plane projects to this ellipse.
  st.disc = (x, y, z, r, fill, stroke = INK, width = 1.2) => {
    const [sx, sy] = st.P(x, y, z);
    ctx.beginPath();
    ctx.ellipse(sx, sy, r * st.s * 1.2247, r * st.s * 0.7071, 0, 0, Math.PI * 2);
    if (fill) {
      ctx.fillStyle = fill;
      ctx.fill();
    }
    if (stroke) {
      ctx.strokeStyle = stroke;
      ctx.lineWidth = width;
      ctx.stroke();
    }
  };

  st.cylinder = (x, y, z, r, h, color) => {
    const [sx, sy] = st.P(x, y, z);
    const rx = r * st.s * 1.2247;
    const ry = r * st.s * 0.7071;
    const hh = h * st.s;
    ctx.beginPath();
    ctx.ellipse(sx, sy, rx, ry, 0, 0, Math.PI);
    ctx.lineTo(sx - rx, sy - hh);
    ctx.ellipse(sx, sy - hh, rx, ry, 0, Math.PI, 0, true);
    ctx.closePath();
    ctx.fillStyle = mix(color, "#0b1230", 0.16);
    ctx.fill();
    ctx.strokeStyle = INK;
    ctx.lineWidth = 1.2;
    ctx.stroke();
    st.disc(x, y, z + h, r, color);
  };

  // A text label on a pill, joined to its anchor by a thin leader.
  st.tag = (text, anchor, dx, dy, { active = false, dim = false, size = 12 } = {}) => {
    const [ax, ay] = st.P(...anchor);
    const fs = st.W < 520 ? size - 1.5 : size;
    ctx.font = `600 ${fs}px Archivo, Arial, sans-serif`;
    const tw = ctx.measureText(text).width;
    const w = tw + 18;
    const h = fs + 13;
    const k = st.W < 520 ? 0.6 : 1;
    let lx = ax + dx * k;
    const ly = ay + dy * k;
    lx = clamp(lx, w / 2 + 4, st.W - w / 2 - 4);
    ctx.globalAlpha *= dim ? 0.35 : 1;
    ctx.beginPath();
    ctx.moveTo(ax, ay);
    ctx.lineTo(lx, ly);
    ctx.strokeStyle = active ? "#1f44ff" : "rgba(11,13,18,0.55)";
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(ax, ay, 2.6, 0, Math.PI * 2);
    ctx.fillStyle = active ? "#1f44ff" : INK;
    ctx.fill();
    ctx.beginPath();
    ctx.roundRect(lx - w / 2, ly - h / 2, w, h, h / 2);
    ctx.fillStyle = active ? "#1f44ff" : "#ffffff";
    ctx.fill();
    ctx.strokeStyle = active ? "#1f44ff" : "rgba(11,13,18,0.85)";
    ctx.lineWidth = 1.1;
    ctx.stroke();
    ctx.fillStyle = active ? "#ffffff" : INK;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, lx, ly + 0.5);
    return { x: lx, y: ly, w, h };
  };

  function frame(now) {
    raf = 0;
    // A frame's timestamp can be slightly earlier than the clock read that scheduled it.
    const dt = clamp((now - last) / 1000, 0, 0.05);
    last = now;
    if (st.running) st.time += dt;
    ctx.clearRect(0, 0, st.W, st.H);
    ctx.globalAlpha = 1;
    st.lift = 0;
    draw(st, st.time);
    if (visible && st.running) raf = requestAnimationFrame(frame);
  }

  // Draw one frame now. Scenes that follow the scroll position call this themselves.
  st.render = () => {
    if (raf) return;
    last = performance.now();
    raf = requestAnimationFrame(frame);
  };

  new ResizeObserver(() => {
    size();
    st.render();
  }).observe(canvas);

  new IntersectionObserver(
    ([entry]) => {
      visible = entry.isIntersecting;
      if (visible) st.render();
    },
    { rootMargin: "80px" },
  ).observe(canvas);

  size();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => st.render());
  return st;
}
