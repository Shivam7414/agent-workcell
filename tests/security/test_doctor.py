"""The doctor must pass on a correct install, and find each way the install can be broken."""

from contextlib import contextmanager

import psycopg
import pytest

from agent_workcell.guard import Names, check
from agent_workcell.guard.doctor import errors
from agent_workcell.policy import parse_policy
from tests.security.conftest import NAMES, POLICY_PATH

pytestmark = pytest.mark.db

BIND = "guard_internal.bind(integer, timestamptz, text, bigint, bigint)"
AGENT, BINDER, GUARD = NAMES.agent_role, NAMES.binder_role, NAMES.guard_role

# (break it, fix it, the problem the doctor must report)
BREAKS = [
    (f"GRANT SELECT ON public.suppliers TO {AGENT}",
     f"REVOKE SELECT ON public.suppliers FROM {AGENT}", f"{AGENT} has rights on public.suppliers"),
    (f"GRANT SELECT (tax_id) ON public.suppliers TO {AGENT}",
     f"REVOKE SELECT (tax_id) ON public.suppliers FROM {AGENT}", f"{AGENT} has rights on public.suppliers"),
    (f"GRANT USAGE ON SEQUENCE public.users_id_seq TO {AGENT}",
     f"REVOKE USAGE ON SEQUENCE public.users_id_seq FROM {AGENT}", f"{AGENT} has rights on public.users_id_seq"),
    (f"GRANT INSERT ON guard.suppliers TO {AGENT}",
     f"REVOKE INSERT ON guard.suppliers FROM {AGENT}", f"{AGENT} has rights on guard.suppliers"),
    ("ALTER VIEW guard.suppliers SET (security_barrier = false)",
     "ALTER VIEW guard.suppliers SET (security_barrier = true)", "guard.suppliers is not a security_barrier view"),
    (f"ALTER ROLE {AGENT} CREATEDB", f"ALTER ROLE {AGENT} NOCREATEDB", f"{AGENT} has CREATEDB"),
    (f"GRANT {GUARD} TO {AGENT}", f"REVOKE {GUARD} FROM {AGENT}", f"{AGENT} is a member of {GUARD}"),
    (f"GRANT USAGE ON SCHEMA guard_internal TO {AGENT}",
     f"REVOKE USAGE ON SCHEMA guard_internal FROM {AGENT}", f"{AGENT} can use schema guard_internal"),
    (f"GRANT EXECUTE ON FUNCTION {BIND} TO {AGENT}",
     f"REVOKE EXECUTE ON FUNCTION {BIND} FROM {AGENT}", f"{AGENT} can run guard_internal.bind"),
    (f"GRANT CREATE ON SCHEMA public TO {AGENT}",
     f"REVOKE CREATE ON SCHEMA public FROM {AGENT}", f"{AGENT} can create objects in schema public"),
    ("CREATE VIEW guard.extra AS SELECT 1 AS one", "DROP VIEW guard.extra", "guard.extra is not in the policy"),
    (f"GRANT SELECT ON public.users TO {BINDER}",
     f"REVOKE SELECT ON public.users FROM {BINDER}", f"{BINDER} has rights on public.users"),
]


@contextmanager
def broken(url: str, break_sql: str, fix_sql: str):
    with psycopg.connect(url, autocommit=True) as owner:
        owner.execute(break_sql)
        try:
            yield owner
        finally:
            owner.execute(fix_sql)


def messages(conn, policy, names: Names | None = None) -> list[str]:
    return [problem.message for problem in check(conn, policy, names or NAMES)]


def test_a_correct_install_has_no_problems(guard_db):
    with psycopg.connect(guard_db.owner_url) as conn:
        assert check(conn, guard_db.policy, NAMES) == []


@pytest.mark.parametrize(("break_sql", "fix_sql", "expected"), BREAKS)
def test_the_doctor_finds_a_broken_install(guard_db, break_sql, fix_sql, expected):
    with broken(guard_db.owner_url, break_sql, fix_sql) as conn:
        found = check(conn, guard_db.policy, NAMES)
        assert any(expected in problem.message for problem in errors(found)), found
    with psycopg.connect(guard_db.owner_url) as conn:
        assert check(conn, guard_db.policy, NAMES) == [], "the fix must restore a clean install"


def test_the_doctor_reports_a_column_that_nobody_has_marked(guard_db):
    with broken(guard_db.owner_url, "ALTER TABLE public.suppliers ADD COLUMN salary numeric",
                "ALTER TABLE public.suppliers DROP COLUMN salary") as conn:
        found = check(conn, guard_db.policy, NAMES)
        assert [str(problem) for problem in found] == [
            "warning: suppliers.salary is not marked visible or hidden, so it is hidden. Mark it in the policy."]
        columns = [row[0] for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'guard' "
            "AND table_name = 'suppliers'").fetchall()]
        assert "salary" not in columns


def test_the_doctor_refuses_an_install_made_from_another_policy(guard_db):
    text = POLICY_PATH.read_text(encoding="utf-8")
    wider = text.replace('hidden = ["tax_id", "bank_account"]', 'hidden = ["bank_account"]')
    wider = wider.replace('"created_at", "deleted_at"]\nhidden', '"created_at", "deleted_at", "tax_id"]\nhidden')
    assert wider != text
    with psycopg.connect(guard_db.owner_url) as conn:
        found = messages(conn, parse_policy(wider))
    assert any("different policy" in message for message in found)
    assert any("guard.suppliers has columns" in message for message in found)


def test_the_doctor_stops_when_a_role_is_missing(guard_db):
    with psycopg.connect(guard_db.owner_url) as conn:
        missing = Names(guard_role=GUARD, agent_role="workcell_test_missing", binder_role=BINDER)
        found = messages(conn, guard_db.policy, missing)
    assert any("workcell_test_missing does not exist" in message for message in found)
