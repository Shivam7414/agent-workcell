"""Per person and per table, the guard shows the rows the role rules allow: no more, no fewer.

The reference ERP has no application screens, so the reference is the role rules in reference_erp/README.md, written
again in Python in tests/security/oracle.py.
"""

import psycopg
import pytest
from psycopg import sql

from agent_workcell.guard.plan import install
from agent_workcell.policy import parse_policy
from tests.security import oracle
from tests.security.conftest import AGENT_PASSWORD, BINDER_PASSWORD, NAMES, POLICY_PATH, guard_outcome

pytestmark = pytest.mark.db


def test_the_oracle_covers_every_table_in_the_policy(guard_db):
    assert set(guard_db.policy.tables) == set(guard_db.tables)


@pytest.mark.parametrize("label", ["admin", "buyer", "supplier_big", "supplier_small", "supplier_without_id"])
def test_each_table_shows_the_rows_the_role_rules_allow(guard_db, label):
    expected = oracle.visible_rows(guard_db.tables, guard_db.principals[label])
    connection = guard_db.shared(label)
    with psycopg.connect(guard_db.owner_url) as owner:
        for table in guard_db.policy.tables:
            ids = sorted(row[0] for row in connection.query(f"SELECT id FROM {table}").rows)
            assert ids == sorted(row["id"] for row in expected[table]), (label, table)
            if not ids:
                real = owner.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier("public", table)))
                assert real.fetchone()[0] > 0, f"{table} must have rows, or an empty result proves nothing"


def test_the_hidden_columns_hold_data(guard_db):
    """The hidden-column queries in the attack suite are only meaningful if these columns are filled."""
    with psycopg.connect(guard_db.owner_url) as owner:
        assert owner.execute("SELECT count(password_hash) FROM public.users").fetchone()[0] > 0
        assert owner.execute("SELECT count(tax_id), count(bank_account) FROM public.suppliers").fetchone()[0] > 0


def test_the_suite_catches_a_policy_that_is_too_wide(guard_db, oracle_db):
    """Install a broken policy on purpose. The comparison must fail, or it could pass by doing nothing."""
    text = POLICY_PATH.read_text(encoding="utf-8")
    wide = text.replace('rows.buyer = "buyer_id = {user_id}"', 'rows.buyer = "true"')
    assert wide != text
    query = "SELECT id FROM purchase_orders"
    try:
        with psycopg.connect(guard_db.owner_url) as conn:
            install(conn, parse_policy(wide), AGENT_PASSWORD, BINDER_PASSWORD, NAMES)
        guard_db.close()
        assert guard_outcome(guard_db, "buyer", query) != oracle_db("buyer", query)
    finally:
        with psycopg.connect(guard_db.owner_url) as conn:
            install(conn, guard_db.policy, AGENT_PASSWORD, BINDER_PASSWORD, NAMES)
        guard_db.close()
    assert guard_outcome(guard_db, "buyer", query) == oracle_db("buyer", query)


def test_the_suite_catches_a_document_rule_that_forgets_the_audience(guard_db, oracle_db):
    """Drop the audience condition from the supplier rules of versions, chunks and embeddings. Internal documents
    about a supplier, and documents of orders not yet sent, would then reach that supplier. The comparison must see
    it."""
    text = POLICY_PATH.read_text(encoding="utf-8")
    wide = text.replace("WHERE d.audience = 'supplier' AND d.supplier_id = {supplier_id}",
                        "WHERE d.supplier_id = {supplier_id}")
    assert wide.count("WHERE d.supplier_id = {supplier_id}") == 3
    query = "SELECT id, content FROM document_chunks"
    people = ["supplier_big", "supplier_small"]
    try:
        with psycopg.connect(guard_db.owner_url) as conn:
            install(conn, parse_policy(wide), AGENT_PASSWORD, BINDER_PASSWORD, NAMES)
        guard_db.close()
        assert any(guard_outcome(guard_db, label, query) != oracle_db(label, query) for label in people)
    finally:
        with psycopg.connect(guard_db.owner_url) as conn:
            install(conn, guard_db.policy, AGENT_PASSWORD, BINDER_PASSWORD, NAMES)
        guard_db.close()
    for label in people:
        assert guard_outcome(guard_db, label, query) == oracle_db(label, query)
