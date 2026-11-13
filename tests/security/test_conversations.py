"""Conversations: a follow-up continues the person's own session, and never anyone else's.

The host application names a conversation; the server chooses the session id and keys it by the whole principal.
"""

from __future__ import annotations

import threading
import time

import pytest

from agent_workcell.guard import Principal
from agent_workcell.runtime import Call, FakeRuntime, Say, Wait
from agent_workcell.server import person_folder
from tests.security.test_answer_server import as_json, post

pytestmark = pytest.mark.db

COUNT = Call("run_query", {"sql": "SELECT count(*) FROM purchase_orders"})


@pytest.fixture(scope="module")
def server(guard_db, tmp_path_factory):
    from tests.security.servers import answer_server

    with answer_server(guard_db, tmp_path_factory.mktemp("workcell-data")) as running:
        yield running


def ask(server, principal: Principal, conversation_id: str | None, script=None, question: str = "How many?"):
    runtime = FakeRuntime(script or [Say("Done.")])
    server.service.runtime = runtime
    body = {"principal": as_json(principal), "question": question}
    if conversation_id is not None:
        body["conversation_id"] = conversation_id
    status, events = post(server, body)
    return status, events, runtime


def request_of(server, principal, conversation_id, **kwargs):
    status, events, runtime = ask(server, principal, conversation_id, **kwargs)
    assert status == 200 and events[-1][1]["ok"], events[-1]
    return runtime.requests[0]


def test_a_follow_up_resumes_the_session_the_first_answer_started(server, guard_db):
    buyer = guard_db.principals["buyer"]
    first = request_of(server, buyer, "orders-chat")
    second = request_of(server, buyer, "orders-chat")
    third = request_of(server, buyer, "orders-chat")
    assert first.session_id and not first.resume
    assert second.session_id == third.session_id == first.session_id
    assert second.resume and third.resume


def test_the_same_conversation_id_from_another_person_is_another_session(server, guard_db):
    buyer, admin = guard_db.principals["buyer"], guard_db.principals["admin"]
    mine = request_of(server, buyer, "shared-name")
    theirs = request_of(server, admin, "shared-name")
    assert theirs.session_id != mine.session_id and not theirs.resume
    assert theirs.workdir != mine.workdir, "each person has their own folder, so sessions never mix"


def test_a_changed_role_or_supplier_starts_with_no_history(server, guard_db):
    supplier = guard_db.principals["supplier_big"]
    before = request_of(server, supplier, "supplier-chat")
    moved = Principal(supplier.role, supplier.user_id, {"supplier_id": supplier.attributes["supplier_id"] + 1})
    after = request_of(server, moved, "supplier-chat")
    assert after.session_id != before.session_id and not after.resume
    assert after.workdir == person_folder(server.settings.data_dir, moved) != before.workdir


def test_without_a_conversation_id_every_answer_is_a_new_session(server, guard_db):
    buyer = guard_db.principals["buyer"]
    one, two = request_of(server, buyer, None), request_of(server, buyer, None)
    assert one.session_id != two.session_id and not one.resume and not two.resume


def test_the_persons_folder_and_sessions_are_kept_after_the_answer(server, guard_db):
    admin = guard_db.principals["admin"]
    used = request_of(server, admin, "kept")
    assert used.workdir.is_dir() and used.workdir.parent == server.settings.data_dir / "people"


@pytest.mark.parametrize("conversation_id", ["", "a b", "../x", "x" * 65, 42, ["list"], "naïve"])
def test_a_malformed_conversation_id_is_refused(server, guard_db, conversation_id):
    status, _, _ = ask(server, guard_db.principals["buyer"], conversation_id)
    assert status == 400


def test_one_conversation_runs_one_answer_at_a_time(server, guard_db):
    buyer = guard_db.principals["buyer"]
    runtime = FakeRuntime([Wait(2), Say("Slow.")])
    server.service.runtime = runtime
    body = {"principal": as_json(buyer), "question": "Slow?", "conversation_id": "busy"}
    first = threading.Thread(target=post, args=(server, body))
    first.start()
    try:
        deadline = time.monotonic() + 5
        while not server.service.busy_conversations and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.service.busy_conversations, "the first answer must be running"
        status, _ = post(server, body)
        assert status == 429
        other, _ = post(server, {**body, "conversation_id": "another"})
        assert other == 200, "another conversation of the same person may run at the same time"
    finally:
        first.join(timeout=10)
    assert not server.service.busy_conversations


def test_the_session_id_never_reaches_the_host(server, guard_db):
    status, events, runtime = ask(server, guard_db.principals["buyer"], "private", script=[COUNT, Say("Done.")])
    assert status == 200
    session_id = runtime.requests[0].session_id
    assert session_id not in str(events)
