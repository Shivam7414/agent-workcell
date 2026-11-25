# The website

The public page for Agent Workcell. It shows what the project leaves out of a retrieval pipeline and what runs
instead, follows one recorded answer through the workcell, and links to the repository. It is short on purpose:
the detail is in `docs/`.

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

Links to the repository use `https://github.com/Shivam7414/agent-workcell`. They are in `index.html` and `404.html`.

## Layout

| Path | What |
|---|---|
| `index.html` | The page. All of its text is here, so it reads without JavaScript. |
| `assets/css/tokens.css` | Colours, type scale, spacing and motion. Change the look here first. |
| `assets/css/base.css` | Reset, type, layout and the shared components: navigation, buttons, code block, footer |
| `assets/css/sections.css` | One block for each section, in page order, with the small figures |
| `assets/js/main.js` | Starts each figure that is on the page, and the copy buttons |
| `assets/js/lib/iso.js` | The isometric canvas stage: projection, frame loop, drawing helpers |
| `assets/js/shift.js` | The hero scene: twelve pipeline blocks become one agent and its environment |
| `assets/js/scene.js` | The workcell scene: the arm carries one question through the cell |
| `assets/js/flow.js` | "How it works": ties the workcell scene to the five steps beside it |
| `assets/js/figures.js` | The small figures: when each starts, the data guard's three people, the test-case squares |
| `assets/fonts/` | Archivo and Martian Mono, with their licences (SIL Open Font License) |

## The look

The two canvas scenes set the drawing rules: flat colour, one ink outline, a flat shadow. Cards and small figures
follow the same rules. Each colour has one meaning everywhere on the page:

| Colour | Meaning |
|---|---|
| Orange | Claude Code, the reused agent |
| Blue | The parts this project builds around it |
| Yellow | The data guard |
| Cyan | Data that was allowed through |
| Dashed outline | Left out, or not run |

## Rules

The project's rules for docs apply to this page as well.

- **Every number comes from a recorded run** in `bench/results/`, with its date, model, commit, seed and case count
  beside it. The question, the SQL and the answer in "How it works" are quoted from the result file, and the 28
  squares of "Recorded run" are the cases of `reference_erp/eval_cases.toml`. When a new run is recorded, update
  both sections.
- **The page describes what exists.** The blocks of the hero scene follow "Compared with a retrieval pipeline" and
  "What is deliberately left out" in `docs/architecture.md`. Every object in the workcell scene stands for a real
  part named there. When that document, `docs/security-model.md` or `docs/roadmap.md` change, check the matching
  section here.
- **The excerpts are real.** The guide, policy and knowledge lines shown are copied from `reference_erp/`, and the
  tool lists from `src/agent_workcell/runtime/claude.py`. Change them here when they change there. The six rows of
  the data guard figure are sample rows, and the page says so; the three row rules applied to them are real.
- **Keep it short.** A new section needs a reason. Detail belongs in `docs/`, with a link from the page.
- No "first" or "best" claims. No real company, client or person. Examples use the reference ERP.
- Plain, professional English. Short sentences.

The page sets a content security policy with no inline script, no inline style and no outside source. Keep it:
put script in `assets/js/` and styles in `assets/css/`. JavaScript may set styles through the DOM.

## Motion

Motion appears only inside a figure, and only to show how a part works.

- The hero scene plays once when the page loads. After that the slider under it moves it by hand.
- The workcell scene runs its loop only while it is on screen. It can be paused, and each step can be selected.
- The small figures play once, when they scroll into view. Each of their animations names only its start, so the
  plain styles are the end state.
- With `prefers-reduced-motion`, nothing plays by itself: the hero scene shows its end state, the workcell scene
  shows one still frame for the selected step, and the small figures show their end state.
- The canvas scenes carry a text description in `aria-label`, and the text beside each one says the same in words.

## The sharing image

`assets/og.png` is a 1200 by 630 capture of the hero in its end state. Make it again after a change to the hero.
With the page served locally, a headless browser can take it:

```bash
chrome --headless=new --hide-scrollbars --force-prefers-reduced-motion --window-size=1524,800 \
    --force-device-scale-factor=0.7874016 --virtual-time-budget=5000 \
    --screenshot=website/assets/og.png http://127.0.0.1:8765/
```
