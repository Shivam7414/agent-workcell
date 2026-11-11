"""The audit record: every answer, with the exact queries behind it.

Stored in SQLite (standard library) in the data folder. It records who asked, what, which model and runtime, what it
cost, and every tool call with its SQL and result size. It never stores the run's token or database credentials.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS answers (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    role TEXT NOT NULL,
    user_id TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT,
    status TEXT,
    error TEXT,
    runtime TEXT NOT NULL,
    runtime_version TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    turns INTEGER,
    duration_ms INTEGER
);
CREATE INDEX IF NOT EXISTS answers_by_person ON answers (user_id, started_at);
CREATE TABLE IF NOT EXISTS tool_calls (
    run_id TEXT NOT NULL REFERENCES answers (run_id),
    seq INTEGER NOT NULL,
    tool TEXT NOT NULL,
    arguments TEXT NOT NULL,
    sql_text TEXT,
    rows INTEGER,
    truncated INTEGER,
    result_chars INTEGER,
    error TEXT,
    duration_ms INTEGER,
    PRIMARY KEY (run_id, seq)
);
"""


@dataclass
class ToolCallRecord:
    tool: str
    arguments: dict
    sql_text: str | None = None
    rows: int | None = None
    truncated: bool | None = None
    result_chars: int = 0
    error: str | None = None
    duration_ms: int = 0


@dataclass
class AnswerRecord:
    run_id: str
    started_at: str
    role: str
    user_id: str
    question: str
    runtime: str
    runtime_version: str
    model: str
    prompt_hash: str
    finished_at: str | None = None
    answer: str | None = None
    status: str | None = None
    error: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    turns: int | None = None
    duration_ms: int | None = None
    tool_calls: list[ToolCallRecord] = field(default_factory=list)


class AuditLog:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    def write(self, record: AnswerRecord) -> None:
        answer = {key: value for key, value in vars(record).items() if key != "tool_calls"}
        with self._lock, self._conn:
            self._conn.execute(
                f"INSERT INTO answers ({', '.join(answer)}) VALUES ({', '.join('?' * len(answer))})",  # noqa: S608
                list(answer.values()))
            self._conn.executemany(
                "INSERT INTO tool_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(record.run_id, seq, call.tool, json.dumps(call.arguments, ensure_ascii=False), call.sql_text,
                  call.rows, None if call.truncated is None else int(call.truncated), call.result_chars, call.error,
                  call.duration_ms)
                 for seq, call in enumerate(record.tool_calls, start=1)])

    def usage_since(self, user_id: str, since: str, unknown_cost: float) -> tuple[int, float]:
        """Answers and cost for one person since a UTC time. An answer whose cost is unknown (for example one that
        was stopped) counts at `unknown_cost`, so a limit can never be passed by stopping answers early."""
        with self._lock:
            count, cost = self._conn.execute(
                "SELECT count(*), coalesce(sum(coalesce(cost_usd, ?)), 0) FROM answers"
                " WHERE user_id = ? AND started_at >= ?", [unknown_cost, user_id, since]).fetchone()
        return count, cost

    def answers(self) -> list[dict]:
        with self._lock:
            cursor = self._conn.execute("SELECT * FROM answers ORDER BY started_at")
            names = [column[0] for column in cursor.description]
            return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def tool_calls(self, run_id: str) -> list[dict]:
        with self._lock:
            cursor = self._conn.execute("SELECT * FROM tool_calls WHERE run_id = ? ORDER BY seq", [run_id])
            names = [column[0] for column in cursor.description]
            return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def close(self) -> None:
        self._conn.close()
