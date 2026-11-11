"""A scripted runtime for tests. It is a real adapter: it calls the tools over MCP, with the run's token, exactly as a
model-driven runtime would. It calls no model and costs nothing.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

import anyio
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from agent_workcell.runtime.base import Event, Finished, RunRequest, Text, ToolCall, ToolResult


@dataclass(frozen=True)
class Call:
    tool: str
    arguments: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Say:
    text: str


@dataclass(frozen=True)
class Wait:
    seconds: float


Step = Call | Say | Wait


class FakeRuntime:
    name = "fake"
    version = "1"

    def __init__(self, script: list[Step] | Callable[[RunRequest], list[Step]], echo: bool = False):
        self.script = script
        self.echo = echo  # when True, each tool result is also said, so it ends up in the answer
        self.results: list[str] = []  # the text of every tool result, for tests to inspect

    async def run(self, request: RunRequest) -> AsyncIterator[Event]:
        steps = self.script(request) if callable(self.script) else self.script
        answer: list[str] = []
        http = create_mcp_http_client(headers={"Authorization": f"Bearer {request.mcp_token}"})
        async with http, Client(streamable_http_client(request.mcp_url, http_client=http)) as client:
            for step in steps:
                if isinstance(step, Wait):
                    await anyio.sleep(step.seconds)
                elif isinstance(step, Say):
                    answer.append(step.text)
                    yield Text(step.text)
                else:
                    yield ToolCall(step.tool, step.arguments)
                    result = await client.call_tool(step.tool, step.arguments)
                    text = "".join(getattr(part, "text", "") for part in result.content)
                    self.results.append(text)
                    yield ToolResult(step.tool, bool(result.is_error))
                    if self.echo:
                        answer.append(text)
                        yield Text(text)
        yield Finished(ok=True, answer="".join(answer), input_tokens=0, output_tokens=0, cost_usd=0.0,
                       turns=len(steps))
