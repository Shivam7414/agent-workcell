"""Documents follow the same guard as every other table. These tests name the document rules one by one.

The attack suite and the parity test already compare every document table with the oracle. The tests here pick
people for whom each rule matters, and first prove through the owner connection that the hidden rows exist.
"""

import psycopg
import pytest

from agent_workcell.guard import Principal
from tests.security.oracle import SENT

pytestmark = pytest.mark.db


def supplier_user(guard_db, supplier_id: int) -> Principal:
    user = next(user for user in guard_db.tables["users"] if user["supplier_id"] == supplier_id)
    return Principal("supplier_user", user["id"], {"supplier_id": supplier_id})


def owner_count(guard_db, query: str, params: list) -> int:
    with psycopg.connect(guard_db.owner_url) as owner:
        return owner.execute(query, params).fetchone()[0]


def doc_numbers(connection, where: str = "true") -> set[str]:
    return {row[0] for row in connection.query(f"SELECT doc_number FROM documents WHERE {where}").rows}


def test_a_supplier_user_never_sees_the_internal_evaluation_of_their_own_supplier(guard_db):
    evaluated = {d["supplier_id"] for d in guard_db.tables["documents"] if d["kind"] == "supplier_evaluation"}
    with_agreement = {d["supplier_id"] for d in guard_db.tables["documents"] if d["kind"] == "supply_agreement"}
    supplier_id = min(evaluated & with_agreement)
    assert owner_count(guard_db, "SELECT count(*) FROM document_chunks c JOIN document_versions v "
                                 "ON v.id = c.document_version_id JOIN documents d ON d.id = v.document_id "
                                 "WHERE d.kind = 'supplier_evaluation' AND d.supplier_id = %s", [supplier_id]) > 0

    connection = guard_db.connect(supplier_user(guard_db, supplier_id))
    try:
        assert doc_numbers(connection, "kind = 'supply_agreement'"), "the person must see their own agreement"
        assert not doc_numbers(connection, "kind = 'supplier_evaluation'")
        found = connection.query("SELECT count(*) FROM document_chunks WHERE content ILIKE '%Internal evaluation%'")
        assert found.rows[0][0] == 0
    finally:
        connection.close()


def test_a_supplier_user_sees_an_order_document_only_once_the_order_was_sent(guard_db):
    orders = {order["id"]: order for order in guard_db.tables["purchase_orders"]}
    shared = [d for d in guard_db.tables["documents"] if d["audience"] == "supplier" and d["purchase_order_id"]]
    unsent = [d for d in shared if orders[d["purchase_order_id"]]["status"] not in SENT]
    supplier_id = next(d["supplier_id"] for d in unsent
                       if any(o["supplier_id"] == d["supplier_id"] and orders[o["purchase_order_id"]]["status"] in SENT
                              for o in shared))
    hidden = {d["doc_number"] for d in unsent if d["supplier_id"] == supplier_id}
    shown = {d["doc_number"] for d in shared
             if d["supplier_id"] == supplier_id and orders[d["purchase_order_id"]]["status"] in SENT}

    connection = guard_db.connect(supplier_user(guard_db, supplier_id))
    try:
        seen = doc_numbers(connection, "purchase_order_id IS NOT NULL")
    finally:
        connection.close()
    assert shown <= seen
    assert not hidden & seen


def test_a_buyer_never_sees_admin_documents_or_the_documents_of_another_buyers_orders(guard_db):
    buyer = guard_db.principals["buyer"]
    orders = {order["id"]: order for order in guard_db.tables["purchase_orders"]}
    documents = guard_db.tables["documents"]
    admin_only = {d["doc_number"] for d in documents if d["audience"] == "admins"}
    others = {d["doc_number"] for d in documents
              if d["purchase_order_id"] and orders[d["purchase_order_id"]]["buyer_id"] != buyer.user_id}
    own = {d["doc_number"] for d in documents
           if d["purchase_order_id"] and orders[d["purchase_order_id"]]["buyer_id"] == buyer.user_id}
    assert admin_only and others and own

    seen = doc_numbers(guard_db.shared("buyer"))
    assert own <= seen
    assert "Purchasing policy" in {row[0] for row in guard_db.shared("buyer").query(
        "SELECT title FROM documents").rows}
    assert not (admin_only | others) & seen


def test_the_text_of_a_hidden_document_cannot_be_found_by_searching_the_chunks(guard_db):
    assert owner_count(guard_db, "SELECT count(*) FROM document_chunks WHERE content LIKE %s",
                       ["%Planned spending for 2026%"]) > 0
    for label in ("buyer", "supplier_big", "supplier_small"):
        found = guard_db.shared(label).query(
            "SELECT count(*) FROM document_chunks WHERE content LIKE '%Planned spending for 2026%'")
        assert found.rows[0][0] == 0, label
    found = guard_db.shared("admin").query(
        "SELECT count(*) FROM document_chunks WHERE content LIKE '%Planned spending for 2026%'")
    assert found.rows[0][0] > 0
