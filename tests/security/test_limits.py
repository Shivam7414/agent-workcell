"""Every query: one statement, read-only, a time limit, a row cap and a size cap."""

import time

import pytest

from agent_workcell.guard import Limits, QueryError

pytestmark = pytest.mark.db


def test_the_row_cap_cuts_the_result_and_says_so(guard_db):
    with guard_db.connect(guard_db.principals["admin"], Limits(max_rows=10)) as connection:
        capped = connection.query("SELECT id FROM purchase_orders")
        small = connection.query("SELECT id FROM departments")
    assert len(capped.rows) == 10 and capped.truncated
    assert 0 < len(small.rows) < 10 and not small.truncated


def test_the_size_cap_cuts_the_result_and_says_so(guard_db):
    with guard_db.connect(guard_db.principals["admin"], Limits(max_chars=1000)) as connection:
        capped = connection.query("SELECT repeat('x', 600) FROM generate_series(1, 5)")
        small = connection.query("SELECT repeat('x', 100) FROM generate_series(1, 5)")
    assert len(capped.rows) == 1 and capped.truncated
    assert len(small.rows) == 5 and not small.truncated


def test_a_slow_query_is_stopped_at_the_time_limit(guard_db):
    with guard_db.connect(guard_db.principals["admin"], Limits(timeout_ms=200)) as connection:
        started = time.monotonic()
        with pytest.raises(QueryError) as stopped:
            connection.query("SELECT pg_sleep(5)")
    assert stopped.value.sqlstate == "57014"
    assert time.monotonic() - started < 3


def test_the_model_cannot_lift_the_time_limit(guard_db):
    with guard_db.connect(guard_db.principals["admin"], Limits(timeout_ms=200)) as connection:
        with pytest.raises(QueryError, match="was stopped"):
            connection.query("SELECT set_config('statement_timeout', '0', false), pg_sleep(3)")
        connection.query("SELECT set_config('statement_timeout', '0', false)")
        with pytest.raises(QueryError, match="was stopped"):
            connection.query("SELECT pg_sleep(3)")


def test_each_query_runs_in_a_read_only_transaction(guard_db):
    with guard_db.connect(guard_db.principals["admin"]) as connection:
        assert connection.query("SELECT current_setting('transaction_read_only')").rows == [("on",)]


def test_only_one_query_runs_and_anything_else_is_refused_by_the_database(guard_db):
    with guard_db.connect(guard_db.principals["admin"]) as connection:
        for text in ("SELECT 1; SELECT 2", "SET statement_timeout = 0", "SHOW search_path", "BEGIN", "COMMIT",
                     "VACUUM purchase_orders", "LISTEN x", "PREPARE p AS SELECT 1"):
            with pytest.raises(QueryError) as refused:
                connection.query(text)
            assert refused.value.sqlstate == "42601", text
        # The checks above pass only if a query can run at all.
        assert connection.query("SELECT 1").rows == [(1,)]


def test_a_failed_query_leaves_the_connection_usable(guard_db):
    with guard_db.connect(guard_db.principals["buyer"]) as connection:
        with pytest.raises(QueryError):
            connection.query("SELECT 1 / 0")
        assert connection.query("SELECT 2").rows == [(2,)]


def test_a_permission_error_does_not_name_real_tables_schemas_or_roles(guard_db):
    with guard_db.connect(guard_db.principals["buyer"]) as connection:
        for text in ("SELECT * FROM public.suppliers", "SELECT * FROM guard_internal.sessions"):
            with pytest.raises(QueryError) as refused:
                connection.query(text)
            message = refused.value.message.lower()
            assert refused.value.sqlstate == "42501"
            for word in ("public", "suppliers", "guard_internal", "sessions", "workcell", "erp"):
                assert word not in message, (text, message)
