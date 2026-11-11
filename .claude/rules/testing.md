---
paths:
  - "tests/**"
---

# Tests

| Folder | Needs | Runs in CI |
|---|---|---|
| `tests/unit/` | Nothing | Yes |
| `tests/integration/` | PostgreSQL (`-m db`, `ERP_TEST_DATABASE_URL`) | Yes |
| `tests/security/` | PostgreSQL | Yes |
| `tests/runtime_conformance/` | A real agent runtime, and an API key or a Claude Code sign-in | No. By hand. |

- Database tests run only on a database whose name ends in `_test`. `tests/conftest.py` stops the run otherwise. Do
  not weaken that check.
- In CI no test may be skipped. A skipped database test could hide a broken guard.
- No test calls a real model unless it lives in `tests/runtime_conformance/` or a benchmark.
- Test names say the rule in plain words: `test_a_supplier_user_never_sees_another_suppliers_orders`.
- A "does not contain" or "returns nothing" check also proves the thing exists when looked at another way. An empty
  result passes it for the wrong reason.
- Use the reference ERP's seeded data and `ANCHOR_DATE`. Do not use the real date.
- A test that loads data leaves the test database loadable for the next test (`reset=True`).
