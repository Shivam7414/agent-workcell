"""The runtime interface: start one run, get a stream of events. Every adapter turns its runtime's output into these.

Keep it small. Do not add a method for one runtime's feature.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class RunRequest:
    run_id: str
    system_prompt: str  # the shared prompt plus the role's fixed addition
    message: str  # the tagged user message; never put on a command line
    mcp_url: str
    mcp_token: str  # identifies the run to the tool server; never shown to the model
    model: str
    max_turns: int
    max_usd: float
    workdir: Path  # the person's own folder, outside any project; it holds the runtime's sessions
    # The conversation's session, chosen by the server (a UUID): started when `resume` is False, continued when True.
    # Never taken from the model or the browser. None runs without a named session.
    session_id: str | None = None
    resume: bool = False


@dataclass(frozen=True)
class Text:
    text: str
    type: str = "text"


@dataclass(frozen=True)
class ToolCall:
    name: str  # the tool's own name, without any runtime prefix
    arguments: dict = field(default_factory=dict)
    type: str = "tool_call"


@dataclass(frozen=True)
class ToolResult:
    name: str
    is_error: bool
    type: str = "tool_result"


@dataclass(frozen=True)
class Finished:
    ok: bool
    answer: str = ""
    error: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    turns: int | None = None
    answer_html: str = ""  # set by the server from `answer`, cleaned; whatever a runtime puts here is replaced
    type: str = "finished"


Event = Text | ToolCall | ToolResult | Finished


class Runtime(Protocol):
    name: str
    version: str

    def run(self, request: RunRequest) -> AsyncIterator[Event]:
        """Yield events. The last event is always Finished."""
        ...
