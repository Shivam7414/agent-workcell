"""The reference host application: a login and a chat page for the reference ERP.

Agent Workcell does no login of its own. A real host application (the ERP) logs people in and calls the answer API
with their principal. This small application plays that part for the reference ERP, so the whole flow can be tried
locally. It is a reference for the integration, not a login system to deploy:

- it checks the seeded users' passwords (scrypt) in the ERP database;
- it keeps the principal in a signed cookie (HttpOnly, SameSite=Strict) and never takes it from the browser;
- it forwards questions, file downloads and notes to the answer server with the host key, which the browser never
  sees;
- the page shows only the server's cleaned answer HTML, under a content security policy that allows no outside
  images, scripts or connections.

    python -m reference_erp.host_app
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs

import httpx2
import psycopg
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from agent_workcell.conversations import valid_conversation_id

STATIC = Path(__file__).parent / "static"
COOKIE = "workcell_session"
SESSION_SECONDS = 8 * 3600
MAX_FORM_BYTES = 4_096
MAX_QUESTION_CHARS = 4_000
FILE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
SECURITY_HEADERS = {
    # No inline script and no outside source of any kind: a cleaned answer that still held an image or a script
    # could not load it.
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
                               "img-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
    "X-Content-Type-Options": "nosniff",
    # Not "no-referrer": with it, browsers send "Origin: null" on this site's own form posts and fetches, and the
    # same-origin check below refuses every sign-in. "same-origin" still sends nothing to other sites.
    "Referrer-Policy": "same-origin",
    "Cache-Control": "no-store",
}
# A hash to check when the email is unknown, so a wrong email takes as long as a wrong password.
DUMMY_HASH = "scrypt$16384$8$1$" + "00" * 16 + "$" + "00" * 32


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class HostSettings:
    erp_url: str
    answer_url: str
    host_key: str
    session_secret: str
    host: str = "127.0.0.1"
    port: int = 8081


def load_host_settings(environ: Mapping[str, str] | None = None) -> HostSettings:
    env = os.environ if environ is None else environ
    required = {"ERP_DATABASE_URL": "the ERP database, to check logins",
                "WORKCELL_HOST_KEY": "the answer server's host key",
                "HOST_APP_SESSION_SECRET": "the secret that signs session cookies (24 characters or more)"}
    missing = [f"{name} ({meaning})" for name, meaning in required.items() if not env.get(name, "").strip()]
    if missing:
        raise ConfigError("Missing settings:\n- " + "\n- ".join(missing))
    if len(env["HOST_APP_SESSION_SECRET"]) < 24:
        raise ConfigError("HOST_APP_SESSION_SECRET must be at least 24 characters long.")
    return HostSettings(erp_url=env["ERP_DATABASE_URL"],
                        answer_url=env.get("WORKCELL_ANSWER_URL", "http://127.0.0.1:8080").rstrip("/"),
                        host_key=env["WORKCELL_HOST_KEY"], session_secret=env["HOST_APP_SESSION_SECRET"],
                        host=env.get("HOST_APP_HOST", "127.0.0.1"), port=int(env.get("HOST_APP_PORT", "8081")))


# Passwords and sessions

def check_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        actual = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p),
                                dklen=len(bytes.fromhex(digest)))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, bytes.fromhex(digest))


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_session(secret: str, session: dict) -> str:
    payload = _b64(json.dumps(session, separators=(",", ":")).encode())
    signature = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def read_session(secret: str, cookie: str | None) -> dict | None:
    """The session, if the cookie is signed by us and has not expired. Anything else is no session."""
    if not cookie or cookie.count(".") != 1:
        return None
    payload, signature = cookie.split(".")
    expected = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        session = json.loads(_unb64(payload))
    except ValueError:
        return None
    return session if isinstance(session, dict) and session.get("exp", 0) > time.time() else None


def principal_of(session: dict) -> dict:
    """The principal for the answer API, from the signed session only."""
    attributes = {"supplier_id": session["supplier_id"]} if session["role"] == "supplier_user" else {}
    return {"role": session["role"], "user_id": session["user_id"], "attributes": attributes,
            "name": session["name"]}


def find_user(erp_url: str, email: str) -> tuple | None:
    """id, password hash, name, role, supplier id, and the organisation the person works for."""
    with psycopg.connect(erp_url) as conn:
        return conn.execute(
            "SELECT u.id, u.password_hash, u.full_name, u.role::text, u.supplier_id,"
            " coalesce(s.name, 'Meridian Works') FROM users u LEFT JOIN suppliers s ON s.id = u.supplier_id"
            " WHERE lower(u.email) = lower(%s) AND u.is_active AND u.deleted_at IS NULL", [email]).fetchone()


ROLE_NAMES = {"admin": "Administrator", "buyer": "Buyer", "supplier_user": "Supplier user"}


def sample_accounts(erp_url: str) -> list[tuple[str, str, str, str]]:
    """One busy account per role, for the sign-in page: role, name, organisation, email."""
    accounts = []
    try:
        with psycopg.connect(erp_url, connect_timeout=5) as conn:
            for role in ROLE_NAMES:
                row = conn.execute(
                    "SELECT u.full_name, coalesce(s.name, 'Meridian Works'), u.email FROM users u"
                    " LEFT JOIN suppliers s ON s.id = u.supplier_id"
                    " WHERE u.role = %s AND u.is_active AND u.deleted_at IS NULL"
                    " ORDER BY (SELECT count(*) FROM purchase_orders o"
                    "           WHERE o.buyer_id = u.id OR o.supplier_id = u.supplier_id) DESC, u.id LIMIT 1",
                    [role]).fetchone()
                if row:
                    accounts.append((role, *row))
    except psycopg.Error:
        return []
    return accounts


# Pages

LOGIN_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in - Meridian Works purchasing assistant</title>
<link rel="stylesheet" href="/static/chat.css">
<script src="/static/login.js" defer></script>
</head>
<body class="signin">
<main class="signin-grid">
  <section class="signin-intro">
    <p class="brand"><span class="brand-mark" aria-hidden="true"></span>Meridian Works</p>
    <h1>Ask your purchasing data in plain words.</h1>
    <p class="intro-text">Orders, deliveries, supplier invoices and payments, answered from the live ERP. The database
      decides what each person may see, so a buyer, a supplier and an administrator get different answers to the same
      question.</p>
    <ul class="intro-facts">
      <li>Every number comes from a query you can open and read.</li>
      <li>Ask for an Excel or CSV file and the server builds it from the data.</li>
      <li>This is a reference ERP. The company, people and figures are made up.</li>
    </ul>
  </section>
  <section class="signin-panel" aria-labelledby="signin-title">
    <h2 id="signin-title">Sign in</h2>
    {error}
    <form method="post" action="/login" class="signin-form">
      <label for="email">Email</label>
      <input id="email" name="email" type="email" autocomplete="username" required>
      <label for="password">Password</label>
      <input id="password" name="password" type="password" autocomplete="current-password" required>
      <button type="submit" class="primary">Sign in</button>
    </form>
    {accounts}
  </section>
</main>
</body>
</html>
"""
LOGIN_FAILED = '<p class="form-error" role="alert">The email or the password is wrong. Check both and try again.</p>'
ACCOUNTS = """<div class="accounts">
      <h3>Try an account</h3>
      <p>Every account uses the password <span class="password">workcell-demo</span>. Choose one to fill the form.</p>
      {buttons}
    </div>"""


def login_html(accounts: list[tuple[str, str, str, str]], error: str = "") -> str:
    """The sign-in page. Names and emails come from the database, so every one is escaped."""
    buttons = "".join(
        f'<button type="button" class="account" data-email="{html.escape(email)}">'
        f'<span class="account-role">{html.escape(ROLE_NAMES.get(role, role))}</span>'
        f'<span class="account-name">{html.escape(name)}</span>'
        f'<span class="account-org">{html.escape(org)}</span></button>'
        for role, name, org, email in accounts)
    return LOGIN_PAGE.format(error=error, accounts=ACCOUNTS.format(buttons=buttons) if accounts else "")


def create_host_app(settings: HostSettings) -> Starlette:
    answer_headers = {"Authorization": f"Bearer {settings.host_key}"}

    def session_of(request: Request) -> dict | None:
        return read_session(settings.session_secret, request.cookies.get(COOKIE))

    def same_origin(request: Request) -> bool:
        """A POST must come from this application's own page: the Origin header names this site, or, where a browser
        sends "null" or nothing, its Sec-Fetch-Site header (which a web page cannot set) says same-origin."""
        origin = request.headers.get("origin")
        if origin is not None and origin != "null":
            return origin == f"{request.url.scheme}://{request.url.netloc}"
        return request.headers.get("sec-fetch-site") == "same-origin"

    def secure(response: Response) -> Response:
        response.headers.update(SECURITY_HEADERS)
        return response

    def no_session() -> JSONResponse:
        return JSONResponse({"error": "Sign in again."}, status_code=401)

    def unavailable() -> JSONResponse:
        return JSONResponse({"error": "The assistant is not available."}, status_code=502)

    async def home(request: Request):
        if session_of(request) is None:
            return RedirectResponse("/login", status_code=303)
        return secure(HTMLResponse((STATIC / "chat.html").read_text(encoding="utf-8")))

    async def login_page(request: Request):
        return secure(HTMLResponse(login_html(sample_accounts(settings.erp_url))))

    async def login(request: Request):
        if not same_origin(request):
            return secure(Response("Forbidden", status_code=403))
        body = await request.body()
        if len(body) > MAX_FORM_BYTES:
            return secure(Response("Too large", status_code=413))
        form = parse_qs(body.decode("utf-8", "replace"))
        email = (form.get("email") or [""])[0].strip()
        password = (form.get("password") or [""])[0]
        user = find_user(settings.erp_url, email) if email else None
        if not check_password(password, user[1] if user else DUMMY_HASH) or user is None:
            return secure(HTMLResponse(login_html(sample_accounts(settings.erp_url), LOGIN_FAILED), status_code=401))
        user_id, _, name, role, supplier_id, org = user
        cookie = sign_session(settings.session_secret, {"user_id": user_id, "role": role, "supplier_id": supplier_id,
                                                        "name": name, "org": org,
                                                        "exp": int(time.time()) + SESSION_SECONDS})
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE, cookie, max_age=SESSION_SECONDS, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", path="/")
        return secure(response)

    async def logout(request: Request):
        if not same_origin(request):
            return secure(Response("Forbidden", status_code=403))
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(COOKIE, path="/")
        return secure(response)

    async def me(request: Request):
        session = session_of(request)
        if session is None:
            return secure(no_session())
        return secure(JSONResponse({"name": session["name"], "role": session["role"],
                                    "organisation": session.get("org", "")}))

    async def ask(request: Request):
        session = session_of(request)
        if session is None:
            return secure(no_session())
        if not same_origin(request):
            return secure(JSONResponse({"error": "forbidden"}, status_code=403))
        try:
            body = await request.json()
            question = str(body["question"]).strip()
        except (ValueError, KeyError, TypeError):
            return secure(JSONResponse({"error": "Send a question."}, status_code=400))
        if not question or len(question) > MAX_QUESTION_CHARS:
            return secure(JSONResponse({"error": f"Ask with 1 to {MAX_QUESTION_CHARS} characters."},
                                       status_code=400))
        # Only the question and the conversation's name are taken from the browser. The principal comes from the
        # signed session, and the answer server keys every conversation by that principal, so a browser cannot name
        # its way into another person's conversation.
        payload = {"principal": principal_of(session), "question": question}
        conversation_id = body.get("conversation_id")
        if conversation_id is not None:
            if not valid_conversation_id(conversation_id):
                return secure(JSONResponse({"error": "The conversation id is not valid."}, status_code=400))
            payload["conversation_id"] = conversation_id
        client = httpx2.AsyncClient(timeout=httpx2.Timeout(10, read=None))
        try:
            upstream = await client.send(client.build_request(
                "POST", f"{settings.answer_url}/v1/answers", json=payload, headers=answer_headers), stream=True)
        except httpx2.HTTPError:
            await client.aclose()
            return secure(unavailable())
        if upstream.status_code != 200:
            content = await upstream.aread()
            await upstream.aclose()
            await client.aclose()
            error = _error_text(content)
            return secure(JSONResponse({"error": error}, status_code=upstream.status_code))

        async def relay():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await client.aclose()

        return secure(StreamingResponse(relay(), media_type="text/event-stream"))

    async def download(request: Request):
        session = session_of(request)
        if session is None:
            return secure(no_session())
        file_id = request.path_params["file_id"]
        if not FILE_ID.fullmatch(file_id):  # only ids the answer server makes, so the browser cannot shape the URL
            return secure(JSONResponse({"error": "This file is not available."}, status_code=404))
        try:
            async with httpx2.AsyncClient(timeout=60) as client:
                upstream = await client.post(f"{settings.answer_url}/v1/files/{file_id}",
                                             json={"principal": principal_of(session)}, headers=answer_headers)
        except httpx2.HTTPError:
            return secure(unavailable())
        if upstream.status_code != 200:
            return secure(JSONResponse({"error": "This file is not available."}, status_code=404))
        headers = {name: upstream.headers[name] for name in ("content-type", "content-disposition")
                   if name in upstream.headers}
        return secure(Response(upstream.content, headers=headers))

    async def notes(request: Request):
        session = session_of(request)
        if session is None:
            return secure(no_session())
        clear = request.method == "POST"
        if clear and not same_origin(request):
            return secure(JSONResponse({"error": "forbidden"}, status_code=403))
        path = "/v1/memory/clear" if clear else "/v1/memory"
        try:
            async with httpx2.AsyncClient(timeout=30) as client:
                upstream = await client.post(f"{settings.answer_url}{path}",
                                             json={"principal": principal_of(session)}, headers=answer_headers)
            return secure(JSONResponse(upstream.json(), status_code=upstream.status_code))
        except (httpx2.HTTPError, ValueError):
            return secure(unavailable())

    return Starlette(routes=[
        Route("/", home), Route("/login", login_page), Route("/login", login, methods=["POST"]),
        Route("/logout", logout, methods=["POST"]), Route("/api/me", me), Route("/api/ask", ask, methods=["POST"]),
        Route("/api/files/{file_id}", download), Route("/api/notes", notes),
        Route("/api/notes/clear", notes, methods=["POST"]),
        Mount("/static", app=StaticFiles(directory=STATIC)),
    ])


def _error_text(content: bytes) -> str:
    try:
        return str(json.loads(content)["error"])
    except (ValueError, KeyError, TypeError):
        return "The assistant is not available."


def main() -> int:
    import sys

    import uvicorn

    try:
        settings = load_host_settings()
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    uvicorn.run(create_host_app(settings), host=settings.host, port=settings.port)
    return 0
