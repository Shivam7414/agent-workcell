# Contributing

Thank you for your interest. The project is at an early stage, so the most valuable contributions right now are a
review of the design ([docs/architecture.md](docs/architecture.md), [docs/security-model.md](docs/security-model.md))
and attempts to bypass the data guard. Its attack suite is in `tests/security/`; a new bypass idea is welcome as a
test case, even one that already fails.

## Setup

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"      # Windows: .venv\Scripts\pip
cp .env.example .env
docker compose up -d db
docker compose run --rm seed
```

## Before you open a pull request

```bash
.venv/bin/ruff check .
.venv/bin/pytest
ERP_TEST_DATABASE_URL=postgresql://erp_owner:erp_owner_dev@127.0.0.1:54329/erp_test .venv/bin/pytest -m db
```

## Guidelines

- Keep changes small and on one subject.
- A change to a security rule comes with a test that fails without it.
- No new dependency without a reason in the pull request text.
- No performance or accuracy numbers without a recorded run in `bench/results/`.
- No real company, client or person in code, docs, seed data or tests.
- Professional, plain English in docs and comments.

The working rules for the code are in [CLAUDE.md](CLAUDE.md) and [.claude/rules/](.claude/rules/). They are written
for coding agents, and they are the same rules people follow.

## Security problems

Do not open a public issue. See [SECURITY.md](SECURITY.md).
