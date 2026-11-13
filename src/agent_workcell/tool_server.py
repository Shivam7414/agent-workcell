"""The tool server: the tools over standard MCP (streamable HTTP), so a runtime needs no project code to use them.

Each run gets a random bearer token when it starts. The runtime adapter puts the token in the MCP connection's
headers; the model never sees it. The server maps the token to the run's context, and so to the person. A token is
valid only while its run is open.
"""

from __future__ import annotations

import secrets
from typing import Literal

import anyio
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from starlette.applications import Starlette

from agent_workcell import tools
from agent_workcell.tools import RunContext

SERVER_NAME = "workcell"
TOOL_NAMES = ("knowledge", "get_schema", "run_query", "create_file", "remember", "search_documents", "read_document")
MAX_SQL_CHARS = 20_000
MAX_QUESTION_CHARS = 2_000
MAX_NOTE_INPUT_CHARS = 1_000  # the memory keeps at most memory.MAX_NOTE_CHARS of it


class RunEnded(PermissionError):
    pass


class ToolServer:
    def __init__(self, base_url: str):
        self._runs: dict[str, RunContext] = {}
        self.mcp = MCPServer(
            SERVER_NAME,
            instructions="Tools to read the company's purchasing data as the person who asked.",
            token_verifier=self,
            # The verifier is the whole check: tokens are random, issued only by this server for its own runs, and
            # forgotten when the run ends. There is no outside issuer whose audience could be confused.
            auth=AuthSettings(issuer_url=base_url, resource_server_url=f"{base_url}/mcp",
                              validate_token_resource=False),
        )
        self._register_tools()

    # Runs and tokens

    def open_run(self, ctx: RunContext) -> str:
        token = secrets.token_urlsafe(32)
        self._runs[token] = ctx
        return token

    def close_run(self, token: str) -> None:
        self._runs.pop(token, None)

    def open_runs(self) -> int:
        return len(self._runs)

    async def verify_token(self, token: str) -> AccessToken | None:
        ctx = self._runs.get(token)
        return AccessToken(token=token, client_id=ctx.run_id, scopes=[]) if ctx else None

    def _context(self) -> RunContext:
        access = get_access_token()
        ctx = self._runs.get(access.token) if access else None
        if ctx is None:
            raise RunEnded("This run has ended.")
        return ctx

    def app(self) -> Starlette:
        return self.mcp.streamable_http_app(stateless_http=True)

    # The tools. Their arguments are what the model may choose; identity is not among them.

    def _register_tools(self) -> None:
        @self.mcp.tool()
        async def knowledge(question: str) -> str:
            """Find what the data means for a question: the tables, columns, stored values and their screen words,
            the joins between the tables, calculations and business rules. Call this first."""
            ctx = self._context()
            question = question[:MAX_QUESTION_CHARS]
            return await anyio.to_thread.run_sync(lambda: tools.record_call(
                ctx, "knowledge", {"question": question}, lambda call: tools.knowledge(ctx, question)))

        @self.mcp.tool()
        async def get_schema(tables: list[str]) -> str:
            """Describe tables you can read: every column with its type and meaning. Pass the table names, or an
            empty list to get the names of all tables you can read."""
            ctx = self._context()
            names = [str(name)[:100] for name in tables[:20]]
            return await anyio.to_thread.run_sync(lambda: tools.record_call(
                ctx, "get_schema", {"tables": names}, lambda call: tools.get_schema(ctx, names)))

        @self.mcp.tool()
        async def run_query(sql: str) -> str:
            """Run one read-only PostgreSQL SELECT statement and return the rows. Only the rows and columns the
            person may see are visible. Results are capped; aggregate in SQL instead of reading many rows."""
            ctx = self._context()
            if len(sql) > MAX_SQL_CHARS:
                return f"The query is too long. Keep it under {MAX_SQL_CHARS} characters."
            return await anyio.to_thread.run_sync(lambda: tools.record_call(
                ctx, "run_query", {"sql": sql}, lambda call: tools.run_query(ctx, sql, call)))

        @self.mcp.tool()
        async def create_file(sql: str, format: Literal["xlsx", "csv"] = "xlsx", title: str = "data") -> str:
            """Make a file for the person to download: an Excel sheet (xlsx) or CSV of every row that one read-only
            SELECT statement returns. The server runs the query and writes the file; you never type the data.
            `title` becomes the file name."""
            ctx = self._context()
            if len(sql) > MAX_SQL_CHARS:
                return f"The query is too long. Keep it under {MAX_SQL_CHARS} characters."
            file_format, name = str(format), str(title)[:100]
            arguments = {"sql": sql, "format": file_format, "title": name}
            return await anyio.to_thread.run_sync(lambda: tools.record_call(
                ctx, "create_file", arguments, lambda call: tools.create_file(ctx, sql, file_format, name, call)))

        @self.mcp.tool()
        async def search_documents(query: str, limit: int = 8) -> str:
            """Search the text of documents the person may read (agreements, policies, certificates, order files) by
            words and by meaning. Returns the best passages of each document's current version, each with its
            citation: document number, title, version, page or section, and passage number. `limit` is 1 to 20."""
            ctx = self._context()
            text = query[:MAX_QUESTION_CHARS]
            count = max(1, min(int(limit), 20))
            return await anyio.to_thread.run_sync(lambda: tools.record_call(
                ctx, "search_documents", {"query": text, "limit": count},
                lambda call: tools.search_documents(ctx, text, count, call)))

        @self.mcp.tool()
        async def read_document(doc_number: str, version: int | None = None, first_passage: int = 1,
                                count: int = 5) -> str:
            """Read passages of one document in order, by its document number (such as D-000012): to see the text
            around a passage that search found, to read a whole document, or to compare two versions. Without
            `version`, reads the current version. Also lists the document's versions. `count` is 1 to 10."""
            ctx = self._context()
            number = doc_number[:40]
            arguments = {"doc_number": number, "version": version, "first_passage": first_passage, "count": count}
            return await anyio.to_thread.run_sync(lambda: tools.record_call(
                ctx, "read_document", arguments,
                lambda call: tools.read_document(ctx, number, version, int(first_passage), int(count), call)))

        @self.mcp.tool()
        async def remember(note: str) -> str:
            """Save a short note for later conversations with this person, when they ask you to remember something
            (a preference, or a name they use for something). The person can see and delete their notes."""
            ctx = self._context()
            text = note[:MAX_NOTE_INPUT_CHARS]
            return await anyio.to_thread.run_sync(lambda: tools.record_call(
                ctx, "remember", {"note": text}, lambda call: tools.remember(ctx, text)))
