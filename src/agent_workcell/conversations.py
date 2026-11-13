"""Conversations: which runtime session continues which conversation, for each person.

The host application names a conversation with an id of its own choice. The server maps (person, conversation id) to
a session id that it chooses itself, a random UUID. The first answer in a conversation starts that session; every
later answer resumes it, so the model sees the earlier questions and answers.

A conversation belongs to the whole principal (`memory.person_key`): the role, the user id and every attribute. The
same conversation id from another person, or from the same person with another role or supplier, is another
conversation with another session. So a session never carries one person's data into another person's answer, or
data from a wider access into a narrower one.

Sessions are kept. Nothing here deletes them; the transcripts stay in the person's folder under the data folder.
"""

from __future__ import annotations

import datetime as dt
import re
import sqlite3
import threading
import uuid
from pathlib import Path

from agent_workcell.guard import Principal
from agent_workcell.memory import person_key

CONVERSATION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    person TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    started INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (person, conversation_id)
);
"""


def valid_conversation_id(value: object) -> bool:
    return isinstance(value, str) and bool(CONVERSATION_ID.match(value))


class Conversations:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()

    def session(self, principal: Principal, conversation_id: str) -> tuple[str, bool]:
        """The conversation's session id, and whether a run has already started it (so the next run resumes it).
        A new conversation gets a new random session id."""
        if not valid_conversation_id(conversation_id):
            raise ValueError("a conversation id is 1 to 64 letters, digits, '-' or '_'")
        person = person_key(principal)
        now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO conversations (person, conversation_id, session_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (person, conversation_id) DO NOTHING",
                (person, conversation_id, str(uuid.uuid4()), now, now))
            session_id, started = self._db.execute(
                "SELECT session_id, started FROM conversations WHERE person = ? AND conversation_id = ?",
                (person, conversation_id)).fetchone()
        return session_id, bool(started)

    def mark_started(self, principal: Principal, conversation_id: str) -> None:
        """A run reached the runtime, so the session exists: later answers resume it."""
        now = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        with self._lock, self._db:
            self._db.execute("UPDATE conversations SET started = 1, updated_at = ? WHERE person = ? "
                             "AND conversation_id = ?", (now, person_key(principal), conversation_id))

    def close(self) -> None:
        self._db.close()
