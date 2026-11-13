"""Hybrid search through the guard: words and meaning, only the passages the person may read.

Search is fixed SQL on the person's guarded connection. These tests compare its results with the oracle's view of
who may read which document, so a search that reached past the guard would fail them.
"""

from __future__ import annotations

import psycopg
import pytest

from agent_workcell.documents.embed import FakeEmbedder
from agent_workcell.documents.search import search
from agent_workcell.guard import Principal
from tests.security import oracle

pytestmark = pytest.mark.db

QUERIES = ["payment terms", "invoices due date", "quality certificate batch", "internal evaluation score",
           "planned spending budget", "delivery address dock", "quote comparison chosen supplier",
           "approval limits administrator"]
PEOPLE = ["admin", "buyer", "supplier_big", "supplier_small", "supplier_without_id"]


def current_versions(tables) -> dict[int, int]:
    """document id -> its current version number: the highest one that is ready."""
    current: dict[int, int] = {}
    for version in tables["document_versions"]:
        if version["status"] == "ready":
            current[version["document_id"]] = max(current.get(version["document_id"], 0), version["version_no"])
    return current


@pytest.mark.parametrize("label", PEOPLE)
@pytest.mark.parametrize("with_meaning", [False, True], ids=["words", "words-and-meaning"])
def test_search_finds_only_current_passages_of_documents_the_person_may_read(guard_db, label, with_meaning):
    readable = oracle.visible_rows(guard_db.tables, guard_db.principals[label])["documents"]
    allowed = {d["doc_number"]: d for d in readable if d["deleted_at"] is None}
    current = current_versions(guard_db.tables)
    embedder = FakeEmbedder() if with_meaning else None
    found = 0
    for query in QUERIES:
        for hit in search(guard_db.shared(label), query, embedder, limit=20):
            assert hit.doc_number in allowed, (label, query, hit.doc_number)
            assert hit.version_no == current[allowed[hit.doc_number]["id"]], (label, hit.doc_number)
            found += 1
    if allowed:
        assert found, f"{label} may read documents, so some query must find something"


def test_what_one_person_cannot_find_exists_for_another(guard_db):
    """The filter must not pass by finding nothing: the admin finds the budget plan and the internal evaluations."""
    admin_titles = {hit.title for hit in search(guard_db.shared("admin"), "planned spending reserve repairs", limit=20)}
    assert "Budget plan 2026" in admin_titles
    for label in ("buyer", "supplier_big", "supplier_small"):
        titles = {hit.title for hit in search(guard_db.shared(label), "planned spending reserve repairs", limit=20)}
        assert "Budget plan 2026" not in titles, label


def test_a_supplier_never_finds_the_internal_evaluation_of_their_own_supplier(guard_db):
    evaluated = {d["supplier_id"] for d in guard_db.tables["documents"] if d["kind"] == "supplier_evaluation"}
    supplier_id = min(evaluated)
    user = next(u for u in guard_db.tables["users"] if u["supplier_id"] == supplier_id)
    connection = guard_db.connect(Principal("supplier_user", user["id"], {"supplier_id": supplier_id}))
    try:
        for embedder in (None, FakeEmbedder()):
            hits = search(connection, "internal evaluation overall score", embedder, limit=20)
            assert not [hit for hit in hits if hit.title.startswith("Supplier evaluation")]
    finally:
        connection.close()
    admin_hits = search(guard_db.shared("admin"), "internal evaluation overall score", limit=20)
    assert any(hit.title.startswith("Supplier evaluation") for hit in admin_hits)


def test_meaning_finds_passages_when_the_exact_words_do_not_all_match(guard_db):
    """Full-text search needs every word; the made-up word stops it. The embedding still finds the passages."""
    query = "payment terms zorblax"
    assert search(guard_db.shared("admin"), query) == []
    hits = search(guard_db.shared("admin"), query, FakeEmbedder())
    assert hits
    assert hits[0].section == "2. Payment terms"
    assert hits[0].doc_number in hits[0].citation() and f"version {hits[0].version_no}" in hits[0].citation()


def test_embeddings_of_another_model_are_never_compared(guard_db):
    assert search(guard_db.shared("admin"), "payment terms zorblax", FakeEmbedder(model="another-model")) == []
    assert search(guard_db.shared("admin"), "payment terms zorblax", FakeEmbedder(dimensions=32)) == []


@pytest.mark.parametrize("text", ["'; DROP TABLE documents; --", "%(text)s %s {limit}", "payment) OR (true",
                                  "\" OR 1=1 --", "<script>alert(1)</script>", "   "])
def test_hostile_search_text_is_only_ever_a_value(guard_db, text):
    for embedder in (None, FakeEmbedder()):
        search(guard_db.shared("supplier_small"), text, embedder)
    with psycopg.connect(guard_db.owner_url) as owner:
        assert owner.execute("SELECT count(*) FROM documents").fetchone()[0] == len(guard_db.tables["documents"])
