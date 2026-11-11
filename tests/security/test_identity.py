"""Who is asking is bound by trusted code, and nothing the agent writes can change it."""

import psycopg
import pytest
from psycopg import sql

from agent_workcell.guard import GuardedConnection, GuardError, Principal
from agent_workcell.guard.plan import bind_call
from tests.security.conftest import NAMES

pytestmark = pytest.mark.db


def count(connection, table: str) -> int:
    return connection.query(f"SELECT count(*) FROM {table}").rows[0][0]


def test_a_connection_with_nobody_bound_sees_no_rows(guard_db):
    with psycopg.connect(guard_db.owner_url) as owner, psycopg.connect(guard_db.agent_url) as agent:
        for table in guard_db.policy.tables:
            seen = agent.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("guard", table))).fetchone()
            real = owner.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("public", table))).fetchone()
            assert seen[0] == 0, table
            assert real[0] > 0, f"{table} must have rows, or this check proves nothing"


def test_the_agent_role_cannot_bind_itself_or_write_who_is_asking(guard_db):
    with psycopg.connect(guard_db.agent_url, autocommit=True) as agent:
        pid, started = agent.execute(
            "SELECT pid, backend_start FROM pg_stat_get_activity(pg_backend_pid())").fetchone()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            agent.execute(bind_call(guard_db.policy, NAMES), [pid, started, "admin", 1, None])
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            agent.execute("INSERT INTO guard_internal.sessions (pid, backend_start, principal_role, principal_user_id) "
                          "VALUES (%s, %s, 'admin', 1)", [pid, started])
        assert agent.execute("SELECT count(*) FROM guard.purchase_orders").fetchone()[0] == 0


def test_settings_written_by_the_model_do_not_change_who_is_asking(guard_db):
    buyer = guard_db.principals["buyer"]
    expected = sum(1 for order in guard_db.tables["purchase_orders"] if order["buyer_id"] == buyer.user_id)
    with guard_db.connect(buyer) as connection:
        for name in ("guard.user_id", "workcell.role", "app.user_id", "application_name"):
            connection.query(f"SELECT set_config('{name}', 'admin', false)")
        assert count(connection, "purchase_orders") == expected
        assert expected < len(guard_db.tables["purchase_orders"])


def test_a_new_connection_with_a_reused_process_id_gets_nobody(guard_db):
    with guard_db.connect(guard_db.principals["admin"]) as connection:
        assert count(connection, "purchase_orders") > 0
        # Make the stored start time differ from the live one, as it would for an earlier connection with this id.
        with psycopg.connect(guard_db.owner_url, autocommit=True) as owner:
            owner.execute("UPDATE guard_internal.sessions SET backend_start = backend_start - interval '1 second' "
                          "WHERE pid = %s", [connection.pid])
        assert count(connection, "purchase_orders") == 0


def test_a_connection_serves_one_person_and_cannot_be_bound_again(guard_db):
    with guard_db.connect(guard_db.principals["buyer"]) as connection:
        with psycopg.connect(guard_db.binder_url, autocommit=True) as binder:
            with pytest.raises(psycopg.errors.RaiseException, match="already bound"):
                binder.execute(bind_call(guard_db.policy, NAMES),
                               [connection.pid, connection.backend_start, "admin", 1, None])
        assert count(connection, "payments") == 0  # still the buyer, who sees no payments


def test_bind_refuses_a_connection_that_is_not_an_agent_connection(guard_db):
    with psycopg.connect(guard_db.binder_url, autocommit=True) as binder:
        pid, started = binder.execute(
            "SELECT pid, backend_start FROM pg_stat_get_activity(pg_backend_pid())").fetchone()
        with pytest.raises(psycopg.errors.RaiseException, match="not an agent connection"):
            binder.execute(bind_call(guard_db.policy, NAMES), [pid, started, "admin", 1, None])


def test_bind_refuses_a_role_that_is_not_in_the_policy(guard_db):
    with pytest.raises(GuardError, match="not in the policy"):
        guard_db.connect(Principal("superuser", 1))

    with psycopg.connect(guard_db.agent_url, autocommit=True) as agent, \
            psycopg.connect(guard_db.binder_url, autocommit=True) as binder:
        pid, started = agent.execute("SELECT pid, backend_start FROM pg_stat_get_activity(pg_backend_pid())").fetchone()
        with pytest.raises(psycopg.errors.RaiseException, match="not in the policy"):
            binder.execute(bind_call(guard_db.policy, NAMES), [pid, started, "superuser", 1, None])
        assert agent.execute("SELECT count(*) FROM guard.purchase_orders").fetchone()[0] == 0


def test_a_principal_attribute_that_the_policy_does_not_define_is_refused(guard_db):
    with pytest.raises(GuardError, match="does not define"):
        guard_db.connect(Principal("buyer", 4, {"tenant_id": 1}))


def test_the_guarded_connection_refuses_to_run_as_any_role_but_the_agent(guard_db):
    with pytest.raises(GuardError, match=f"must log in as {NAMES.agent_role}"):
        GuardedConnection(guard_db.owner_url, guard_db.binder_url, guard_db.policy, guard_db.principals["admin"],
                          names=NAMES)


def test_closing_the_connection_removes_the_binding_and_it_is_never_reused(guard_db):
    connection = guard_db.connect(guard_db.principals["buyer"])
    with psycopg.connect(guard_db.owner_url) as owner:
        bound = "SELECT count(*) FROM guard_internal.sessions WHERE pid = %s"
        assert owner.execute(bound, [connection.pid]).fetchone()[0] == 1
        connection.close()
        assert owner.execute(bound, [connection.pid]).fetchone()[0] == 0
    with pytest.raises(GuardError, match="closed"):
        connection.query("SELECT 1")
