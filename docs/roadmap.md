# Status and plans

This page says what exists today, what has not been proven yet, and what is planned. A part counts as existing only
when its tests are in the repository and pass. The design and its reasons are in [architecture.md](architecture.md).

## What exists

| Part | Where | Tests |
|---|---|---|
| Reference ERP: schema, repeatable made-up data, access policy | `reference_erp/schema.sql`, `seed.py`, `policy.toml` | `tests/unit/test_seed_data.py`, `tests/integration/test_seed_load.py` |
| Policy file and loader | `src/agent_workcell/policy.py` | `tests/unit/test_policy.py` |
| Data guard for PostgreSQL: install SQL (`guard plan`), start-up check (`guard doctor`), guarded connection | `src/agent_workcell/guard/`, `python -m agent_workcell.guard plan / doctor / apply` (`apply` is for local development only) | `tests/unit/test_plan.py`; `tests/security/`: attack suite against an independent oracle, parity per person and table, identity, limits, doctor |
| Knowledge graph: data files, check against the live database, generated guide per role, retrieval | `src/agent_workcell/knowledge/`, `reference_erp/knowledge/` (13 tables, 5 calculations, 12 rules) | `tests/unit/test_knowledge_graph.py`, `test_retrieve.py`, `tests/integration/test_knowledge_check.py` |
| Tools over MCP: `knowledge`, `get_schema`, `run_query`, `create_file`, `remember` | `src/agent_workcell/tools.py`, `tool_server.py` | `tests/security/test_answer_server.py` |
| Runtime interface, fake runtime, Claude adapter with its hardening profile | `src/agent_workcell/runtime/` | `tests/unit/test_runtime_and_prompt.py`; `tests/runtime_conformance/` (by hand, calls a model) |
| Answer server: host-key API, streamed answers, per-answer and per-person limits, audit | `src/agent_workcell/server.py`, `prompt.py`, `audit.py`, `config.py` | `tests/security/test_answer_server.py`, `tests/unit/test_runtime_and_prompt.py` |
| Files (CSV and Excel built by the server), memory notes, cleaned answer HTML | `src/agent_workcell/files.py`, `memory.py`, `render.py` | `tests/unit/test_files.py`, `test_memory.py`, `test_render.py` |
| Reference host application: sign-in, signed session, chat page | `reference_erp/host_app/`, `python -m reference_erp.host_app` | `tests/security/test_host_app.py` |
| Evaluation: 28 cases (22 with gold SQL, 6 permission probes), graders in plain code, harness | `reference_erp/eval_cases.toml`, `src/agent_workcell/eval/`, `python -m agent_workcell.eval` | `tests/unit/test_eval_grade.py`, `tests/security/test_eval_harness.py` |
| Guard latency benchmark | `bench/guard_latency/run.py` | Results in `bench/results/` |

## Dependencies and why

| Dependency | Why |
|---|---|
| `psycopg` | The PostgreSQL driver for the guard, the knowledge check and the reference ERP |
| `mcp` | The official MCP SDK. The tool server must speak standard MCP, and the protocol is too large to write by hand safely. |
| `starlette`, `uvicorn`, `anyio` | The web stack `mcp` already uses. Listed because the project imports them. |
| `httpx2` | The HTTP client `mcp` already installs. The reference host application and the evaluation harness call the answer API with it. |
| `claude-agent-sdk` (optional `claude` extra, pinned) | The agent runtime the project reuses. Pinned because the hardening profile is checked against one version. |

Everything else (TOML, SQLite, CSV, Excel files, HTML cleaning, password hashing) uses the standard library.

## Recorded results

- **Guard latency:** two runs, at 3,000 and about 900,000 purchase orders. See [architecture.md](architecture.md),
  "Speed".
- **Answers:** one small run of 10 cases. See [architecture.md](architecture.md), "Recorded results". It shows the
  system works end to end; it does not measure accuracy.
- **Runtime conformance:** run in the Claude Code sign-in mode. See [architecture.md](architecture.md), "The agent
  runtime".

## Not proven yet (to verify)

- The Claude adapter's isolated mode against the real runtime: no account details reach the model, and no transcript
  is left outside the run's folder. These checks need an API key.
- Whether the model always uses the date in `<today>` rather than the real date the runtime adds.
- Whether the built files open cleanly in Excel and LibreOffice. So far they are checked by reading the XML back and
  with `openpyxl`.
- Whether word matching is enough for retrieval. The recorded answer run is too small to tell.
- Accuracy. It needs all 28 cases, repeated, with a recorded commit:
  `python -m agent_workcell.eval --cases reference_erp/eval_cases.toml --runtime claude --repeats 3 --max-usd N`.

## Planned

| Item | Notes |
|---|---|
| Docker Compose services for the answer server and the reference host application | Today they run with Python next to the Compose database. Goal: `docker compose up` gives PostgreSQL, the reference ERP and the assistant, and three roles can sign in and ask. |
| A first release | After the item above and a recorded benchmark run with a commit |
| A fact in the reference knowledge that the ERP belongs to Meridian Works | The recorded run showed a supplier user's answer hedging because the model did not know it |
| MariaDB and MySQL guard | Needs its own lab that passes the same attack suite; the technique is open (see [architecture.md](architecture.md)) |
| Per-role column visibility | Today a column is visible to every role that can read its table, or to none |
| A plain model-API tool loop as a benchmark baseline | To show what reusing an agent runtime adds |
| Charts built by the server | Same rule as files: the model never types the data |
| Editing single memory notes | Today a person can list their notes and delete all of them |
| More runtime adapters | Only when there is a concrete need |
