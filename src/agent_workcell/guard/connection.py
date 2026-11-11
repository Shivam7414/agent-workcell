"""One agent connection for one person.

The server opens a GuardedConnection with the person's principal (from the host application, never from the model).
It logs in as the agent role, records who is asking through the binder role, and then runs the model's SQL:

- as a server-side cursor, so PostgreSQL itself accepts exactly one query (SELECT, VALUES, TABLE or WITH) and nothing
  else: no second statement, no SET, no SHOW;
- in a read-only transaction that is always rolled back;
- with a statement time limit, a row cap and a size cap.

None of this decides access. Access is decided by the grants and the guard views. These limits keep one query from
running too long or returning too much.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass, field

import psycopg
from psycopg import sql

from agent_workcell.guard.plan import DEFAULT_NAMES, Names, bind_call
from agent_workcell.policy import Policy

TIMEOUT = "57014"
INSUFFICIENT_PRIVILEGE = "42501"


class GuardError(RuntimeError):
    """The guarded connection cannot be used. This is a set-up or binding problem, never the model's fault."""


class QueryError(Exception):
    """The model's query failed. The message is safe to show to the model."""

    def __init__(self, sqlstate: str | None, message: str):
        self.sqlstate = sqlstate
        self.message = message
        super().__init__(message)


@dataclass(frozen=True)
class Principal:
    """Who is asking, as the host application authenticated them."""

    role: str
    user_id: int | str
    attributes: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Limits:
    timeout_ms: int = 5000
    max_rows: int = 1000
    max_chars: int = 1_000_000  # approximate size of the result, counted as characters of the values

    def __post_init__(self) -> None:
        if self.timeout_ms < 1 or self.max_rows < 1 or self.max_chars < 1:
            raise ValueError("every limit must be 1 or more")


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    truncated: bool  # True when the row cap or the size cap cut the result


class GuardedConnection:
    def __init__(self, agent_url: str, binder_url: str, policy: Policy, principal: Principal,
                 limits: Limits | None = None, names: Names = DEFAULT_NAMES):
        if principal.role not in policy.roles:
            raise GuardError(f"Role {principal.role!r} is not in the policy.")
        extra = sorted(set(principal.attributes) - (set(policy.attributes) - {"user_id"}))
        if extra:
            raise GuardError(f"Principal attributes that the policy does not define: {extra}")

        self.limits = limits or Limits()
        self._binder_url = binder_url
        self._names = names
        self._conn = psycopg.connect(agent_url, autocommit=True)
        try:
            current, session = self._conn.execute("SELECT current_user, session_user").fetchone()
            if current != names.agent_role or session != names.agent_role:
                raise GuardError(f"The agent URL must log in as {names.agent_role}. Refusing to run as another role.")
            self._pid, self._backend_start = self._conn.execute(
                "SELECT a.pid, a.backend_start FROM pg_catalog.pg_stat_get_activity(pg_catalog.pg_backend_pid()) a"
            ).fetchone()

            values = [principal.attributes.get(name) for name in policy.attributes if name != "user_id"]
            with psycopg.connect(binder_url, autocommit=True) as binder:
                binder.execute(bind_call(policy, names),
                               [self._pid, self._backend_start, principal.role, principal.user_id, *values])

            self._conn.execute(sql.SQL("SET search_path = {}").format(sql.Identifier(names.views)))
            self._conn.autocommit = False
            self._conn.read_only = True
        except BaseException:
            self._conn.close()
            raise

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def backend_start(self) -> dt.datetime:
        return self._backend_start

    def query(self, text: str, limits: Limits | None = None) -> QueryResult:
        """Run one query. `limits` replaces the connection's limits for this query only (files need larger caps)."""
        limits = limits or self.limits
        if self._conn.closed:
            raise GuardError("The guarded connection is closed. Open a new one; never reconnect this one.")
        try:
            self._conn.execute("SELECT set_config('statement_timeout', %s, true)", [str(limits.timeout_ms)])
            with self._conn.cursor(name="workcell_query") as cursor:
                cursor.execute(text)  # untrusted SQL, run as the agent role through the guard views
                rows = cursor.fetchmany(limits.max_rows + 1)
                columns = [column.name for column in cursor.description or []]
        except psycopg.Error as error:
            raise QueryError(error.sqlstate, self._message(error, limits)) from None
        finally:
            if not self._conn.closed:
                self._conn.rollback()

        truncated = len(rows) > limits.max_rows
        rows = rows[: limits.max_rows]
        size = 0
        for index, row in enumerate(rows):
            size += sum(len(str(value)) for value in row if value is not None)
            if size > limits.max_chars:
                rows, truncated = rows[:index], True
                break
        return QueryResult(columns, rows, truncated)

    def _message(self, error: psycopg.Error, limits: Limits) -> str:
        if error.sqlstate == TIMEOUT:
            return f"The query ran longer than {limits.timeout_ms} ms and was stopped."
        if error.sqlstate == INSUFFICIENT_PRIVILEGE:
            # PostgreSQL's text would name real tables, schemas or roles. Say only what the model needs.
            return "Permission denied. Only the tables in the guide can be read, and only with SELECT."
        diagnostic = error.diag
        message = diagnostic.message_primary or "The query failed."
        if diagnostic.message_hint:
            message += f" Hint: {diagnostic.message_hint}"
        return message

    def close(self) -> None:
        if self._conn.closed:
            return
        try:
            with psycopg.connect(self._binder_url, autocommit=True) as binder:
                binder.execute(sql.SQL("SELECT {}.unbind(%s, %s)").format(sql.Identifier(self._names.internal)),
                               [self._pid, self._backend_start])
        finally:
            self._conn.close()

    def __enter__(self) -> GuardedConnection:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
