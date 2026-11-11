// Draws connectors between HTML elements, as SVG paths laid over their container.
// The paths are measured from the live layout, so they stay right at every width.
//
// route "v":  straight down or up, where the two elements overlap horizontally
// route "h":  straight across, where they overlap vertically
// route "hv": leave the first element sideways, arrive at the second from above or below

const NS = "http://www.w3.org/2000/svg";
const R = 12;

function box(node, origin) {
  const r = node.getBoundingClientRect();
  const x = r.left - origin.left;
  const y = r.top - origin.top;
  return { x, y, r: x + r.width, b: y + r.height, cx: x + r.width / 2, cy: y + r.height / 2 };
}

function path(a, b, route) {
  if (route === "v") {
    const x = (Math.max(a.x, b.x) + Math.min(a.r, b.r)) / 2;
    return a.cy < b.cy ? `M${x} ${a.b} V${b.y}` : `M${x} ${a.y} V${b.b}`;
  }
  if (route === "h") {
    const y = (Math.max(a.y, b.y) + Math.min(a.b, b.b)) / 2;
    return a.cx < b.cx ? `M${a.r} ${y} H${b.x}` : `M${a.x} ${y} H${b.r}`;
  }
  const right = b.cx > a.cx;
  const down = b.cy > a.cy;
  const x0 = right ? a.r : a.x;
  const y1 = down ? b.y : b.b;
  const sx = right ? 1 : -1;
  const sy = down ? 1 : -1;
  return `M${x0} ${a.cy} H${b.cx - sx * R} Q${b.cx} ${a.cy} ${b.cx} ${a.cy + sy * R} V${y1}`;
}

export function createWires(container, svg, edges) {
  const paths = new Map();
  for (const edge of edges) {
    const node = document.createElementNS(NS, "path");
    node.setAttribute("class", "wire");
    svg.append(node);
    paths.set(edge.id, node);
  }

  function layout() {
    const origin = container.getBoundingClientRect();
    if (!origin.width) return;
    for (const edge of edges) {
      const a = box(container.querySelector(edge.from), origin);
      const b = box(container.querySelector(edge.to), origin);
      paths.get(edge.id).setAttribute("d", path(a, b, edge.route));
    }
  }

  new ResizeObserver(layout).observe(container);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(layout);
  layout();

  return {
    get: (id) => paths.get(id),
    // Set the state of every wire at once: { e1: "is-on", e2: "is-bad" }. Others are dimmed when any is set.
    mark(states) {
      const any = Object.keys(states).length > 0;
      for (const [id, node] of paths) {
        node.setAttribute("class", `wire ${states[id] || (any ? "is-dim" : "")}`.trim());
      }
    },
  };
}
