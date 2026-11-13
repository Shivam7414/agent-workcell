"""The document tools end to end: real HTTP, real MCP, the guarded database, a scripted runtime, the fake embedder.

The tools take no user, role or tenant argument (test_answer_server.py checks the arguments). What a person can find
or read is what the guard views let them read.
"""

from __future__ import annotations

import re

import pytest

from agent_workcell.documents.embed import EmbeddingError, FakeEmbedder
from agent_workcell.guard import Principal
from agent_workcell.runtime import Call, FakeRuntime, Say
from reference_erp import seed
from tests.security import oracle
from tests.security.test_answer_server import as_json, post

pytestmark = pytest.mark.db

DOC_NUMBER = re.compile(r"\bD-\d{6}\b")


@pytest.fixture(scope="module")
def server(guard_db, tmp_path_factory):
    from tests.security.servers import answer_server

    with answer_server(guard_db, tmp_path_factory.mktemp("workcell-data"), embedder=FakeEmbedder()) as running:
        yield running


def tool_results(server, principal: Principal, calls: list[Call]) -> list[str]:
    runtime = FakeRuntime(calls)
    server.service.runtime = runtime
    status, events = post(server, {"principal": as_json(principal), "question": "What do the documents say?"})
    assert status == 200 and events[-1][0] == "finished" and events[-1][1]["ok"], events[-1]
    assert len(runtime.results) == len(calls)
    return runtime.results


def readable_numbers(guard_db, principal) -> set[str]:
    return {d["doc_number"] for d in oracle.visible_rows(guard_db.tables, principal)["documents"]}


def supplier_with(guard_db, kind: str) -> tuple[dict, Principal]:
    document = next(d for d in guard_db.tables["documents"] if d["kind"] == kind and d["deleted_at"] is None
                    and d["audience"] == "supplier")
    user = next(u for u in guard_db.tables["users"] if u["supplier_id"] == document["supplier_id"])
    return document, Principal("supplier_user", user["id"], {"supplier_id": document["supplier_id"]})


@pytest.mark.parametrize("label", ["buyer", "supplier_big", "supplier_small"])
def test_search_returns_only_documents_the_person_may_read(server, guard_db, label):
    principal = guard_db.principals[label]
    queries = ["payment terms", "planned spending reserve repairs", "internal evaluation overall score",
               "quality certificate batch", "delivery address dock"]
    results = tool_results(server, principal, [Call("search_documents", {"query": q, "limit": 20}) for q in queries])
    found = {number for text in results for number in DOC_NUMBER.findall(text)}
    assert found <= readable_numbers(guard_db, principal)
    admin = tool_results(server, guard_db.principals["admin"], [Call("search_documents", {"query": q, "limit": 20})
                                                                 for q in queries])
    assert {number for text in admin for number in DOC_NUMBER.findall(text)} - readable_numbers(guard_db, principal), \
        "the admin must find documents this person cannot, or the check proves nothing"


def test_reading_a_document_the_person_may_not_read_looks_exactly_like_one_that_does_not_exist(server, guard_db):
    principal = guard_db.principals["supplier_small"]
    hidden = sorted({d["doc_number"] for d in guard_db.tables["documents"]} - readable_numbers(guard_db, principal))[0]
    hidden_text, missing_text = tool_results(server, principal, [
        Call("read_document", {"doc_number": hidden}), Call("read_document", {"doc_number": "D-999999"})])
    assert hidden_text.replace(hidden, "X") == missing_text.replace("D-999999", "X")
    admin_text = tool_results(server, guard_db.principals["admin"], [Call("read_document", {"doc_number": hidden})])[0]
    assert f"Document {hidden}" in admin_text


def test_read_document_gives_passages_in_order_with_citations_and_the_versions(server, guard_db):
    versions: dict[int, list] = {}
    for version in guard_db.tables["document_versions"]:
        versions.setdefault(version["document_id"], []).append(version)
    document = next(d for d in guard_db.tables["documents"] if d["kind"] == "supply_agreement"
                    and d["deleted_at"] is None and len(versions[d["id"]]) > 1
                    and all(v["status"] == "ready" for v in versions[d["id"]]))
    text = tool_results(server, guard_db.principals["admin"], [
        Call("read_document", {"doc_number": document["doc_number"], "first_passage": 2, "count": 2})])[0]
    current = max(v["version_no"] for v in versions[document["id"]])
    assert f"Version {current}, passages 2 to 3 of 5." in text
    assert "Versions: 1 (ready), 2 (ready)" in text
    assert f"[2] {document['doc_number']}" in text and f"version {current}, page 1, section" in text
    assert "read on with first_passage=4" in text

    first = tool_results(server, guard_db.principals["admin"], [
        Call("read_document", {"doc_number": document["doc_number"], "version": 1, "first_passage": 2, "count": 1})])[0]
    assert "Version 1, passages 2 to 2 of 5." in first


def test_a_version_that_failed_shows_its_status_and_reason_and_no_text(server, guard_db):
    failed = next(v for v in guard_db.tables["document_versions"] if v["status"] == "failed")
    document = next(d for d in guard_db.tables["documents"] if d["id"] == failed["document_id"])
    text = tool_results(server, guard_db.principals["admin"], [
        Call("read_document", {"doc_number": document["doc_number"], "version": failed["version_no"]})])[0]
    assert f"Version {failed['version_no']} has status failed and no text. Reason: {failed['error']}" in text
    assert "<passage>" not in text


def test_hostile_text_in_a_passage_cannot_close_its_block_or_open_a_trusted_one(server, guard_db):
    hostile = {c["document_version_id"]: c for c in guard_db.tables["document_chunks"]
               if all(note in c["content"] for note in seed.HOSTILE_NOTES)}
    version = next(v for v in guard_db.tables["document_versions"] if v["id"] in hostile)
    document = next(d for d in guard_db.tables["documents"] if d["id"] == version["document_id"])
    text = tool_results(server, guard_db.principals["admin"], [
        Call("read_document", {"doc_number": document["doc_number"], "version": version["version_no"]})])[0]
    raw = hostile[version["id"]]["content"]
    assert raw not in text
    assert raw.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") in text
    assert text.count("<passage>") == text.count("</passage>") == len(re.findall(r"^\[\d+\] ", text, re.M))


def test_search_by_meaning_finds_a_passage_whose_words_do_not_all_match(server, guard_db):
    document, principal = supplier_with(guard_db, "supply_agreement")
    text = tool_results(server, principal, [Call("search_documents", {"query": "payment terms zorblax"})])[0]
    assert "(words and meaning)" in text
    assert 'section "2. Payment terms"' in text
    assert DOC_NUMBER.findall(text) and set(DOC_NUMBER.findall(text)) <= readable_numbers(guard_db, principal)


def test_when_the_embedding_provider_fails_search_still_runs_on_words_through_the_guard(server, guard_db):
    class Unavailable:
        model, dimensions = "m", 3

        def embed(self, texts):
            raise EmbeddingError("The embedding provider answered HTTP 503.")

    principal = guard_db.principals["supplier_small"]
    server.service.embedder = Unavailable()
    try:
        text = tool_results(server, principal, [Call("search_documents", {"query": "payment terms"})])[0]
    finally:
        server.service.embedder = FakeEmbedder()
    assert "match words only" in text
    assert set(DOC_NUMBER.findall(text)) <= readable_numbers(guard_db, principal)


def test_every_document_tool_call_is_in_the_audit(server, guard_db):
    seen = {}

    def script(request):
        seen["run_id"] = request.run_id
        return [Call("search_documents", {"query": "approval limits administrator"}),
                Call("read_document", {"doc_number": "D-000001", "count": 2})]

    runtime = FakeRuntime(script)
    server.service.runtime = runtime
    status, _ = post(server, {"principal": as_json(guard_db.principals["buyer"]), "question": "Approval limits?"})
    assert status == 200
    calls = server.service.audit.tool_calls(seen["run_id"])
    assert [call["tool"] for call in calls] == ["search_documents", "read_document"]
    assert calls[0]["rows"] >= 1 and calls[1]["rows"] == 2


def test_the_sources_come_from_the_rows_the_tools_returned_and_mark_what_the_answer_cites(server, guard_db):
    document = next(d for d in guard_db.tables["documents"] if d["title"] == "Purchasing policy")
    runtime = FakeRuntime([Call("search_documents", {"query": "approval limits administrator", "limit": 3}),
                           Call("read_document", {"doc_number": document["doc_number"], "count": 2}),
                           Say(f"Orders above the limit need an administrator ({document['doc_number']}).")])
    server.service.runtime = runtime
    status, events = post(server, {"principal": as_json(guard_db.principals["admin"]), "question": "Approval?"})
    assert status == 200
    assert [kind for kind, _ in events][-2:] == ["sources", "finished"]
    sources = events[-2][1]["documents"]

    returned = {number for text in runtime.results for number in DOC_NUMBER.findall(text)}
    assert {source["doc_number"] for source in sources} == returned
    by_number = {d["doc_number"]: d for d in guard_db.tables["documents"]}
    chunks = {(c["document_version_id"], c["chunk_no"]) for c in guard_db.tables["document_chunks"]}
    version_ids = {(v["document_id"], v["version_no"]): v["id"] for v in guard_db.tables["document_versions"]}
    for source in sources:
        assert source["title"] == by_number[source["doc_number"]]["title"], "the title comes from the database"
        assert source["cited"] == (source["doc_number"] == document["doc_number"])
        version_id = version_ids[(by_number[source["doc_number"]]["id"], source["version_no"])]
        assert all((version_id, passage["passage"]) in chunks for passage in source["passages"])
    assert sources[0]["doc_number"] == document["doc_number"], "cited documents come first"


def test_an_answer_that_read_no_document_has_no_sources(server, guard_db):
    server.service.runtime = FakeRuntime([Call("run_query", {"sql": "SELECT count(*) FROM documents"}), Say("Done.")])
    status, events = post(server, {"principal": as_json(guard_db.principals["admin"]), "question": "How many?"})
    assert status == 200 and "sources" not in [kind for kind, _ in events]
