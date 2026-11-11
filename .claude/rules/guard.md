---
paths:
  - "src/agent_workcell/guard/**"
  - "src/agent_workcell/policy.py"
  - "tests/security/**"
  - "bench/guard_latency/**"
---

# Data guard and policy

Status: **built for PostgreSQL.** Code: `src/agent_workcell/policy.py` and `src/agent_workcell/guard/`.
The design and its reasons are in `docs/architecture.md` ("The data guard"). Known limits are in
`docs/security-model.md`.

## What it must always do

- The agent's database role has **no rights on real tables**. It reads guard views only. Safety must not depend on
  finding table names in the model's SQL.
- A table not in the policy is not available. A column not marked visible is hidden.
- Identity is bound to the connection by trusted code, in a place the agent role cannot write. A connection with no
  bound identity returns no rows.
- One connection serves one person. Never reuse a guarded connection for someone else, and never let a pool or an
  automatic reconnect hand the agent a fresh connection that has no identity bound.
- Every query: one statement, read-only transaction, time limit, row cap, size cap.
- At start-up, check what the agent role can really do. If it can do more than read the guard views, stop.

## When you write guard code

- Policy values (like a supplier id) reach SQL as bound parameters or through the bound identity. Never by string
  formatting.
- Table and column names in generated SQL come from the database catalog or the validated policy, and are quoted with
  the driver's identifier quoting.
- Errors returned to the model may name only tables and columns that person may see. Never the database name, the
  host, the role names or the real table behind a view.
- Do not add an SQL parser to "make it safer". If a query can reach data it should not, fix the grants.
- In a view, read the bound person only through uncorrelated scalar subqueries on `guard_internal.principal`.
  A join or a correlated reference makes PostgreSQL run row conditions once per row (see "Speed" in the
  architecture). Re-run `bench/guard_latency/run.py` after changing the view shape.

## Tests

- Every rule above has a test in `tests/security/` that fails when the rule is broken.
- The attack suite compares the guard with an independent oracle: the same query on a database that physically holds
  only that person's rows. Add a case for every bypass idea, including ones that already fail.
- A "returns no rows" check is not enough alone. Also prove the rows exist when read without the guard.
- Guard tests never need a model.
- Tests use their own role names (`workcell_test_*`, `NAMES` in `tests/security/conftest.py`). Roles belong to
  the whole server, so tests must never install over the development roles.

## Other database engines

MariaDB and MySQL come after PostgreSQL. Their technique is not decided. Start with a lab that runs the same attack
suite and oracle on that engine, and choose the technique from its results. Do not ship one without that lab.
