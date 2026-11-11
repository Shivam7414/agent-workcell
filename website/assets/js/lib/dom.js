// Small DOM helpers shared by the figures.

export const REPO = "https://github.com/Shivam7414/agent-workcell";

// A link to a file or folder of the repository.
export const src = (path) => `${REPO}/${path.endsWith("/") ? "tree" : "blob"}/main/${path}`;

export const $ = (selector, root = document) => root.querySelector(selector);
export const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

// el("p", { class: "note", text: "…" }, child, child)
export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "text") node.textContent = value;
    else if (key === "class") node.className = value;
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === undefined || child === null || child === false) continue;
    node.append(child);
  }
  return node;
}

export const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export function onVisible(node, callback, { margin = "0px", once = true } = {}) {
  const watcher = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        callback(entry.isIntersecting);
        if (entry.isIntersecting && once) watcher.disconnect();
      }
    },
    { rootMargin: margin },
  );
  watcher.observe(node);
}

// A code block with a title bar. Lines can be plain strings or [text, className].
export function codeBlock(title, lines, { href } = {}) {
  const pre = el("pre");
  lines.forEach((line, i) => {
    const [text, cls] = Array.isArray(line) ? line : [line, null];
    pre.append(cls ? el("span", { class: cls, text }) : text);
    if (i < lines.length - 1) pre.append("\n");
  });
  const label = href ? el("a", { href, rel: "noopener", text: title }) : el("span", { text: title });
  return el("div", { class: "code" }, el("div", { class: "code__bar" }, label), pre);
}
