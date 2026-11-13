"""The Claude adapter: runs Claude Code through the Claude Agent SDK, with the project's hardening profile.

Claude Code comes with tools of its own (shell, files, web). This adapter removes all of them and gives the model
only the project's tools over MCP:

- `tools=[]` removes the built-in tool set, and `disallowed_tools` names each built-in tool again as a second layer;
- `permission_mode="dontAsk"` denies any call that is not pre-approved, and only the project's MCP tools are;
- `strict_mcp_config` and `setting_sources=[]`: no MCP servers, settings, CLAUDE.md or skills from disk;
- the working folder is an empty folder made for the run, outside any project;
- a turn limit and a cost limit for every run (the server adds a time limit);
- the environment passed on holds only what the CLI needs: every other inherited variable, including database
  URLs and passwords, is blanked;
- the CLI's own configuration folder (CLAUDE_CONFIG_DIR) is an empty folder inside the run's folder, deleted with
  it. Without this the CLI uses the server user's ~/.claude: it signs in with that person's Claude account, tells
  the model the account's email address, and keeps a transcript of every run, data included, in
  ~/.claude/projects. Isolated, it needs ANTHROPIC_API_KEY.

`account_login=True` turns the isolation off, for trying the server locally with a Claude Code sign-in. Never use it
on a server: the account's email reaches the model and transcripts stay on disk, and Anthropic does not allow
products to run on a claude.ai sign-in without its approval. config.check_claude_access keeps the mode on a loopback
address and requires an API key otherwise; the answer service runs one answer at a time in it.

The SDK starts the Claude Code CLI (a Node.js program) as a new process for every run.

Whether this holds is checked by hand with tests/runtime_conformance, against the pinned SDK version.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Mapping

from agent_workcell.runtime.base import Event, Finished, RunRequest, Text, ToolCall, ToolResult
from agent_workcell.tool_server import SERVER_NAME, TOOL_NAMES

# Claude Code's own tools. `tools=[]` already removes them; listing them here removes them again if that ever changes.
BUILT_IN_TOOLS = (
    "Agent", "Task", "Bash", "BashOutput", "KillShell", "TaskStop", "Read", "Write", "Edit", "MultiEdit",
    "NotebookEdit", "Glob", "Grep", "LS", "WebFetch", "WebSearch", "TodoWrite", "TaskCreate", "TaskGet",
    "TaskUpdate", "TaskList", "AskUserQuestion", "Skill", "SlashCommand", "ExitPlanMode", "EnterPlanMode",
    "ListMcpResourcesTool", "ReadMcpResourceTool", "Monitor", "ToolSearch",
)

# Inherited variables the CLI may keep. Everything else is blanked.
KEEP_ENV = {
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "TMPDIR", "HOME",
    "USERPROFILE", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "LANG", "LC_ALL", "TZ",
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_PROFILE",
    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "SSL_CERT_FILE",
}
PREFIX = f"mcp__{SERVER_NAME}__"


def runtime_env(inherited: Mapping[str, str]) -> dict[str, str]:
    """The SDK merges this over the inherited environment, so every variable not kept is set to an empty value."""
    return {name: "" for name in inherited if name.upper() not in KEEP_ENV}


def build_options(request: RunRequest, inherited_env: Mapping[str, str] | None = None, account_login: bool = False):
    from claude_agent_sdk import ClaudeAgentOptions

    # Absolute paths: the runtime starts inside the work folder, so a relative path would point below it.
    workdir = request.workdir.resolve()
    prompt_file = workdir / "system_prompt.md"
    prompt_file.write_text(request.system_prompt, encoding="utf-8")
    env = runtime_env(os.environ if inherited_env is None else inherited_env)
    if not account_login:
        config_dir = workdir / ".claude-config"
        config_dir.mkdir(exist_ok=True)
        env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    return ClaudeAgentOptions(
        tools=[],
        allowed_tools=[PREFIX + name for name in TOOL_NAMES],
        disallowed_tools=list(BUILT_IN_TOOLS),
        permission_mode="dontAsk",
        # A file, not a command-line argument: the guide can be longer than a Windows command line allows.
        system_prompt={"type": "file", "path": str(prompt_file)},
        mcp_servers={SERVER_NAME: {"type": "http", "url": request.mcp_url,
                                   "headers": {"Authorization": f"Bearer {request.mcp_token}"}}},
        strict_mcp_config=True,
        setting_sources=[],
        cwd=str(workdir),
        max_turns=request.max_turns,
        max_budget_usd=request.max_usd,
        model=request.model,
        env=env,
    )


class ClaudeRuntime:
    name = "claude-agent-sdk"

    def __init__(self, account_login: bool = False) -> None:
        from claude_agent_sdk import __version__

        self.version = __version__
        self.account_login = account_login

    async def run(self, request: RunRequest) -> AsyncIterator[Event]:
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeSDKError,
            ResultError,
            ResultMessage,
            TextBlock,
            ToolResultBlock,
            ToolUseBlock,
            UserMessage,
            query,
        )

        names: dict[str, str] = {}
        texts: list[str] = []
        result = None
        try:
            # The message reaches the CLI on standard input (the SDK always uses --input-format stream-json).
            options = build_options(request, account_login=self.account_login)
            async for message in query(prompt=request.message, options=options):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            texts.append(block.text)
                            yield Text(block.text)
                        elif isinstance(block, ToolUseBlock):
                            names[block.id] = block.name.removeprefix(PREFIX)
                            yield ToolCall(names[block.id], dict(block.input))
                elif isinstance(message, UserMessage) and isinstance(message.content, list):
                    for block in message.content:
                        if isinstance(block, ToolResultBlock):
                            yield ToolResult(names.get(block.tool_use_id, "unknown"), bool(block.is_error))
                elif isinstance(message, ResultMessage):
                    result = message
        except ResultError as error:
            # The reason, not the CLI's full text: this error reaches the person and the audit record.
            reason = error.terminal_reason or error.subtype
            status = f" (HTTP {error.api_error_status})" if error.api_error_status else ""
            yield Finished(ok=False, answer="", error=f"runtime stopped: {reason}{status}")
            return
        except ClaudeSDKError as error:
            yield Finished(ok=False, answer="", error=f"runtime failed: {type(error).__name__}")
            return

        if result is None:
            yield Finished(ok=False, error="runtime ended without a result")
            return
        usage = result.usage or {}
        yield Finished(
            ok=not result.is_error,
            answer=result.result or (texts[-1] if texts else ""),
            error=None if not result.is_error else result.subtype,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cost_usd=result.total_cost_usd,
            turns=result.num_turns,
        )
