"""A test database with the reference ERP, the installed guard, and one oracle schema per test person."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from agent_workcell.guard import GuardedConnection, Limits, Names, Principal, QueryError
from agent_workcell.guard.plan import install
from agent_workcell.policy import Policy, load_policy
from reference_erp import seed
from tests.security import oracle

POLICY_PATH = Path(__file__).parents[2] / "reference_erp" / "policy.toml"
AGENT_PASSWORD = "agent-test-only"
BINDER_PASSWORD = "binder-test-only"
# Roles belong to the whole PostgreSQL server, not to one database. The tests use their own role names, so they never
# change the roles (or passwords) of the local development database.
NAMES = Names(guard_role="workcell_test_guard", agent_role="workcell_test_agent", binder_role="workcell_test_binder")
# The attack suite compares whole results, so its caps are far above the size of the seed data.
WIDE = Limits(timeout_ms=20_000, max_rows=100_000, max_chars=200_000_000)


@dataclass
class GuardDB:
    owner_url: str
    agent_url: str
    binder_url: str
    policy: Policy
    tables: dict[str, list[dict]]
    principals: dict[str, Principal | None]
    _open: dict[str, GuardedConnection] = field(default_factory=dict)

    def connect(self, principal: Principal, limits: Limits = WIDE) -> GuardedConnection:
        return GuardedConnection(self.agent_url, self.binder_url, self.policy, principal, limits, NAMES)

    def shared(self, label: str) -> GuardedConnection:
        """One long-lived guarded connection per test person, for read-only queries."""
        if label not in self._open:
            self._open[label] = self.connect(self.principals[label])
        return self._open[label]

    def close(self) -> None:
        for connection in self._open.values():
            connection.close()
        self._open.clear()


@pytest.fixture(scope="session")
def guard_db(erp_test_url):
    policy = load_policy(POLICY_PATH)
    with psycopg.connect(erp_test_url) as conn:
        seed.load(conn, reset=True)
        install(conn, policy, AGENT_PASSWORD, BINDER_PASSWORD, NAMES)

    tables = oracle.generated_tables()
    db = GuardDB(
        owner_url=erp_test_url,
        agent_url=make_conninfo(erp_test_url, user=NAMES.agent_role, password=AGENT_PASSWORD),
        binder_url=make_conninfo(erp_test_url, user=NAMES.binder_role, password=BINDER_PASSWORD),
        policy=policy,
        tables=tables,
        principals=oracle.principals_under_test(tables),
    )
    yield db
    db.close()


@pytest.fixture(scope="session")
def oracle_db(guard_db):
    """Builds oracle_<label> schemas, and returns a function that runs a query on one of them."""
    with psycopg.connect(guard_db.owner_url) as conn:
        for label, principal in guard_db.principals.items():
            oracle.build_schema(conn, f"oracle_{label}", oracle.visible_rows(guard_db.tables, principal))

    conn = psycopg.connect(guard_db.owner_url)

    def run(label: str, text: str) -> tuple:
        try:
            conn.read_only = True
            conn.execute(sql.SQL("SET LOCAL search_path = {}").format(sql.Identifier(f"oracle_{label}")))
            with conn.cursor(name="oracle_query") as cursor:
                cursor.execute(text)
                rows = cursor.fetchall()
                columns = [column.name for column in cursor.description or []]
            return outcome(columns, rows)
        except psycopg.Error as error:
            return ("error", error.sqlstate)
        finally:
            conn.rollback()

    yield run
    with psycopg.connect(guard_db.owner_url, autocommit=True) as cleanup:
        for label in guard_db.principals:
            cleanup.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(f"oracle_{label}")))
    conn.close()


def outcome(columns: list[str], rows: list[tuple]) -> tuple:
    return ("rows", columns, sorted(rows, key=repr))


def guard_outcome(guard_db: GuardDB, label: str, text: str) -> tuple:
    """The same shape as the oracle's result. 'nobody' runs on a plain agent connection with no one bound."""
    if guard_db.principals[label] is None:
        with psycopg.connect(guard_db.agent_url) as conn:
            conn.read_only = True
            conn.execute("SET search_path = guard")
            try:
                with conn.cursor(name="q") as cursor:
                    cursor.execute(text)
                    return outcome([column.name for column in cursor.description or []], cursor.fetchall())
            except psycopg.Error as error:
                return ("error", error.sqlstate)
    try:
        result = guard_db.shared(label).query(text)
    except QueryError as error:
        return ("error", error.sqlstate)
    assert not result.truncated, "raise the WIDE limits: the comparison needs whole results"
    return outcome(result.columns, result.rows)
