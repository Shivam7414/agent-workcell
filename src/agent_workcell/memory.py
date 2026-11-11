"""Memory: short notes kept for one person between answers.

Notes are untrusted. The model writes them (with the `remember` tool), and what it writes can come from the person's
message or, through prompt injection, from text in the data. So:

- a note never changes identity or access: the principal comes from the host application, and the guard decides
  what the person can read;
- notes reach the model in their own escaped block, marked as notes;
- the person can see every note and delete all of them through the host application.

Notes belong to the whole principal: the role, the user id and every attribute. If the host application changes a
person's role or supplier, they start with no notes, so a note cannot carry data from a wider access to a narrower one.
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from agent_workcell.guard import Principal

MAX_NOTE_CHARS = 300
MAX_NOTES = 20

SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS notes_by_person ON notes (person, id);
"""


@dataclass(frozen=True)
class Note:
    id: int
    text: str
    created_at: str


def person_key(principal: Principal) -> str:
    """One key for the whole principal, so any change in access starts a new memory."""
    attributes = {name: str(value) for name, value in sorted(principal.attributes.items())}
    return json.dumps([principal.role, str(principal.user_id), attributes], separators=(",", ":"))


def clean_note(text: str) -> str:
    """One line, capped. Escaping for the prompt happens where the note is used."""
    text = " ".join(str(text).split())
    return text[:MAX_NOTE_CHARS]


class Memory:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(SCHEMA)
        self._lock = threading.Lock()

    def add(self, principal: Principal, text: str) -> Note | None:
        """Save a note. Keeps the newest MAX_NOTES notes. Returns None for an empty note."""
        text = clean_note(text)
        if not text:
            return None
        person = person_key(principal)
        created = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        with self._lock, self._conn:
            cursor = self._conn.execute("INSERT INTO notes (person, text, created_at) VALUES (?, ?, ?)",
                                        [person, text, created])
            self._conn.execute(
                "DELETE FROM notes WHERE person = ? AND id NOT IN"
                " (SELECT id FROM notes WHERE person = ? ORDER BY id DESC LIMIT ?)", [person, person, MAX_NOTES])
        return Note(cursor.lastrowid, text, created)

    def notes(self, principal: Principal) -> list[Note]:
        with self._lock:
            rows = self._conn.execute("SELECT id, text, created_at FROM notes WHERE person = ? ORDER BY id",
                                      [person_key(principal)]).fetchall()
        return [Note(*row) for row in rows]

    def clear(self, principal: Principal) -> int:
        with self._lock, self._conn:
            return self._conn.execute("DELETE FROM notes WHERE person = ?", [person_key(principal)]).rowcount

    def close(self) -> None:
        self._conn.close()
