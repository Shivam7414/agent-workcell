---
paths:
  - "reference_erp/**"
  - "tests/unit/test_seed_data.py"
  - "tests/integration/test_seed_load.py"
  - "tests/security/test_host_app.py"
---

# Reference ERP

Status: schema, seed and the reference host application (`host_app/`: sign-in and the chat page) are **built**.

`reference_erp/README.md` describes the tables, the roles and the traps.

## Rules

- **Same seed and scale, same rows.** `seed.py` never reads the clock and uses one `random.Random(seed)`. Every date
  is counted back from `ANCHOR_DATE`. The order of random calls is part of the output: adding one call in the middle
  changes every row after it. That is allowed, but say so, because recorded benchmark results stop being comparable.
- **Everything is made up.** No real company, person, address, tax number or bank account. Email addresses use the
  `.example` domain. Hostile links use `example.invalid`.
- **The traps are the point.** Do not tidy the schema (add missing foreign keys, store totals, rename `approved_by`).
  If you add a trap, add it to the README table and to `test_the_traps_are_in_the_data`.
- `generate()` needs no database, so its tests run anywhere. `load()` is the only part that touches PostgreSQL.
- `load()` never deletes unless `reset=True`, and then only empties the reference ERP tables it knows.
- When you change `schema.sql`, change `COLUMNS` in `seed.py` in the same step.
- The reference ERP is not part of the installed package. Nothing in `src/agent_workcell/` may import it.

## The reference host application

- It plays the host application: it signs people in and sends their principal to the answer API. The principal
  comes only from its signed session, never from the browser.
- The page shows model and data text with `textContent`. Only the server's `answer_html` is set as HTML.
- Keep the content security policy strict: no inline script, no outside source. Put new script in `static/`.
- It is a reference, not a login system to deploy. Do not grow it into one; say so in the docs instead.
