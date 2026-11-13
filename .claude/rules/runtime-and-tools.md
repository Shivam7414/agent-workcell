---
paths:
  - "src/agent_workcell/runtime/**"
  - "src/agent_workcell/tools.py"
  - "src/agent_workcell/tool_server.py"
  - "src/agent_workcell/server.py"
  - "src/agent_workcell/prompt.py"
  - "src/agent_workcell/audit.py"
  - "src/agent_workcell/config.py"
  - "src/agent_workcell/memory.py"
  - "src/agent_workcell/files.py"
  - "src/agent_workcell/render.py"
  - "tests/runtime_conformance/**"
  - "tests/security/test_answer_server.py"
---

# Agent runtime, tools and server

Status: **built.** Code: `src/agent_workcell/tools.py`, `tool_server.py`, `runtime/`, `server.py`,
`prompt.py`, `audit.py`, `memory.py`, `files.py`, `render.py`. The design is in `docs/architecture.md`.

## Runtime adapters

- The interface stays small: start one run, get a stream of events. Do not add a method for one runtime's feature.
- Each adapter owns its **hardening profile**: no shell, no file tools, no web access, only our tool server, the person's
  own working folder outside any project, no settings or instruction files picked up from disk, a time cap and a cost cap.
- The person's message goes to the runtime on standard input or through an API field. Never as a command-line
  argument.
- Pass the runtime only the environment variables it needs. Never the database password.
- Give the runtime its own configuration folder inside the person's folder (`CLAUDE_CONFIG_DIR` for Claude Code).
  The server user's own configuration brings a personal sign-in, account details the model can read, and
  transcripts of every run left on disk.
- Products run on an API key. The Claude Code sign-in mode is for one person on their own computer: a loopback
  address only and one answer at a time across the server (`config.check_claude_access`,
  `AnswerService.limit_refusal`). Never widen it, and never pass a subscription token (`CLAUDE_CODE_OAUTH_TOKEN`) to
  the runtime.
- A conformance test must prove the run really happened (an answer, no failure), or it can pass for the wrong reason.
- Pin the runtime's version. Its flags and behaviour change between versions. After changing the Claude adapter or the
  pinned `claude-agent-sdk` version, run `tests/runtime_conformance` by hand and say so in your reply.
- Read SDK behaviour from the installed package or its docs, never from memory: the `mcp` 2.x and Agent SDK APIs differ
  from older versions.
- The fake runtime is a real adapter. Chat-flow tests use it and cost nothing.
- A new adapter needs conformance tests against the real runtime, run by hand: ask it to read a file, run a command
  and fetch a URL, and assert each is denied.

## Tools

- Few tools, each clearly different from the others.
- No tool takes a user, role or tenant argument. Identity comes from the run's server-side token.
- A tool that reads data goes through the guard. There is no second path to the database.
- Tool results are capped in rows and bytes. Tell the model when a result was cut.
- An error message should help the next try: on an unknown column, list the columns that person may use.
- Files and charts reach the browser from tool results, never from the model's text. The server sends a `file`
  event; the model is told not to write a link.
- Text from the database written into a spreadsheet is always written as text, so it can never run as a formula.
  Excel: inline strings only, never a formula element. CSV: an apostrophe before text that starts like a formula.
- A file over its caps is not made. Never hand out a file that silently misses rows.
- A file or a note belongs to the whole principal (`memory.person_key`), not to the user id alone.
- A conversation belongs to the whole principal too. The server chooses its session id; never take one from the
  model or the browser, and never send it to the host.

## Memory and limits

- Notes are untrusted. They go to the model escaped, in their own block, and never decide identity or access.
- Every saved note is shown to the person, and the person can delete their notes.
- Daily limits are per person and UTC day. The cost check reserves the per-answer cap for each running answer and the
  new one. An unknown cost counts at the cap. A limit of zero is a configuration error, never "no limit".

## The answer as HTML

- The answer becomes HTML only in `render.py`: escape everything first, then add tags from the fixed list, never an
  attribute. No images, no clickable links. Feed every new rule the hostile records from `reference_erp/seed.py`.

## Server

- The system prompt is the same for everyone, so it can be cached. The role goes in a fixed system-prompt addition.
  The name, date and memory notes go in the message, in separate tagged blocks.
- Escape those tags wherever a person's text or a note appears, so nobody can fake a trusted block.
- The system prompt holds no server paths, hostnames or set-up details. A test checks this.
- Store an audit record for every answer: run id, model, runtime version, prompt hash, tokens, time, and every tool
  call with its SQL and result size.
