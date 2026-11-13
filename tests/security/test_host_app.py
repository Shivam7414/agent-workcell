"""The reference host application: sign-in, the session, and the chat page in front of the answer server.

The principal comes from the signed session that sign-in created, never from the browser.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx2
import pytest

from agent_workcell.runtime import Call, FakeRuntime, Say
from reference_erp import seed
from reference_erp.host_app import HostSettings, create_host_app
from reference_erp.host_app.app import COOKIE, check_password, read_session, sign_session
from tests.security import oracle
from tests.security.servers import HOST_KEY, answer_server, free_port, serving

pytestmark = pytest.mark.db

SECRET = "session-secret-for-tests-only-0123"
STATIC = Path(__file__).parents[2] / "reference_erp" / "host_app" / "static"
COUNT_ORDERS = Call("run_query", {"sql": "SELECT count(*) FROM purchase_orders"})


@pytest.fixture(scope="module")
def apps(guard_db, tmp_path_factory):
    with answer_server(guard_db, tmp_path_factory.mktemp("workcell-host")) as server:
        settings = HostSettings(erp_url=guard_db.owner_url, answer_url=server.url, host_key=HOST_KEY,
                                session_secret=SECRET)
        port = free_port()
        with serving(create_host_app(settings), port) as url:
            yield server, url


def user(guard_db, label: str) -> dict:
    found = next(row for row in guard_db.tables["users"] if row["id"] == guard_db.principals[label].user_id)
    assert found["is_active"] and found["deleted_at"] is None, "the test person must be able to sign in"
    return found


def browser(url: str) -> httpx2.Client:
    return httpx2.Client(base_url=url, headers={"Origin": url}, follow_redirects=False, timeout=60)


def sign_in(url: str, email: str, password: str = seed.DEMO_PASSWORD) -> httpx2.Client:
    client = browser(url)
    response = client.post("/login", data={"email": email, "password": password})
    assert response.status_code == 303, response.text
    return client


def ask(client: httpx2.Client, body: dict) -> list[tuple[str, dict]]:
    events, kind = [], None
    with client.stream("POST", "/api/ask", json=body) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("event: "):
                kind = line[7:]
            elif line.startswith("data: "):
                events.append((kind, json.loads(line[6:])))
    return events


# Sign-in

def test_the_seeded_password_matches_and_a_wrong_one_does_not():
    stored = seed.password_hash(42)
    assert check_password(seed.DEMO_PASSWORD, stored)
    assert not check_password("wrong", stored)
    assert not check_password(seed.DEMO_PASSWORD, "md5$abc")
    assert not check_password(seed.DEMO_PASSWORD, "not a hash")


def test_a_wrong_password_and_an_unknown_email_get_the_same_refusal_and_no_session(apps, guard_db):
    _, url = apps
    client = browser(url)
    wrong = client.post("/login", data={"email": user(guard_db, "buyer")["email"], "password": "wrong"})
    unknown = client.post("/login", data={"email": "nobody@meridian.example", "password": seed.DEMO_PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.text == unknown.text and "The email or the password is wrong." in wrong.text
    assert COOKIE not in client.cookies


def test_signing_in_gives_a_cookie_scripts_cannot_read_and_other_sites_cannot_send(apps, guard_db):
    _, url = apps
    client = browser(url)
    response = client.post("/login", data={"email": user(guard_db, "buyer")["email"].upper(),
                                           "password": seed.DEMO_PASSWORD})
    assert response.status_code == 303 and response.headers["location"] == "/"
    cookie = response.headers["set-cookie"].lower()
    assert f"{COOKIE}=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/me").json() == {"name": user(guard_db, "buyer")["full_name"], "role": "buyer",
                                          "organisation": "Meridian Works"}


def test_without_a_session_nothing_is_served(apps):
    _, url = apps
    client = browser(url)
    assert client.get("/").status_code == 303 and client.get("/").headers["location"] == "/login"
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/ask", json={"question": "Hi"}).status_code == 401
    assert client.get("/api/notes").status_code == 401
    assert client.get("/api/files/abc").status_code == 401


# The principal comes from the session

def test_the_principal_comes_from_the_session_and_never_from_the_browser(apps, guard_db):
    server, url = apps
    server.service.runtime = FakeRuntime([COUNT_ORDERS], echo=True)
    person = guard_db.principals["supplier_small"]
    client = sign_in(url, user(guard_db, "supplier_small")["email"])
    admin = {"role": "admin", "user_id": guard_db.principals["admin"].user_id, "attributes": {}}
    events = ask(client, {"question": "How many orders?", "principal": admin, "role": "admin", "supplier_id": 1})
    finished = events[-1][1]
    visible = len(oracle.visible_rows(guard_db.tables, person)["purchase_orders"])
    assert finished["ok"] and f"count\n{visible}" in finished["answer"]
    assert visible < len(guard_db.tables["purchase_orders"])


def test_the_page_continues_its_conversation_and_a_bad_conversation_id_is_refused(apps, guard_db):
    server, url = apps
    runtime = FakeRuntime([COUNT_ORDERS])
    server.service.runtime = runtime
    client = sign_in(url, user(guard_db, "buyer")["email"])
    ask(client, {"question": "How many orders?", "conversation_id": "page-1"})
    ask(client, {"question": "And last month?", "conversation_id": "page-1"})
    first, second = runtime.requests
    assert second.session_id == first.session_id and second.resume and not first.resume
    assert client.post("/api/ask", json={"question": "Hi", "conversation_id": "../x"}).status_code == 400


def test_a_changed_forged_or_expired_session_is_refused(apps, guard_db):
    _, url = apps
    client = sign_in(url, user(guard_db, "buyer")["email"])
    session = read_session(SECRET, client.cookies[COOKIE])
    assert session["role"] == "buyer", "the real cookie is accepted"

    payload, signature = client.cookies[COOKIE].split(".")
    raised = sign_session(SECRET, {**session, "role": "admin"}).split(".")[0]
    for cookie in (f"{raised}.{signature}",                                   # changed, old signature
                   sign_session("another-secret-0123456789abcdef", {**session, "role": "admin"}),  # forged
                   sign_session(SECRET, {**session, "exp": int(time.time()) - 1}),             # expired
                   "garbage", f"{payload}.", ""):
        forged = browser(url)
        forged.cookies.set(COOKIE, cookie)
        assert forged.get("/api/me").status_code == 401, cookie


def test_a_post_from_another_site_is_refused(apps, guard_db):
    _, url = apps
    client = sign_in(url, user(guard_db, "buyer")["email"])
    for origin in ("http://example.invalid", None):
        headers = {"Origin": origin} if origin else {}
        if origin is None:
            client.headers.pop("Origin")
        assert client.post("/api/ask", json={"question": "Hi"}, headers=headers).status_code == 403
        assert client.post("/api/notes/clear", headers=headers).status_code == 403
        assert client.post("/logout", headers=headers).status_code == 403
        assert client.post("/login", data={"email": "a", "password": "b"}, headers=headers).status_code == 403

    # "Origin: null" is accepted only with the browser's own Sec-Fetch-Site header saying same-origin.
    for fetch_site in ("cross-site", "same-site", None):
        headers = {"Origin": "null", **({"Sec-Fetch-Site": fetch_site} if fetch_site else {})}
        assert client.post("/login", data={"email": "a", "password": "b"}, headers=headers).status_code == 403


def test_a_browser_that_sends_origin_null_can_still_sign_in(apps, guard_db):
    """Browsers send "Origin: null" on a site's own posts under some referrer policies. Sign-in must still work."""
    _, url = apps
    client = httpx2.Client(base_url=url, headers={"Origin": "null", "Sec-Fetch-Site": "same-origin"},
                           follow_redirects=False, timeout=60)
    response = client.post("/login", data={"email": user(guard_db, "buyer")["email"], "password": seed.DEMO_PASSWORD})
    assert response.status_code == 303 and COOKIE in client.cookies


def test_the_referrer_policy_keeps_the_sites_own_origin(apps):
    """With "no-referrer", browsers send "Origin: null" on every form post and fetch, and sign-in fails."""
    _, url = apps
    assert httpx2.get(f"{url}/login").headers["referrer-policy"] == "same-origin"


# The page

def test_the_page_allows_no_inline_script_and_no_outside_source(apps, guard_db):
    _, url = apps
    client = sign_in(url, user(guard_db, "buyer")["email"])
    response = client.get("/")
    assert response.status_code == 200
    policy = response.headers["content-security-policy"]
    assert "default-src 'none'" in policy and "script-src 'self'" in policy and "img-src 'self'" in policy
    assert "unsafe-inline" not in policy and "unsafe-eval" not in policy
    assert response.headers["x-content-type-options"] == "nosniff"
    for page in (response.text, client.get("/login").text):
        assert not re.search(r"<script(?![^>]*\bsrc=)", page), "no inline script"
        assert not re.search(r"\son[a-z]+\s*=", page), "no inline event handler"


def test_the_page_puts_html_only_from_the_servers_cleaned_answer():
    script = (STATIC / "chat.js").read_text(encoding="utf-8")
    uses = [line.strip() for line in script.splitlines() if re.search(r"innerHTML|outerHTML|insertAdjacentHTML", line)
            and not line.strip().startswith("//")]
    assert uses == ["answer.innerHTML = data.answer_html; // cleaned by the answer server; see the comment at the top"]
    assert "eval(" not in script and "document.write" not in script


# Files and notes through the host application

def test_a_file_and_a_note_reach_their_owner_and_nobody_else(apps, guard_db):
    server, url = apps
    owner = sign_in(url, user(guard_db, "buyer")["email"])
    server.service.runtime = FakeRuntime([
        Call("create_file", {"sql": "SELECT po_number FROM purchase_orders", "format": "csv"}),
        Call("remember", {"note": "Call open orders 'pending'."}), Say("Done.")])
    events = ask(owner, {"question": "Export my orders and remember my wording."})
    (ready,) = [data for kind, data in events if kind == "file"]

    download = owner.get(f"/api/files/{ready['file_id']}")
    assert download.status_code == 200 and download.headers["content-disposition"].startswith("attachment")
    assert download.content.decode("utf-8-sig").splitlines()[0] == "po_number"
    assert [note["text"] for note in owner.get("/api/notes").json()["notes"]] == ["Call open orders 'pending'."]

    other = sign_in(url, user(guard_db, "admin")["email"])
    assert other.get(f"/api/files/{ready['file_id']}").status_code == 404
    assert other.get("/api/notes").json()["notes"] == []
    assert owner.get("/api/files/..%2F..%2Fv1%2Fmemory").status_code == 404

    assert owner.post("/api/notes/clear").json()["deleted"] == 1
    assert owner.get("/api/notes").json()["notes"] == []
