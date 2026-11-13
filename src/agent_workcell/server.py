"""The answer server.

    POST /v1/answers          the host application asks a question for one authenticated person; the answer streams
                              back as server-sent events
    POST /v1/files/{file_id}  a file that an answer built, for the person who owns it
    POST /v1/memory           the person's saved notes
    POST /v1/memory/clear     delete the person's saved notes
    GET  /health              liveness
    /mcp                      the tool server, used by the agent runtime on this machine

The host application authenticates the person and sends the principal with a shared secret (WORKCELL_HOST_KEY). The
project does no login of its own. Each answer runs with its own guarded connection, its own tool token, its own empty
working folder, a time limit and a cost limit, and leaves an audit record. Each person has a daily limit on answers
and on cost, and a limit on answers running at once.

    python -m agent_workcell.server
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import json
import secrets
import shutil
import sys
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass, field

import anyio
import psycopg
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route

from agent_workcell import prompt, render
from agent_workcell.audit import AnswerRecord, AuditLog
from agent_workcell.config import ConfigError, Settings, check_claude_access, load_settings
from agent_workcell.files import FileStore
from agent_workcell.guard import GuardedConnection, GuardError, Limits, Principal
from agent_workcell.guard.doctor import check, errors
from agent_workcell.guard.plan import DEFAULT_NAMES, Names
from agent_workcell.knowledge import Graph, load_graph
from agent_workcell.memory import Memory, person_key
from agent_workcell.policy import Policy, load_policy
from agent_workcell.runtime import Event, Finished, RunRequest, Runtime
from agent_workcell.tool_server import ToolServer
from agent_workcell.tools import RoleKnowledge, RunContext, load_role_knowledge

MAX_QUESTION_CHARS = 4_000


@dataclass
class AnswerService:
    settings: Settings
    policy: Policy
    graph: Graph
    roles: dict[str, RoleKnowledge]
    runtime: Runtime
    audit: AuditLog
    tool_server: ToolServer
    memory: Memory
    files: FileStore
    names: Names = DEFAULT_NAMES
    running: dict[str, int] = field(default_factory=dict)  # answers running now, per person

    def limit_refusal(self, principal: Principal) -> str | None:
        """Why this person may not start an answer now, or None. The cost check reserves the full per-answer cap for
        each running answer and for the new one, so the daily cost limit holds even if they all reach the cap."""
        settings, person = self.settings, str(principal.user_id)
        running = self.running.get(person, 0)
        if settings.claude_account_login and sum(self.running.values()) > 0:
            # The sign-in mode is one person's Claude Code sign-in: never several agent runs at once on it.
            return "The server is in the local sign-in mode and runs one answer at a time. Try again shortly."
        if running >= settings.concurrent_answers:
            return "Another answer for you is still running. Wait for it to finish."
        count, cost = self.audit.usage_since(person, _day_start(), unknown_cost=settings.answer_max_usd)
        if count + running >= settings.daily_answers:
            return "You have reached the daily limit of answers. It resets at midnight UTC."
        if cost + (running + 1) * settings.answer_max_usd > settings.daily_usd:
            return "You have reached the daily cost limit. It resets at midnight UTC."
        return None

    async def answer(self, principal: Principal, question: str, name: str | None = None) -> AsyncIterator[Event]:
        """Stream the events of one answer. The last event is always Finished."""
        # The check and the reservation run with no await between them, so two requests cannot both pass.
        refusal = self.limit_refusal(principal)
        if refusal:
            yield Finished(ok=False, error=refusal)
            return
        person = str(principal.user_id)
        self.running[person] = self.running.get(person, 0) + 1
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        producer = asyncio.create_task(self._produce(principal, question, name, queue))
        try:
            while (event := await queue.get()) is not None:
                yield event
        finally:
            if not producer.done():
                producer.cancel()  # the caller went away: stop the run; _produce still cleans up
            await asyncio.gather(producer, return_exceptions=True)
            # Released only after the audit record (with the cost) is written, so no answer is ever uncounted.
            self.running[person] -= 1
            if not self.running[person]:
                del self.running[person]

    async def _produce(self, principal: Principal, question: str, name: str | None,
                       queue: asyncio.Queue[Event | None]) -> None:
        settings = self.settings
        knowledge = self.roles[principal.role]
        system_prompt = prompt.SYSTEM_PROMPT + "\n" + prompt.role_addition(principal.role, knowledge.guide)
        run_id = uuid.uuid4().hex
        record = AnswerRecord(
            run_id=run_id, started_at=_now(), role=principal.role, user_id=str(principal.user_id),
            question=question, runtime=self.runtime.name, runtime_version=self.runtime.version,
            model=settings.model, prompt_hash=prompt.prompt_hash(system_prompt))
        workdir = settings.data_dir / "runs" / run_id
        workdir.mkdir(parents=True)
        started = time.monotonic()
        connection = token = None
        finished: Finished | None = None
        loop = asyncio.get_running_loop()

        def emit(event: object) -> None:  # called from tool threads
            loop.call_soon_threadsafe(queue.put_nowait, event)

        try:
            try:
                limits = Limits(settings.query_timeout_ms, settings.query_max_rows, settings.query_max_chars)
                connection = await anyio.to_thread.run_sync(lambda: GuardedConnection(
                    settings.agent_url, settings.binder_url, self.policy, principal, limits, self.names))
            except (GuardError, psycopg.Error):
                finished = Finished(ok=False, error="The data connection could not be opened for this person.")
                return

            file_limits = Limits(settings.query_timeout_ms, settings.file_max_rows, settings.file_max_chars)
            ctx = RunContext(run_id, principal, connection, knowledge, self.graph, record, memory=self.memory,
                             files=self.files, file_limits=file_limits, emit=emit)
            token = self.tool_server.open_run(ctx)
            notes = [note.text for note in await anyio.to_thread.run_sync(lambda: self.memory.notes(principal))]
            request = RunRequest(
                run_id=run_id, system_prompt=system_prompt,
                message=prompt.user_message(question, settings.today_or_now(), name, notes),
                mcp_url=settings.mcp_url, mcp_token=token, model=settings.model, max_turns=settings.max_turns,
                max_usd=settings.answer_max_usd, workdir=workdir)
            try:
                async with asyncio.timeout(settings.answer_timeout_s), aclosing(self.runtime.run(request)) as events:
                    async for event in events:
                        if isinstance(event, Finished):
                            finished = event
                            break
                        await queue.put(event)
            except TimeoutError:
                finished = Finished(ok=False, error=f"The answer took longer than {settings.answer_timeout_s:g} "
                                                    "seconds and was stopped.")
            except Exception as error:  # a broken runtime must still end the answer cleanly
                finished = Finished(ok=False, error=f"The runtime failed: {type(error).__name__}")
            if finished is None:
                finished = Finished(ok=False, error="The runtime ended without a result.")
        finally:
            if token:
                self.tool_server.close_run(token)
            if connection is not None:
                await anyio.to_thread.run_sync(connection.close)
            shutil.rmtree(workdir, ignore_errors=True)
            finished = finished or Finished(ok=False, error="The answer was stopped.")
            finished = dataclasses.replace(finished, answer_html=render.answer_html(finished.answer))
            record.finished_at, record.duration_ms = _now(), int((time.monotonic() - started) * 1000)
            record.status, record.answer, record.error = ("ok" if finished.ok else "failed"), finished.answer, \
                finished.error
            record.input_tokens, record.output_tokens = finished.input_tokens, finished.output_tokens
            record.cost_usd, record.turns = finished.cost_usd, finished.turns
            await anyio.to_thread.run_sync(lambda: self.audit.write(record))
            queue.put_nowait(finished)
            queue.put_nowait(None)


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds")


def _day_start() -> str:
    """Midnight UTC today, in the same form as the audit's start times."""
    today = dt.datetime.now(dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return today.isoformat(timespec="milliseconds")


def build_service(settings: Settings, runtime: Runtime, names: Names = DEFAULT_NAMES) -> AnswerService:
    """Load everything and check the guard. Refuses to build a service on a guard that does not check out."""
    policy = load_policy(settings.policy)
    graph = load_graph(settings.knowledge)
    roles = load_role_knowledge(graph, policy, settings.knowledge / "guide")
    with psycopg.connect(settings.binder_url) as conn:
        problems = errors(check(conn, policy, names))
    if problems:
        raise ConfigError("The guard check failed. The service will not start:\n- "
                          + "\n- ".join(problem.message for problem in problems))
    audit = AuditLog(settings.data_dir / "audit.sqlite3")
    tool_server = ToolServer(f"http://{settings.host}:{settings.port}")
    memory = Memory(settings.data_dir / "memory.sqlite3")
    files = FileStore(settings.data_dir / "files", settings.file_keep_hours)
    files.remove_expired()
    return AnswerService(settings, policy, graph, roles, runtime, audit, tool_server, memory, files, names)


class BadRequest(ValueError):
    pass


def read_principal(body: dict, roles: dict) -> Principal:
    """The principal the host application sent. Refuses anything malformed and any role the policy does not have."""
    try:
        raw = body["principal"]
        principal = Principal(str(raw["role"]), raw["user_id"], dict(raw.get("attributes") or {}))
    except (KeyError, TypeError, ValueError, AttributeError):
        raise BadRequest("send JSON with principal {role, user_id, attributes}") from None
    if principal.role not in roles:
        raise BadRequest("unknown role")
    if not isinstance(principal.user_id, int | str) or isinstance(principal.user_id, bool):
        raise BadRequest("user_id must be a number or a string")
    return principal


def create_app(service: AnswerService) -> Starlette:
    mcp_app = service.tool_server.app()
    expected = f"Bearer {service.settings.host_key}"

    def authorized(request: Request) -> bool:
        return secrets.compare_digest(request.headers.get("authorization", ""), expected)

    async def principal_of(request: Request) -> tuple[Principal, dict]:
        try:
            body = await request.json()
        except ValueError:
            raise BadRequest("send a JSON body") from None
        if not isinstance(body, dict):
            raise BadRequest("send a JSON object")
        return read_principal(body, service.roles), body

    def refused(error: BadRequest) -> JSONResponse:
        return JSONResponse({"error": str(error)}, status_code=400)

    async def answers(request: Request):
        if not authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            principal, body = await principal_of(request)
        except BadRequest as error:
            return refused(error)
        question = str(body.get("question") or "").strip()
        name = body["principal"].get("name")
        if not question or len(question) > MAX_QUESTION_CHARS:
            return JSONResponse({"error": f"the question must have 1 to {MAX_QUESTION_CHARS} characters"},
                                status_code=400)
        refusal = service.limit_refusal(principal)
        if refusal:
            return JSONResponse({"error": refusal}, status_code=429)

        async def stream():
            async with aclosing(service.answer(principal, question, str(name) if name else None)) as events:
                async for event in events:
                    payload = json.dumps(dataclasses.asdict(event), ensure_ascii=False, default=str)
                    yield f"event: {event.type}\ndata: {payload}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    async def file(request: Request):
        if not authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            principal, _ = await principal_of(request)
        except BadRequest as error:
            return refused(error)
        stored = service.files.get(person_key(principal), request.path_params["file_id"])
        if stored is None:  # the same answer for "no such file", "expired" and "someone else's"
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(stored.path, media_type=stored.media_type, filename=stored.name,
                            headers={"X-Content-Type-Options": "nosniff"})

    async def memory(request: Request):
        if not authorized(request):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            principal, _ = await principal_of(request)
        except BadRequest as error:
            return refused(error)
        if request.url.path.endswith("/clear"):
            return JSONResponse({"deleted": service.memory.clear(principal)})
        return JSONResponse({"notes": [dataclasses.asdict(note) for note in service.memory.notes(principal)]})

    async def health(request: Request):
        return JSONResponse({"status": "ok"})

    @asynccontextmanager
    async def lifespan(app: Starlette):
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    return Starlette(routes=[Route("/v1/answers", answers, methods=["POST"]),
                             Route("/v1/files/{file_id}", file, methods=["POST"]),
                             Route("/v1/memory", memory, methods=["POST"]),
                             Route("/v1/memory/clear", memory, methods=["POST"]),
                             Route("/health", health),
                             Mount("/", app=mcp_app)], lifespan=lifespan)


def main() -> int:
    import uvicorn

    try:
        settings = load_settings()
        if settings.runtime != "claude":
            raise ConfigError(f"WORKCELL_RUNTIME={settings.runtime!r} is not a runtime this server can start.")
        try:
            from agent_workcell.runtime.claude import ClaudeRuntime
        except ImportError:
            raise ConfigError("The Claude runtime needs the 'claude' extra: pip install 'agent-workcell[claude]'")\
                from None
        check_claude_access(settings)
        if settings.claude_account_login:
            print("WORKCELL_CLAUDE_ACCOUNT_LOGIN=1: the runtime uses this computer's Claude Code sign-in, for a local "
                  "trial by one person. One answer runs at a time. The account's email reaches the model and "
                  "transcripts stay in ~/.claude. A service for other people must use ANTHROPIC_API_KEY.",
                  file=sys.stderr)
        service = build_service(settings, ClaudeRuntime(account_login=settings.claude_account_login))
    except (ConfigError, OSError, psycopg.Error) as error:
        print(error, file=sys.stderr)
        return 2
    uvicorn.run(create_app(service), host=settings.host, port=settings.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
