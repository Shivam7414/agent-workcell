"""Uploading and processing documents: the documents role, the upload API, the job queue, and access to the result.

The passages a worker writes are read back only through the guard, so a new file follows its document's access rules
like the seeded passages do. Every test removes the versions it added, so later tests see the seeded rows only.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import quote

import httpx2
import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from agent_workcell.config import ConfigError
from agent_workcell.documents import store
from agent_workcell.documents.embed import EmbeddingError, FakeEmbedder
from agent_workcell.documents.search import search
from agent_workcell.documents.store import DocumentNames, Upload, check_role, install, process_next, register
from agent_workcell.guard import Principal
from tests.security.conftest import NAMES
from tests.security.servers import HOST_KEY, answer_server

pytestmark = pytest.mark.db

DOC_NAMES = DocumentNames(role="workcell_test_documents", schema="workcell_test_documents")
PASSWORD = "documents-test-only"
SCHEMA = "public"


@pytest.fixture(scope="module")
def documents(guard_db):
    with psycopg.connect(guard_db.owner_url) as conn:
        install(conn, SCHEMA, PASSWORD, DOC_NAMES)
    yield make_conninfo(guard_db.owner_url, user=DOC_NAMES.role, password=PASSWORD)


@pytest.fixture(autouse=True)
def remove_added_versions(guard_db):
    """Each test may add versions. Take them out again, with their passages and jobs."""
    with psycopg.connect(guard_db.owner_url) as conn:
        last = conn.execute("SELECT COALESCE(MAX(id), 0) FROM document_versions").fetchone()[0]
    yield
    with psycopg.connect(guard_db.owner_url) as conn:
        conn.execute("DELETE FROM document_chunk_embeddings WHERE document_version_id > %s", [last])
        conn.execute("DELETE FROM document_chunks WHERE document_version_id > %s", [last])
        conn.execute("DELETE FROM document_versions WHERE id > %s", [last])  # jobs go with them


def writer(url: str) -> psycopg.Connection:
    return psycopg.connect(url, autocommit=True)


def shared_agreement(guard_db) -> tuple[dict, Principal]:
    """A supply agreement that is shared with its supplier, and a user of that supplier."""
    document = next(d for d in guard_db.tables["documents"]
                    if d["kind"] == "supply_agreement" and d["deleted_at"] is None)
    user = next(u for u in guard_db.tables["users"] if u["supplier_id"] == document["supplier_id"])
    return document, Principal("supplier_user", user["id"], {"supplier_id": document["supplier_id"]})


def other_supplier(guard_db, supplier_id: int) -> Principal:
    user = next(u for u in guard_db.tables["users"] if u["supplier_id"] not in (None, supplier_id))
    return Principal("supplier_user", user["id"], {"supplier_id": user["supplier_id"]})


def received_file(tmp_path: Path, content: bytes) -> tuple[Path, str]:
    path = tmp_path / "received.bin"
    path.write_bytes(content)
    return path, hashlib.sha256(content).hexdigest()


def add_version(url, tmp_path, document_id: int, name: str, content: bytes) -> int:
    path, sha = received_file(tmp_path, content)
    media = {"md": "text/markdown", "txt": "text/plain", "csv": "text/csv"}[name.rsplit(".", 1)[1]]
    with writer(url) as conn:
        version_id, _ = register(conn, SCHEMA, tmp_path / "documents",
                                 Upload(document_id, name, media, len(content), sha, 1), path, DOC_NAMES)
    return version_id


def version_row(guard_db, version_id: int) -> tuple:
    with psycopg.connect(guard_db.owner_url) as conn:
        return conn.execute(
            "SELECT v.status, v.error, j.status, j.attempts, (SELECT count(*) FROM document_chunks c "
            "WHERE c.document_version_id = v.id) FROM document_versions v "
            f"JOIN {DOC_NAMES.schema}.jobs j ON j.document_version_id = v.id WHERE v.id = %s", [version_id]).fetchone()


def found(guard_db, principal: Principal, phrase: str) -> int:
    connection = guard_db.connect(principal)
    try:
        return connection.query(f"SELECT count(*) FROM document_chunks WHERE content LIKE '%{phrase}%'").rows[0][0]
    finally:
        connection.close()


# --------------------------------------------------------------------------------------------------------------------
# The documents role


def test_the_documents_role_checks_out(documents):
    with writer(documents) as conn:
        assert check_role(conn, SCHEMA, DOC_NAMES) == []


R = DOC_NAMES.role
BREAKS = [
    (f"GRANT SELECT ON public.suppliers TO {R}", f"REVOKE SELECT ON public.suppliers FROM {R}",
     "SELECT on public.suppliers"),
    (f"GRANT SELECT ON public.documents TO {R}", f"REVOKE SELECT ON public.documents FROM {R}",
     "SELECT on public.documents"),
    (f"GRANT SELECT (content) ON public.document_chunks TO {R}",
     f"REVOKE SELECT (content) ON public.document_chunks FROM {R}", "SELECT on public.document_chunks"),
    (f"GRANT DELETE ON public.document_chunks TO {R}", f"REVOKE DELETE ON public.document_chunks FROM {R}",
     "DELETE on public.document_chunks"),
    (f"GRANT UPDATE (storage_key) ON public.document_versions TO {R}",
     f"REVOKE UPDATE (storage_key) ON public.document_versions FROM {R}", "exactly status and error"),
    (f"GRANT DELETE ON {DOC_NAMES.schema}.jobs TO {R}", f"REVOKE DELETE ON {DOC_NAMES.schema}.jobs FROM {R}",
     f"DELETE on {DOC_NAMES.schema}.jobs"),
    (f"GRANT USAGE ON SEQUENCE public.users_id_seq TO {R}", f"REVOKE USAGE ON SEQUENCE public.users_id_seq FROM {R}",
     "sequence public.users_id_seq"),
    (f"GRANT CREATE ON SCHEMA public TO {R}", f"REVOKE CREATE ON SCHEMA public FROM {R}", "schema public"),
    (f"ALTER ROLE {R} CREATEDB", f"ALTER ROLE {R} NOCREATEDB", "CREATEDB"),
    (f"GRANT {NAMES.binder_role} TO {R}", f"REVOKE {NAMES.binder_role} FROM {R}", "member of another role"),
    (f"REVOKE INSERT ON public.document_chunks FROM {R}", f"GRANT INSERT ON public.document_chunks TO {R}",
     "missing INSERT on public.document_chunks"),
    (f"GRANT SELECT ON public.document_chunk_embeddings TO {R}",
     f"REVOKE SELECT ON public.document_chunk_embeddings FROM {R}", "SELECT on public.document_chunk_embeddings"),
    (f"REVOKE INSERT ON public.document_chunk_embeddings FROM {R}",
     f"GRANT INSERT ON public.document_chunk_embeddings TO {R}", "missing INSERT on public.document_chunk_embeddings"),
]


@pytest.mark.parametrize(("breaking", "fixing", "expected"), BREAKS)
def test_the_check_finds_each_extra_or_missing_right(guard_db, documents, breaking, fixing, expected):
    with psycopg.connect(guard_db.owner_url, autocommit=True) as owner:
        owner.execute(breaking)
        try:
            with writer(documents) as conn:
                problems = check_role(conn, SCHEMA, DOC_NAMES)
        finally:
            owner.execute(fixing)
    assert any(expected in problem for problem in problems), problems
    with writer(documents) as conn:
        assert check_role(conn, SCHEMA, DOC_NAMES) == []


def test_the_check_refuses_a_connection_that_is_not_the_documents_role(guard_db):
    with psycopg.connect(guard_db.agent_url, autocommit=True) as conn:
        problems = check_role(conn, SCHEMA, DOC_NAMES)
    assert any("not as the documents role" in problem for problem in problems)


@pytest.mark.parametrize("statement", [
    "SELECT * FROM public.suppliers",
    "SELECT * FROM public.documents",
    "SELECT content FROM public.document_chunks",
    "SELECT * FROM guard_internal.sessions",
    "UPDATE public.document_versions SET storage_key = 'x' WHERE id = 1",
    "UPDATE public.documents SET audience = 'supplier' WHERE id = 1",
    "DELETE FROM public.document_chunks",
    "INSERT INTO public.documents (doc_number, title, kind, audience) VALUES ('X', 'X', 'company_policy', 'staff')",
])
def test_the_documents_role_cannot_read_business_data_or_change_documents(documents, statement):
    with writer(documents) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(statement)


def test_the_server_will_not_start_when_the_documents_role_does_not_check_out(guard_db, tmp_path):
    with pytest.raises(ConfigError, match="documents role check failed"):
        with answer_server(guard_db, tmp_path, DOC_NAMES, documents_url=guard_db.agent_url):
            pass


# --------------------------------------------------------------------------------------------------------------------
# Processing


def test_an_uploaded_file_becomes_passages_that_follow_the_documents_access(guard_db, documents, tmp_path):
    document, owner_of_it = shared_agreement(guard_db)
    phrase = "Spare parts are kept for seven years"
    content = f"# Service\n\n{phrase} after the last delivery.\n\n## Contacts\n\nCall the service desk.\n".encode()
    version_id = add_version(documents, tmp_path, document["id"], "agreement.md", content)
    assert version_row(guard_db, version_id)[:3] == ("pending", None, "queued")
    assert found(guard_db, owner_of_it, phrase) == 0

    with writer(documents) as conn:
        assert process_next(conn, SCHEMA, tmp_path / "documents", DOC_NAMES) == "ready"
        assert process_next(conn, SCHEMA, tmp_path / "documents", DOC_NAMES) is None

    assert version_row(guard_db, version_id) == ("ready", None, "done", 1, 2)
    assert found(guard_db, owner_of_it, phrase) == 1
    assert found(guard_db, Principal("admin", 1), phrase) == 1
    assert found(guard_db, other_supplier(guard_db, document["supplier_id"]), phrase) == 0

    connection = guard_db.connect(owner_of_it)
    try:
        rows = connection.query(f"SELECT chunk_no, section FROM document_chunks WHERE document_version_id = "
                                f"{version_id} ORDER BY chunk_no").rows
    finally:
        connection.close()
    assert rows == [(1, "Service"), (2, "Service > Contacts")]


def test_with_an_embedder_each_passage_gets_one_embedding_and_search_finds_it_by_meaning(guard_db, documents,
                                                                                        tmp_path):
    document, owner_of_it = shared_agreement(guard_db)
    content = (b"# Warranty\n\nDefective parts are replaced free of charge for two years.\n\n"
               b"# Returns\n\nUnused stock may be sent back within 30 days.\n")
    version_id = add_version(documents, tmp_path, document["id"], "agreement.md", content)
    with writer(documents) as conn:
        assert process_next(conn, SCHEMA, tmp_path / "documents", DOC_NAMES, embedder=FakeEmbedder()) == "ready"
    with psycopg.connect(guard_db.owner_url) as owner:
        rows = owner.execute("SELECT chunk_no, model FROM document_chunk_embeddings WHERE document_version_id = %s "
                             "ORDER BY chunk_no", [version_id]).fetchall()
    assert rows == [(1, "fake-64"), (2, "fake-64")]

    query = "warranty defective zorblax"  # the made-up word stops full-text search; meaning still finds it
    connection = guard_db.connect(owner_of_it)
    try:
        hits = search(connection, query, FakeEmbedder())
    finally:
        connection.close()
    assert hits and hits[0].content.startswith("Defective parts are replaced")
    stranger = guard_db.connect(other_supplier(guard_db, document["supplier_id"]))
    try:
        assert not [hit for hit in search(stranger, query, FakeEmbedder()) if "Defective parts" in hit.content]
    finally:
        stranger.close()


def test_when_the_embedding_provider_fails_no_passage_is_written_and_the_job_is_retried(guard_db, documents,
                                                                                       tmp_path):
    class Unavailable:
        model, dimensions = "m", 3

        def embed(self, texts):
            raise EmbeddingError("The embedding provider answered HTTP 503.")

    document, _ = shared_agreement(guard_db)
    version_id = add_version(documents, tmp_path, document["id"], "ok.txt", b"Fine text.")
    with writer(documents) as conn:
        assert process_next(conn, SCHEMA, tmp_path / "documents", DOC_NAMES, embedder=Unavailable()) == "retry"
    assert version_row(guard_db, version_id) == ("pending", None, "queued", 1, 0)


def test_a_file_that_cannot_be_read_fails_at_once_with_a_message_and_no_passages(guard_db, documents, tmp_path):
    document, owner_of_it = shared_agreement(guard_db)
    version_id = add_version(documents, tmp_path, document["id"], "broken.txt", b"caf\xe9 \xff\xfe")
    with writer(documents) as conn:
        assert process_next(conn, SCHEMA, tmp_path / "documents", DOC_NAMES) == "failed"
    assert version_row(guard_db, version_id) == ("failed", "The file is not UTF-8 text.", "failed", 1, 0)

    connection = guard_db.connect(owner_of_it)
    try:
        status, error = connection.query(
            f"SELECT status, error FROM document_versions WHERE id = {version_id}").rows[0]
    finally:
        connection.close()
    assert (status, error) == ("failed", "The file is not UTF-8 text.")


def test_a_file_with_no_text_fails(guard_db, documents, tmp_path):
    document, _ = shared_agreement(guard_db)
    version_id = add_version(documents, tmp_path, document["id"], "blank.txt", b"\n\n   \n")
    with writer(documents) as conn:
        assert process_next(conn, SCHEMA, tmp_path / "documents", DOC_NAMES) == "failed"
    assert version_row(guard_db, version_id)[:2] == ("failed", "The file holds no text.")


def test_a_fault_of_the_environment_is_retried_and_then_fails_with_a_fixed_message(guard_db, documents, tmp_path,
                                                                                 monkeypatch, capsys):
    document, _ = shared_agreement(guard_db)
    version_id = add_version(documents, tmp_path, document["id"], "ok.txt", b"Fine text.")

    def disk_fails(path, media_type):
        raise OSError(f"disk error while reading {path}")

    monkeypatch.setattr(store, "read_blocks", disk_fails)
    root = tmp_path / "documents"
    with writer(documents) as conn:
        assert process_next(conn, SCHEMA, root, DOC_NAMES, retry_delay_s=3600) == "retry"
        assert version_row(guard_db, version_id)[:4] == ("pending", None, "queued", 1)
        assert process_next(conn, SCHEMA, root, DOC_NAMES) is None, "a retried job waits for its delay"

        assert process_next(conn, SCHEMA, root, DOC_NAMES, retry_delay_s=0, max_attempts=2) is None
        with psycopg.connect(guard_db.owner_url) as owner:
            owner.execute(f"UPDATE {DOC_NAMES.schema}.jobs SET run_after = now() WHERE document_version_id = %s",
                          [version_id])
        assert process_next(conn, SCHEMA, root, DOC_NAMES, max_attempts=2) == "failed"

    status, error, job_status, attempts, passages = version_row(guard_db, version_id)
    assert (status, error, job_status, attempts, passages) == (
        "failed", "Processing failed after 2 attempts.", "failed", 2, 0)
    assert str(tmp_path) not in error, "the message people and the model see names no path"
    assert "disk error" in capsys.readouterr().err, "the detail goes to the server's log"


def test_a_job_whose_worker_stopped_is_taken_again_after_its_lease(guard_db, documents, tmp_path):
    document, _ = shared_agreement(guard_db)
    version_id = add_version(documents, tmp_path, document["id"], "ok.txt", b"Fine text.")
    with psycopg.connect(guard_db.owner_url) as owner:
        owner.execute(f"UPDATE {DOC_NAMES.schema}.jobs SET status = 'running', attempts = 1, "
                      "locked_until = now() + interval '1 hour' WHERE document_version_id = %s", [version_id])
    with writer(documents) as conn:
        assert process_next(conn, SCHEMA, tmp_path / "documents", DOC_NAMES) is None, "the lease still runs"
        with psycopg.connect(guard_db.owner_url) as owner:
            owner.execute(f"UPDATE {DOC_NAMES.schema}.jobs SET locked_until = now() - interval '1 second' "
                          "WHERE document_version_id = %s", [version_id])
        assert process_next(conn, SCHEMA, tmp_path / "documents", DOC_NAMES) == "ready"
    assert version_row(guard_db, version_id)[2:4] == ("done", 2)


def test_a_worker_whose_job_was_taken_over_writes_nothing(guard_db, documents, tmp_path):
    """Worker A takes a job and is slow. Its lease runs out, worker B takes the job and finishes it. When A comes
    back, it must neither add a second set of passages nor change the version B finished."""
    document, _ = shared_agreement(guard_db)
    version_id = add_version(documents, tmp_path, document["id"], "ok.md", b"# A\n\nOne.\n\n# B\n\nTwo.")
    root = tmp_path / "documents"
    with writer(documents) as slow, writer(documents) as fast:
        job = store.take_job(slow, SCHEMA, DOC_NAMES)
        assert job is not None and job.attempt == 1
        with psycopg.connect(guard_db.owner_url) as owner:
            owner.execute(f"UPDATE {DOC_NAMES.schema}.jobs SET locked_until = now() - interval '1 second' "
                          "WHERE id = %s", [job.id])
        assert process_next(fast, SCHEMA, root, DOC_NAMES) == "ready"
        assert store.run_job(slow, SCHEMA, root, job, DOC_NAMES) == "taken over"
    assert version_row(guard_db, version_id) == ("ready", None, "done", 2, 2)


def test_two_workers_never_take_the_same_job(guard_db, documents, tmp_path):
    document, _ = shared_agreement(guard_db)
    version_id = add_version(documents, tmp_path, document["id"], "ok.txt", b"Fine text.")
    with psycopg.connect(guard_db.owner_url) as holder:
        # Another worker is in the middle of taking this job: its row is locked.
        holder.execute(f"SELECT id FROM {DOC_NAMES.schema}.jobs WHERE document_version_id = %s FOR UPDATE",
                       [version_id])
        with writer(documents) as conn:
            assert process_next(conn, SCHEMA, tmp_path / "documents", DOC_NAMES) is None
        holder.rollback()
    with writer(documents) as conn:
        assert process_next(conn, SCHEMA, tmp_path / "documents", DOC_NAMES) == "ready"


def test_versions_of_one_document_are_numbered_in_order(guard_db, documents, tmp_path):
    document, _ = shared_agreement(guard_db)
    before = sum(1 for v in guard_db.tables["document_versions"] if v["document_id"] == document["id"])
    numbers = []
    for text in (b"First.", b"Second."):
        path, sha = received_file(tmp_path, text)
        with writer(documents) as conn:
            numbers.append(register(conn, SCHEMA, tmp_path / "documents",
                                    Upload(document["id"], "a.txt", "text/plain", len(text), sha, 1), path,
                                    DOC_NAMES)[1])
    assert numbers == [before + 1, before + 2]


# --------------------------------------------------------------------------------------------------------------------
# The upload API


@pytest.fixture(scope="module")
def server(guard_db, documents, tmp_path_factory):
    with answer_server(guard_db, tmp_path_factory.mktemp("workcell-data"), DOC_NAMES, documents_url=documents,
                       document_max_bytes=10_000) as running:
        yield running


def upload(server, document_id: int, name: str, content: bytes, key: str = HOST_KEY, by: str = "1") -> httpx2.Response:
    headers = {"X-File-Name": quote(name), "X-Uploaded-By": by}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    with httpx2.Client(timeout=30) as client:
        return client.post(f"{server.url}/v1/documents/{document_id}/versions", content=content, headers=headers)


def incoming_files(server) -> list[Path]:
    folder = server.settings.data_dir / "documents" / "incoming"
    return list(folder.iterdir()) if folder.exists() else []


def test_an_upload_through_the_api_is_stored_under_a_key_made_of_numbers_and_its_hash(guard_db, server):
    document, owner_of_it = shared_agreement(guard_db)
    name = "../../Übersicht?.md"
    content = b"# Notes\n\nUploaded through the API."
    response = upload(server, document["id"], name, content)
    assert response.status_code == 202, response.text
    version_id = response.json()["version_id"]

    with psycopg.connect(guard_db.owner_url) as conn:
        file_name, storage_key, uploaded_by = conn.execute(
            "SELECT file_name, storage_key, uploaded_by FROM document_versions WHERE id = %s", [version_id]).fetchone()
    assert file_name == name, "the name is kept as data"
    assert storage_key == f"{document['id']}/{response.json()['version_no']}/{hashlib.sha256(content).hexdigest()}"
    assert (server.settings.data_dir / "documents" / storage_key).read_bytes() == content
    assert uploaded_by == 1
    assert incoming_files(server) == []

    with writer(server.settings.documents_url) as conn:
        assert process_next(conn, SCHEMA, server.settings.data_dir / "documents", DOC_NAMES) == "ready"
    assert found(guard_db, owner_of_it, "Uploaded through the API") == 1


@pytest.mark.parametrize(("name", "content", "key", "by", "status"), [
    ("a.md", b"text", "wrong-key-0123456789-0123456789", "1", 401),
    ("a.md", b"text", "", "1", 401),
    ("a.pdf", b"%PDF-1.7", HOST_KEY, "1", 415),
    ("a.md", b"x" * 10_001, HOST_KEY, "1", 413),
    ("a.md", b"", HOST_KEY, "1", 400),
    ("a.md", b"text", HOST_KEY, "admin", 400),
    ("", b"text", HOST_KEY, "1", 400),
    ("a\nb.md", b"text", HOST_KEY, "1", 400),
])
def test_an_upload_that_is_refused_leaves_no_file_and_no_version(guard_db, server, name, content, key, by, status):
    document, _ = shared_agreement(guard_db)
    with psycopg.connect(guard_db.owner_url) as conn:
        before = conn.execute("SELECT count(*) FROM document_versions").fetchone()[0]
    assert upload(server, document["id"], name, content, key, by).status_code == status
    with psycopg.connect(guard_db.owner_url) as conn:
        assert conn.execute("SELECT count(*) FROM document_versions").fetchone()[0] == before
    assert incoming_files(server) == []


def test_an_upload_for_a_document_that_does_not_exist_is_refused(guard_db, server):
    response = upload(server, 999_999_999, "a.md", b"text")
    assert response.status_code == 404
    assert incoming_files(server) == []


def test_without_the_documents_setting_there_is_no_upload_route(guard_db, tmp_path):
    with answer_server(guard_db, tmp_path) as running:
        assert upload(running, 1, "a.md", b"text").status_code in (404, 405)
