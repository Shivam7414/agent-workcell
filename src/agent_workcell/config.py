"""Settings for the answer server, read from the environment.

A missing required setting stops the service with a message that names it. Nothing falls back to a more powerful
account or a wider default.
"""

from __future__ import annotations

import datetime as dt
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

REQUIRED = {
    "WORKCELL_POLICY": "the access policy file",
    "WORKCELL_KNOWLEDGE": "the knowledge directory (with generated guides in guide/)",
    "WORKCELL_AGENT_DATABASE_URL": "the database URL that logs in as the agent role",
    "WORKCELL_BINDER_DATABASE_URL": "the database URL that logs in as the binder role",
    "WORKCELL_HOST_KEY": "the secret the host application sends to call the answer API",
    "WORKCELL_DATA_DIR": "the folder for the audit database and per-run working folders",
}


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    policy: Path
    knowledge: Path
    agent_url: str
    binder_url: str
    host_key: str
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8080
    runtime: str = "claude"
    model: str = "claude-opus-5-5"
    answer_timeout_s: float = 180.0
    answer_max_usd: float = 1.0
    max_turns: int = 16
    query_timeout_ms: int = 10_000
    query_max_rows: int = 200
    query_max_chars: int = 60_000
    daily_answers: int = 100  # per person and UTC day
    daily_usd: float = 10.0  # per person and UTC day
    concurrent_answers: int = 2  # per person
    file_max_rows: int = 50_000
    file_max_chars: int = 20_000_000
    file_keep_hours: float = 24.0
    # Documents. Off unless the documents role's URL is set; see agent_workcell/documents.
    documents_url: str | None = None
    document_max_bytes: int = 100 * 1024 * 1024
    # Local trials only: let the Claude runtime use this computer's Claude Code sign-in. See runtime/claude.py.
    claude_account_login: bool = False
    today: dt.date | None = None  # fixed "today" for the reference ERP; None means the real date

    @property
    def mcp_url(self) -> str:
        return f"http://{self.host}:{self.port}/mcp"

    def today_or_now(self) -> dt.date:
        return self.today or dt.date.today()


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if environ is None else environ
    missing = [f"{name} ({meaning})" for name, meaning in REQUIRED.items() if not env.get(name, "").strip()]
    if missing:
        raise ConfigError("Missing settings. The service will not start without them:\n- " + "\n- ".join(missing))
    if len(env["WORKCELL_HOST_KEY"]) < 24:
        raise ConfigError("WORKCELL_HOST_KEY must be at least 24 characters long.")

    try:
        today = dt.date.fromisoformat(env["WORKCELL_TODAY"]) if env.get("WORKCELL_TODAY") else None
        settings = Settings(
            policy=Path(env["WORKCELL_POLICY"]),
            knowledge=Path(env["WORKCELL_KNOWLEDGE"]),
            agent_url=env["WORKCELL_AGENT_DATABASE_URL"],
            binder_url=env["WORKCELL_BINDER_DATABASE_URL"],
            host_key=env["WORKCELL_HOST_KEY"],
            data_dir=Path(env["WORKCELL_DATA_DIR"]).expanduser().resolve(),
            host=env.get("WORKCELL_HOST", "127.0.0.1"),
            port=int(env.get("WORKCELL_PORT", "8080")),
            runtime=env.get("WORKCELL_RUNTIME", "claude"),
            model=env.get("WORKCELL_MODEL", "claude-opus-5-5"),
            answer_timeout_s=float(env.get("WORKCELL_ANSWER_TIMEOUT_S", "180")),
            answer_max_usd=float(env.get("WORKCELL_ANSWER_MAX_USD", "1.0")),
            max_turns=int(env.get("WORKCELL_MAX_TURNS", "16")),
            daily_answers=int(env.get("WORKCELL_DAILY_ANSWERS", "100")),
            daily_usd=float(env.get("WORKCELL_DAILY_USD", "10")),
            concurrent_answers=int(env.get("WORKCELL_CONCURRENT_ANSWERS", "2")),
            file_max_rows=int(env.get("WORKCELL_FILE_MAX_ROWS", "50000")),
            file_keep_hours=float(env.get("WORKCELL_FILE_KEEP_HOURS", "24")),
            documents_url=env.get("WORKCELL_DOCUMENTS_DATABASE_URL", "").strip() or None,
            document_max_bytes=int(env.get("WORKCELL_DOCUMENT_MAX_BYTES", str(100 * 1024 * 1024))),
            claude_account_login=env.get("WORKCELL_CLAUDE_ACCOUNT_LOGIN", "") == "1",
            today=today,
        )
    except ValueError as error:
        raise ConfigError(f"A setting has an invalid value: {error}") from None
    check_limits(settings)
    check_data_dir(settings.data_dir)
    return settings


# A folder holding one of these is a code project. The runtime runs in the data folder, so it must not sit inside one.
PROJECT_MARKERS = (".git", "CLAUDE.md", "AGENTS.md")


def check_data_dir(data_dir: Path) -> None:
    """The agent runtime runs in a folder under the data folder. It must be outside any code project, so the runtime
    cannot pick up the project's instructions, settings or repository details."""
    for folder in (data_dir, *data_dir.parents):
        found = [marker for marker in PROJECT_MARKERS if (folder / marker).exists()]
        if found:
            raise ConfigError(f"WORKCELL_DATA_DIR ({data_dir}) is inside a code project ({folder} holds "
                              f"{', '.join(found)}). Choose a folder outside it, for example ~/.agent-workcell.")


LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


def check_claude_access(settings: Settings, environ: Mapping[str, str] | None = None) -> None:
    """How the Claude runtime signs in. Called before the Claude runtime starts.

    The default is an API key. Anthropic does not allow products built on the Claude Agent SDK to offer claude.ai
    login or its rate limits unless Anthropic has approved it, so a service for other people must use an API key.
    The sign-in mode is for one person trying the project on their own computer: it must listen on a loopback
    address only (the answer service also runs one answer at a time in it; see server.py)."""
    env = os.environ if environ is None else environ
    if settings.claude_account_login:
        if settings.host not in LOOPBACK_HOSTS:
            raise ConfigError(f"WORKCELL_CLAUDE_ACCOUNT_LOGIN=1 is for a local trial on your own computer, so "
                              f"WORKCELL_HOST must be 127.0.0.1, not {settings.host!r}. A service that other people "
                              "use must run with ANTHROPIC_API_KEY.")
        return
    if not env.get("ANTHROPIC_API_KEY", "").strip():
        raise ConfigError("ANTHROPIC_API_KEY is missing. The Claude runtime needs an API key. For a local trial "
                          "with your own Claude Code sign-in, set WORKCELL_CLAUDE_ACCOUNT_LOGIN=1 instead.")


def check_limits(settings: Settings) -> None:
    """A limit of zero would mean "no limit" to some readers and "nothing" to others. Refuse it."""
    positive = ("answer_timeout_s", "answer_max_usd", "max_turns", "daily_answers", "daily_usd", "concurrent_answers",
                "file_max_rows", "file_max_chars", "file_keep_hours", "document_max_bytes")
    bad = [name for name in positive if not getattr(settings, name) > 0]
    if bad:
        raise ConfigError("These limits must be greater than zero: " + ", ".join(bad))
    if settings.daily_usd < settings.answer_max_usd:
        raise ConfigError("WORKCELL_DAILY_USD must be at least WORKCELL_ANSWER_MAX_USD, or no answer could start.")
