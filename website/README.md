# The website

The public page for Agent Workcell. It explains the idea, shows how the parts work and links to the repository.

It is static: plain HTML, CSS and JavaScript modules. There is no build step, no framework and no dependency. The
reasons: GitHub Pages serves the folder as it is, and the project prefers plain code to a new layer.

## Preview

JavaScript modules need a web server. A file opened from disk will not run them.

```bash
python -m http.server 8765 --directory website
# then open http://127.0.0.1:8765/
```

## Publish

`.github/workflows/pages.yml` publishes this folder to GitHub Pages on every push to `main` that changes it.
Once, in the repository settings, set **Pages > Source** to **GitHub Actions**.

The page's address is written in three places. Change all of them if the site moves:

- `index.html`: `canonical`, `og:url`, `og:image` and `twitter:image`
- `sitemap.xml` and `robots.txt`
- `404.html`: its links start with `/agent-workcell/`

Links to the repository use `https://github.com/Shivam7414/agent-workcell`. They are in `index.html`, `404.html`
and `assets/js/lib/dom.js`.

## Layout

| Path | What |
|---|---|
| `index.html` | The page. All prose is here, so it reads without JavaScript. |
| `assets/css/tokens.css` | Colours, type scale, spacing and motion. Change the look here first. |
| `assets/css/base.css` | Reset, type, layout and the shared components: navigation, buttons, figure frame, code, tags |
| `assets/css/sections.css` | One block for each section, in page order |
| `assets/js/main.js` | Starts each figure if its element is on the page |
| `assets/js/data.js` | The content of the interactive figures. See "Rules" below. |
| `assets/js/lib/iso.js` | The isometric canvas stage: projection, frame loop, drawing helpers |
| `assets/js/scene.js` | The workcell scene, used by the hero (`play`) and the architecture section (`explore`) |
| `assets/js/shift.js` | The scroll-driven scene of the "Idea" section |
| `assets/js/lib/wires.js` | Connectors between HTML elements, for the knowledge figure |
| `assets/js/*.js` | One module for each remaining figure |
| `assets/fonts/` | Archivo and Martian Mono, with their licences (SIL Open Font License) |

## Rules

The project's rules for docs apply to this page as well.

- **Every number comes from a recorded run** in `bench/results/`, with its date, model, commit, seed and case count
  beside it. When a new run is recorded, update `assets/js/data.js` and the figure captions in `index.html`.
- **The page describes what exists.** When `docs/architecture.md`, `docs/security-model.md` or `docs/roadmap.md`
  change, check the matching section here. Planned and unproven parts are named as such.
- **Each guard query on the page is a case in `tests/security/test_attack_suite.py`**, with the same outcome.
- No "first" or "best" claims. No real company, client or person. Examples use the reference ERP.
- Plain, professional English. Short sentences.

The page sets a content security policy with no inline script, no inline style and no outside source. Keep it:
put script in `assets/js/` and styles in `assets/css/`. JavaScript may set styles through the DOM.

## Motion

- The hero scene and the walkthrough play only while they are on screen.
- With `prefers-reduced-motion`, the scenes draw one still frame, the scroll scene shows its end state and nothing
  plays by itself.
- The canvas scenes carry a text description in `aria-label`. The architecture section also has a button for each
  part, so it works without a pointer.

## The sharing image

`assets/og.png` is a 1200 by 630 capture of the hero. Make it again after a change to the hero.
