# Agent Workcell

**Reuse the agent. Engineer the environment.**

In a factory, nobody builds a new robot for each job. They take a general-purpose robot and build a *workcell* around
it: fixtures, tooling and safety guarding. The robot is reused. The cell is engineered.

Agent Workcell applies the same idea to AI agents that answer questions over business data. It does not build a new
agent loop on top of a raw model API. It takes an existing agent runtime and builds the workplace around it:

| Part of the cell | What it gives the agent |
|---|---|
| Data guard | The database itself decides which rows and columns each person may read. Never the prompt. |
| Knowledge graph | The business meaning of the data, written once and checked against the real system. |
| Tools | A small set of tools over MCP: look up knowledge, read data, make files. |
| Identity | Who is asking, taken from the server. The model cannot change it. |
| Memory | Short notes per person, kept apart from anything trusted. |
| Audit | Every answer stored with the exact queries behind it. |
| Evaluation | Benchmarks with answers checked against the database, and permission probes. |

## Status

**Early stage, not independently reviewed.** Do not use it with production data. [docs/roadmap.md](docs/roadmap.md)
lists what exists, what is not proven yet and what is planned.

What exists:

- a **reference ERP**: a purchase-to-pay schema with repeatable made-up data, used by every test and benchmark;
- the **data guard** for PostgreSQL: a policy file, the SQL that installs it, a start-up check, and a connection that
  runs one read-only query as one person. An attack suite compares every hostile query with an independent oracle;
- the **knowledge graph**: what each table, column and stored value means, with calculations and business rules. A
  check compares it with the live database, and a guide is generated for each role that lists only what that role
  can read;
- the **answer server**: an API for the host application, a tool server over MCP whose tools act as the person who
  asked, a Claude adapter that removes Claude Code's own shell, file and web tools, limits per answer and per person,
  and an audit record of every answer and query;
- **files and memory**: CSV and Excel files that the server builds from a query (text never runs as a formula), and
  short notes per person that never change access. The answer is returned as cleaned HTML;
- a **reference chat page** with sign-in for the reference ERP's users, to try the whole flow locally;
- an **evaluation harness**: 28 benchmark cases with gold SQL run through the guard as the same person, permission
  probes, graders in plain code, and a total cost budget for real runs.

One small answer run is recorded: 10 cases, 7 of 7 graded answers correct and 0 permission violations in 3 probes
(2026-11-11, `claude-opus-5-5`, no commit; [result](bench/results/answers-2026-11-11-claude-agent-sdk-claude-opus-5-5-10.json),
details in [docs/architecture.md](docs/architecture.md), "Recorded results"). With so few answers it shows the system
works end to end; it does not measure accuracy.

## Quick start

You need Docker, or Python 3.11+ with a PostgreSQL server.

```bash
cp .env.example .env
docker compose up -d db                  # PostgreSQL on 127.0.0.1:54329
docker compose run --rm seed             # load the reference ERP: 3,000 purchase orders
docker compose run --rm guard apply      # install the data guard from reference_erp/policy.toml, then check it
docker compose run --rm guard plan       # print the SQL that "apply" ran, for review
```

See what the knowledge graph gives the model for a question, as one role:

```bash
python -m agent_workcell.knowledge find --role buyer "invoices in review for my orders" \
    --knowledge reference_erp/knowledge --policy reference_erp/policy.toml
```

Then query the reference ERP as one person, through the guard:

```python
from agent_workcell.guard import GuardedConnection, Principal, QueryError
from agent_workcell.policy import load_policy

policy = load_policy("reference_erp/policy.toml")
agent = "postgresql://workcell_agent:workcell_agent_dev@127.0.0.1:54329/erp"
binder = "postgresql://workcell_binder:workcell_binder_dev@127.0.0.1:54329/erp"
person = Principal("supplier_user", 16, {"supplier_id": 1})   # from the host application's login, never the model

with GuardedConnection(agent, binder, policy, person) as connection:
    print(connection.query("SELECT count(*) FROM purchase_orders").rows)   # only supplier 1's sent orders
    try:
        connection.query("SELECT * FROM public.purchase_orders")             # the real table
    except QueryError as error:
        print(error.message)                                                  # Permission denied. ...
```

Run the answer server with the Claude runtime. Answers call a model and cost money. It reads `ANTHROPIC_API_KEY` and
the settings in `.env`. The runtime gets its own empty configuration folder for each answer, so it never uses a
personal Claude Code sign-in. For a local trial with your own sign-in instead, set `WORKCELL_CLAUDE_ACCOUNT_LOGIN=1`;
the account's email address then reaches the model, so never use it on a server.

```bash
pip install -e ".[claude]"
python -m agent_workcell.server                       # http://127.0.0.1:8080

curl -N http://127.0.0.1:8080/v1/answers \
  -H "Authorization: Bearer $WORKCELL_HOST_KEY" -H "Content-Type: application/json" \
  -d '{"principal": {"role": "buyer", "user_id": 4}, "question": "How many of my orders are still open?"}'
```

The answer streams back as server-sent events. In a real deployment the host application sends the principal of the
person it has logged in; the project does no login of its own.

To try it in a browser, start the reference host application next to the answer server. It plays the part of the
ERP: it signs in the reference ERP's users and sends their principal to the answer server.

```bash
python -m reference_erp.host_app                      # http://127.0.0.1:8081
```

Sign in with any seeded user and the password `workcell-demo`. The sign-in page lists one account for each role.

For development:

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev,claude]"   # Windows: .venv\Scripts\pip
.venv/bin/pytest                           # tests that need no database
ERP_TEST_DATABASE_URL=postgresql://erp_owner:erp_owner_dev@127.0.0.1:54329/erp_test .venv/bin/pytest
.venv/bin/ruff check .
```

With `ERP_TEST_DATABASE_URL` set, the run includes the guard's attack suite (`tests/security/`). It creates its own
`workcell_test_*` roles, so it does not change the roles of the development database.

## Documentation

- [docs/architecture.md](docs/architecture.md): the design and the reasons behind it
- [docs/security-model.md](docs/security-model.md): what is trusted, what is not, and how access is enforced
- [docs/roadmap.md](docs/roadmap.md): what exists, what is not proven yet, and what is planned
- [reference_erp/README.md](reference_erp/README.md): the reference ERP and its deliberate traps
- [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md)

## How results are reported

- Every accuracy or speed number comes from a recorded benchmark run in this repository, with the date, the model,
  the commit and the number of cases.
- Data safety never depends on a prompt. Access rules live in the database.

## License

Apache-2.0. See [LICENSE](LICENSE).
