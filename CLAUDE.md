# Agent Workcell

A Python project built around one idea: **reuse the agent, engineer the environment.** We do not build an agent loop.
We take an existing agent runtime and build the workplace around it: a data guard, a checked knowledge graph, tools
over MCP, identity, memory, audit and evaluation. The agent answers questions over business data in a relational
database.

Read these before you design anything:

- `docs/architecture.md`: the design and the reasons
- `docs/security-model.md`: what is trusted and what is not
- `docs/roadmap.md`: **what exists today**, what is not proven yet and what is planned. Check here before you assume
  a module exists, and keep it true when you add or finish a part.

## Hard rules (never break these)

1. **The database decides access.** Never a prompt. Never a parser reading the model's SQL. Row rules, column hiding
   and table access are grants and views.
2. **Fail closed.** A table, column or person the policy does not mention gets nothing. A missing setting stops the
   service with a clear message. It never falls back to a more powerful account or a wider rule.
3. **Identity comes from the server.** Never from a tool argument, the message or memory. No tool has a "user"
   argument.
4. **Untrusted:** the person's message, everything the model writes (SQL, tool arguments, text, notes), every value
   read from the database, and memory notes. Never build SQL, HTML, a file path or a shell command from them without
   the right escaping or binding.
5. **The model never retypes data.** Files and charts are built by the server from query results.
6. **No invented numbers.** Accuracy, speed and cost figures in docs come only from a recorded run in `bench/results`,
   with the date, model, commit, seed and case count. If there is no run, write "not measured yet".
7. **No "first of its kind" or "best" claims** in docs, comments or the README.
8. **Never name a real company, client, deployment or person** in code, docs, seed data, tests or commit messages.
   All seed data is made up.
9. **No new dependency and no new layer without a reason.** Say the reason in your reply. Prefer the standard library
   and plain functions.

## How to work here

- Build one planned item at a time, in small steps. Stop for review at the end of a step.
- Docs describe the system as it is now. Do not write build history ("this step added", "the first version did") in
  docs, comments or rules.
- Make the smallest change that does the job. Do not refactor or rename code the task did not ask for. If you see a
  bug outside the task, say so. Do not fix it silently.
- Write simple code: plain functions and small classes. No repositories, factories or plug-in systems until two real
  cases need one.
- Every security rule needs a test that fails when the rule is broken. Feed checks a deliberately broken input too,
  so a check cannot pass by doing nothing.
- A design choice that is not proven yet is marked **to verify** in the docs. Do not write it as a fact.
- Use professional, plain English in replies, comments, docs and messages. Short sentences, precise words. No slang,
  jokes or casual phrasing.
- **Never `git commit` or `git push`** unless the maintainer asks in that message.

## Commands

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"    # Windows: .venv\Scripts\pip
.venv/bin/pytest                      # tests that need no database
.venv/bin/pytest -m db                # database tests (need ERP_TEST_DATABASE_URL)
.venv/bin/ruff check .

docker compose up -d db               # PostgreSQL on 127.0.0.1:54329, databases erp and erp_test
docker compose run --rm seed          # load the reference ERP (add --scale 300 for benchmarks, --reset to reload)
docker compose run --rm guard apply   # install the data guard on the local erp database, then check it
.venv/bin/python -m agent_workcell.guard plan --policy reference_erp/policy.toml   # print the install SQL
.venv/bin/python bench/guard_latency/run.py --scale 1                         # guard latency benchmark
.venv/bin/python -m agent_workcell.knowledge check    # knowledge vs the live database (needs WORKCELL_KNOWLEDGE,
.venv/bin/python -m agent_workcell.knowledge guide    #   WORKCELL_POLICY, ERP_DATABASE_URL); guide rewrites the guides
.venv/bin/python -m agent_workcell.server             # the answer server (Claude runtime: answers cost money)
.venv/bin/python -m reference_erp.host_app            # sign-in and the chat page, in front of the answer server
.venv/bin/python -m agent_workcell.eval --cases reference_erp/eval_cases.toml --runtime fake   # free check
#   --runtime claude --max-usd N calls a real model: ask the maintainer first
```

## Layout

| Path | What |
|---|---|
| `src/agent_workcell/` | The package: `policy.py`, `guard/`, `knowledge/`, `tools.py`, `tool_server.py`, `runtime/`, `server.py`, `prompt.py`, `audit.py`, `config.py`, `memory.py`, `files.py`, `render.py`, `eval/`. |
| `reference_erp/` | Purchase-to-pay schema (`schema.sql`), repeatable made-up data (`seed.py`), access policy (`policy.toml`), knowledge (`knowledge/`), benchmark cases (`eval_cases.toml`) and the reference host application with the chat page (`host_app/`). Not part of the package. |
| `tests/unit/` | No database, no network |
| `tests/integration/` | Need PostgreSQL (`-m db`) |
| `tests/security/` | The guard attack suite and its independent oracle, and the answer server end to end (need PostgreSQL) |
| `tests/runtime_conformance/` | The Claude adapter against a real model. By hand only; costs money. |
| `bench/` | Benchmarks (`guard_latency/`) and recorded results (`results/`) |
| `docs/` | Design, security model, status and plans |
| `.claude/rules/` | Rules for each module. They load when you open that module's files. |
| `docker/`, `compose.yaml` | The local environment |

## Safety while developing

- Database tests empty and reload the reference ERP tables. They run only on a database whose name ends in `_test`
  (`tests/conftest.py` enforces this). Never point `ERP_TEST_DATABASE_URL` at anything else.
- `seed.py --reset` empties the reference ERP tables. Use it only on the local development database.
- Never commit `.env`, API keys or database dumps.
- Tests must not call a real model or cost money, unless they are marked for it and run by hand.
- **The product's own agent must never load this file.** When the project runs an agent runtime, it runs it in an
  empty folder outside this repository, with no project settings. Developer instructions here are for people and
  coding agents working on the code, not for the assistant the project ships.

## Reference ERP in one paragraph

A made-up buying company, "Meridian Works", orders from suppliers. Flow: purchase order → goods receipt → supplier
invoice → payment. Roles: `admin` (everything), `buyer` (only their own orders), `supplier_user` (only their own
supplier). The schema is awkward on purpose; `reference_erp/README.md` lists the traps. "Today" in the data is always
2026-06-30 (`ANCHOR_DATE`).
