---
paths:
  - "src/agent_workcell/eval/**"
  - "bench/**"
  - "reference_erp/eval_cases*"
---

# Evaluation and benchmarks

Status: guard latency benchmark **built** (`bench/guard_latency/run.py`). Evaluation harness **built**:
`src/agent_workcell/eval/`, cases in `reference_erp/eval_cases.toml`.

## Rules

- **A published number needs a recorded run.** The run file goes in `bench/results/` with: date, model and settings,
  runtime version, commit, seed, scale, number of cases, runs per case. No run, no number.
- Gold answers are SQL, not typed numbers. Gold SQL runs **through the guard as the same person**, so it is what that
  person may see.
- Permission probes run SQL **without** the guard to get values the person must never see, then check the answer
  holds none of them. Permission violations must be zero. Report them apart from accuracy.
- Report answers correct, cases correct on every repeat, cost and time. With few cases, give the margin of error and
  do not read meaning into small differences. Compare two set-ups case by case.
- The data's "today" is `ANCHOR_DATE` from `reference_erp/seed.py`. Pass it to the assistant as today's date, or "this
  month" questions will not match the gold answers.
- A grader must not pass by accident. A number must match as a whole number in the answer, not as part of another.
  Each grader has a test with an answer that should fail.
- Benchmarks that call a real model cost money. They never run in CI, and they ask before they start.
- Every run cleans up after itself: chats, files, notes. In the Claude Code sign-in mode the runtime also leaves
  transcripts in `~/.claude/projects`; remove the ones the run made.
- A real run needs a total budget (`--max-usd`). Say the number of answers and the cap before you start.
- A new case must pass with `--runtime fake` (the test suite runs every case that way). Prefer gold values that
  cannot appear by accident: not 0 or 1, not a one-row file.
- Read a result file before it goes into the repository: it holds answers and SQL, and must hold no personal data.
