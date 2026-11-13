"""The benchmark harness: ask every case as its person, through the real answer API, and grade the answers.

    python -m agent_workcell.eval --cases reference_erp/eval_cases.toml --runtime fake
    python -m agent_workcell.eval --cases reference_erp/eval_cases.toml --runtime claude --max-usd 2 --out bench/results

- It starts the answer server in this process, on a free local port, with a data folder of its own that is deleted at
  the end (audit, notes and files go with it).
- It plays the host application: each case's person comes from the ERP's own users table, read as the database owner.
- Gold SQL runs through the guard as that person. Forbidden values for permission probes are read without the guard.
- `--runtime fake` answers every case by running its gold SQL. It costs nothing and checks the cases and the harness.
- `--runtime claude` calls a real model and costs money. `--max-usd` is required: before each answer the harness
  reserves the per-answer cap, and stops when the next answer might not fit. An answer whose cost is unknown counts
  at the cap.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import secrets
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx2
import psycopg
import uvicorn
from psycopg import sql

from agent_workcell.config import ConfigError, Settings, check_claude_access, load_settings
from agent_workcell.eval import grade
from agent_workcell.eval.cases import Case, CaseFile, load_cases
from agent_workcell.guard import GuardedConnection, GuardError, Limits, Principal, QueryError
from agent_workcell.guard.plan import DEFAULT_NAMES, Names
from agent_workcell.memory import person_key
from agent_workcell.runtime import Call, FakeRuntime, Runtime, Say
from agent_workcell.server import AnswerService, build_service, create_app

GOLD_LIMITS = Limits(timeout_ms=30_000, max_rows=10_000, max_chars=50_000_000)
MAX_GOLD_VALUES = 25  # an answer cannot be expected to list more


class BenchError(RuntimeError):
    pass


@dataclass
class Asker:
    """One person from the case file, as the host application would send them."""

    principal: Principal
    name: str | None
    params: dict[str, object]


@dataclass
class Prepared:
    case: Case
    asker: Asker
    question: str
    gold_sql: str | None
    gold: list[object] = field(default_factory=list)  # first column of the gold result
    forbidden: list[str] = field(default_factory=list)


# Preparing the cases

def resolve_people(owner_url: str, case_file: CaseFile, attributes: tuple[str, ...]) -> dict[str, Asker]:
    people = {}
    with psycopg.connect(owner_url) as conn:
        for key, person in case_file.people.items():
            with conn.cursor() as cursor:
                cursor.execute(person.sql)
                row = cursor.fetchone()
                names = [column.name for column in cursor.description or []]
            if row is None or "user_id" not in names:
                raise BenchError(f"people.{key}: the SQL must return one row with a user_id column")
            values = dict(zip(names, row, strict=True))
            name = values.pop("name", None)
            attrs = {column: values[column] for column in values if column in attributes and column != "user_id"}
            principal = Principal(person.role, values["user_id"], attrs)
            people[key] = Asker(principal, name, values)  # every column, user_id included, can fill case SQL
    return people


def fill_sql(conn: psycopg.Connection, text: str, params: Mapping[str, object]) -> str:
    """Put a person's values into case SQL as quoted literals, never as raw text."""
    used = {name: sql.Literal(value) for name, value in params.items() if "{" + name + "}" in text}
    return sql.SQL(text).format(**used).as_string(conn) if used else text


def prepare(settings: Settings, owner_url: str, case_file: CaseFile, people: dict[str, Asker],
            policy, cases: list[Case], names: Names = DEFAULT_NAMES) -> list[Prepared]:
    prepared = []
    with psycopg.connect(owner_url) as owner:
        for case in cases:
            asker = people[case.person]
            texts = " ".join(filter(None, (case.question, case.gold, case.forbidden)))
            empty = [name for name, value in asker.params.items() if value is None and "{" + name + "}" in texts]
            if empty:
                raise BenchError(f"{case.id}: people.{case.person} found no value for {', '.join(empty)}")
            try:
                question = case.question.format_map(asker.params)
            except KeyError as error:
                raise BenchError(f"{case.id}: the question names {error}, which people.{case.person} does not return")\
                    from None
            item = Prepared(case, asker, question, fill_sql(owner, case.gold, asker.params) if case.gold else None)
            if item.gold_sql:
                with GuardedConnection(settings.agent_url, settings.binder_url, policy, asker.principal,
                                       GOLD_LIMITS, names) as guarded:
                    try:
                        result = guarded.query(item.gold_sql)
                    except QueryError as error:
                        raise BenchError(f"{case.id}: the gold SQL failed as this person: {error.message}") from None
                item.gold = [row[0] for row in result.rows]
                if not item.gold or item.gold[0] is None:
                    raise BenchError(f"{case.id}: the gold SQL returned nothing, so the case would prove nothing")
                if case.expect == "value" and len(item.gold) != 1:
                    raise BenchError(f"{case.id}: expect = 'value' needs exactly one gold row")
                if case.expect == "values" and len(item.gold) > MAX_GOLD_VALUES:
                    raise BenchError(f"{case.id}: more than {MAX_GOLD_VALUES} gold values")
            if case.forbidden:
                rows = owner.execute(fill_sql(owner, case.forbidden, asker.params)).fetchall()
                item.forbidden = grade.usable_forbidden([row[0] for row in rows])
                if not item.forbidden:
                    raise BenchError(f"{case.id}: the forbidden SQL found nothing, so the probe would prove nothing")
            prepared.append(item)
    return prepared


def oracle_script(item: Prepared) -> list:
    """What the fake runtime does for a case: what a perfect assistant would do, without a model."""
    if item.case.expect == "file":
        return [Call("create_file", {"sql": item.gold_sql, "format": "csv", "title": item.case.id}), Say("Done.")]
    if item.case.expect in ("value", "values"):
        return [Call("run_query", {"sql": item.gold_sql})]
    return [Say("I cannot show that.")]


# Running

@contextmanager
def serving(app, port: int) -> Iterator[str]:
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        if not thread.is_alive():
            raise BenchError("The answer server did not start.")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def ask(url: str, host_key: str, asker: Asker, question: str, timeout: float) -> list[tuple[str, dict]]:
    principal = {"role": asker.principal.role, "user_id": asker.principal.user_id,
                 "attributes": dict(asker.principal.attributes)}
    if asker.name:
        principal["name"] = asker.name
    events, kind = [], None
    with httpx2.stream("POST", f"{url}/v1/answers", json={"principal": principal, "question": question},
                       headers={"Authorization": f"Bearer {host_key}"}, timeout=timeout) as response:
        if response.status_code != 200:
            return [("finished", {"ok": False, "error": f"HTTP {response.status_code}", "answer": ""})]
        for line in response.iter_lines():
            if line.startswith("event: "):
                kind = line[7:]
            elif line.startswith("data: "):
                events.append((kind, json.loads(line[6:])))
    return events


def grade_answer(service: AnswerService, item: Prepared, events: list[tuple[str, dict]]) -> dict:
    finished = events[-1][1] if events and events[-1][0] == "finished" else {"ok": False, "answer": ""}
    answer = finished.get("answer") or ""
    tables = []
    for kind, data in events:
        if kind == "file":
            stored = service.files.get(person_key(item.asker.principal), data["file_id"])
            if stored is not None:
                tables.append(grade.read_table(stored.path, stored.format))

    expect = item.case.expect
    if not finished.get("ok"):
        correct, reason = False, f"the answer failed: {finished.get('error')}"
    elif expect == "value":
        correct = grade.grade_value(answer, item.gold[0])
        reason = "" if correct else f"the answer does not state {grade.as_text(item.gold[0])}"
    elif expect == "values":
        missing = [grade.as_text(value) for value in item.gold if not grade.holds(answer, value)]
        correct, reason = not missing, ("" if not missing else f"missing: {', '.join(missing[:5])}")
    elif expect == "file":
        correct = any(grade.grade_file(table, item.gold) for table in tables)
        reason = "" if correct else ("no file was built" if not tables else "the file does not hold the gold rows")
    else:
        correct, reason = None, ""

    file_texts = ["\n".join(" ".join(row) for row in table) for table in tables]
    leaks = grade.leaked([answer, *file_texts], item.forbidden) if item.forbidden else []
    return {"correct": correct, "reason": reason, "violation": bool(leaks), "leaked": leaks[:5],
            "answer": answer, "error": finished.get("error"), "cost_usd": finished.get("cost_usd"),
            "turns": finished.get("turns"), "input_tokens": finished.get("input_tokens"),
            "output_tokens": finished.get("output_tokens"),
            "files": [data for kind, data in events if kind == "file"]}


def wilson(successes: int, total: int, z: float = 1.96) -> list[float] | None:
    """95% interval for a share. With few cases it is wide; read the result with it."""
    if total == 0:
        return None
    share = successes / total
    centre = (share + z * z / (2 * total)) / (1 + z * z / total)
    margin = z * math.sqrt(share * (1 - share) / total + z * z / (4 * total * total)) / (1 + z * z / total)
    return [round(max(0.0, centre - margin), 3), round(min(1.0, centre + margin), 3)]


def summarise(results: list[dict], repeats: int) -> dict:
    run = [result for result in results if not result.get("not_run")]
    graded = [result for result in run if result["correct"] is not None]
    correct = sum(1 for result in graded if result["correct"])
    by_case: dict[str, list[bool]] = {}
    for result in graded:
        by_case.setdefault(result["case"], []).append(result["correct"])
    costs = [result["cost_usd"] for result in run if result["cost_usd"] is not None]
    return {
        "answers_run": len(run),
        "answers_graded": len(graded),
        "answers_correct": correct,
        "accuracy": round(correct / len(graded), 3) if graded else None,
        "accuracy_95ci": wilson(correct, len(graded)),
        "cases_correct_on_every_repeat": sum(1 for outcomes in by_case.values()
                                             if len(outcomes) == repeats and all(outcomes)),
        "cases_graded": len(by_case),
        "probe_answers": sum(1 for result in run if result["expect"] == "none"),
        "permission_violations": sum(1 for result in run if result["violation"]),
        "failed_answers": sum(1 for result in run if result["error"]),
        "not_run_budget": sum(1 for result in results if result.get("not_run")),
        "cost_usd_total": round(sum(costs), 4),
        "cost_usd_per_answer_mean": round(statistics.mean(costs), 4) if costs else None,
        "duration_s_median": round(statistics.median([result["duration_ms"] for result in run]) / 1000, 2)
        if run else None,
        "turns_mean": round(statistics.mean([result["turns"] for result in run if result["turns"] is not None]), 2)
        if any(result["turns"] is not None for result in run) else None,
    }


def git_commit() -> str:
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
                                timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True,
                               timeout=10).stdout.strip()
        return commit + (" (uncommitted changes)" if dirty else "")
    except (OSError, subprocess.SubprocessError):
        return "none"


def run_benchmark(settings: Settings, owner_url: str, case_file: CaseFile, runtime: Runtime | None, *,
                  repeats: int = 1, max_usd: float = 0.0, only: list[str] | None = None, seed: int = 42,
                  names: Names = DEFAULT_NAMES, progress: Callable[[str], None] = lambda line: None) -> dict:
    """Run the cases and return the result document. `runtime=None` means the fake oracle runtime."""
    cases = [case for case in case_file.cases if not only or case.id in only]
    if only and len(cases) != len(set(only)):
        raise BenchError(f"unknown case ids: {sorted(set(only) - {case.id for case in cases})}")
    service = build_service(settings, runtime or FakeRuntime([]), names=names)
    people = resolve_people(owner_url, case_file, tuple(service.policy.attributes))
    prepared = prepare(settings, owner_url, case_file, people, service.policy, cases, names)
    with psycopg.connect(owner_url) as owner:
        orders = owner.execute("SELECT count(*) FROM purchase_orders").fetchone()[0]

    results: list[dict] = []
    spent = 0.0
    try:
        with serving(create_app(service), settings.port) as url:
            for repeat in range(1, repeats + 1):
                for item in prepared:
                    entry = {"case": item.case.id, "person": item.case.person, "role": item.asker.principal.role,
                             "expect": item.case.expect, "repeat": repeat, "question": item.question}
                    if runtime is not None and spent + settings.answer_max_usd > max_usd + 1e-9:
                        results.append({**entry, "not_run": "the next answer might pass the budget"})
                        continue
                    if runtime is None:
                        service.runtime = FakeRuntime(oracle_script(item), echo=True)
                    started = time.monotonic()
                    events = ask(url, settings.host_key, item.asker, item.question, settings.answer_timeout_s + 30)
                    entry["duration_ms"] = int((time.monotonic() - started) * 1000)
                    entry.update(grade_answer(service, item, events))
                    run_id = service.audit.answers()[-1]["run_id"] if service.audit.answers() else None
                    entry["tool_calls"] = [{"tool": call["tool"], "sql": call["sql_text"], "rows": call["rows"],
                                            "error": call["error"]} for call in service.audit.tool_calls(run_id)] \
                        if run_id else []
                    if runtime is not None:
                        spent += entry["cost_usd"] if entry["cost_usd"] is not None else settings.answer_max_usd
                    results.append(entry)
                    mark = {True: "correct", False: "wrong", None: "probe"}[entry["correct"]]
                    progress(f"{item.case.id} #{repeat}: {mark}{' VIOLATION' if entry['violation'] else ''}"
                             f"{' (' + entry['reason'] + ')' if entry['reason'] else ''}"
                             f" cost={entry['cost_usd']}")
            for asker in people.values():
                service.memory.clear(asker.principal)
    finally:
        service.audit.close()
        service.memory.close()
        service.files.close()

    used = service.runtime
    return {
        "benchmark": "answers",
        "date": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "commit": git_commit(),
        "runtime": used.name,
        "runtime_version": used.version,
        "model": settings.model if runtime is not None else "none",
        "account_login": settings.claude_account_login if runtime is not None else None,
        "seed": seed,
        "scale_orders": orders,
        "today": case_file.today.isoformat(),
        "cases": len(prepared),
        "runs_per_case": repeats,
        "answer_max_usd": settings.answer_max_usd if runtime is not None else None,
        "max_usd": max_usd if runtime is not None else None,
        "max_turns": settings.max_turns,
        "summary": summarise(results, repeats),
        "results": results,
    }


def bench_settings(environ: Mapping[str, str], data_dir: Path, case_file: CaseFile, answer_max_usd: float,
                   max_usd: float) -> Settings:
    env = {**environ, "WORKCELL_DATA_DIR": str(data_dir), "WORKCELL_PORT": str(free_port()),
           "WORKCELL_HOST": "127.0.0.1", "WORKCELL_TODAY": case_file.today.isoformat(),
           "WORKCELL_ANSWER_MAX_USD": str(answer_max_usd), "WORKCELL_DAILY_ANSWERS": "1000000",
           "WORKCELL_DAILY_USD": str(max(max_usd, answer_max_usd) + 1), "WORKCELL_CONCURRENT_ANSWERS": "1"}
    env.setdefault("WORKCELL_HOST_KEY", secrets.token_urlsafe(32))
    if len(env["WORKCELL_HOST_KEY"]) < 24:
        env["WORKCELL_HOST_KEY"] = secrets.token_urlsafe(32)
    return load_settings(env)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent_workcell.eval", description=__doc__.split("\n")[0])
    parser.add_argument("--cases", required=True, help="the case file (TOML)")
    parser.add_argument("--runtime", choices=("fake", "claude"), default="fake")
    parser.add_argument("--max-usd", type=float, default=0.0, help="total budget; required for --runtime claude")
    parser.add_argument("--answer-max-usd", type=float, default=0.25, help="cost cap for each answer")
    parser.add_argument("--repeats", type=int, default=1, help="runs per case")
    parser.add_argument("--only", default="", help="comma-separated case ids")
    parser.add_argument("--seed", type=int, default=42, help="the seed the reference data was loaded with")
    parser.add_argument("--out", default="", help="folder for the result file (bench/results for a recorded run)")
    args = parser.parse_args(argv)

    if args.runtime == "claude" and args.max_usd <= 0:
        parser.error("--runtime claude calls a real model and costs money: give a total budget with --max-usd")
    if args.repeats < 1:
        parser.error("--repeats must be 1 or more")
    owner_url = os.environ.get("ERP_DATABASE_URL", "")
    if not owner_url:
        print("ERP_DATABASE_URL is missing: the harness reads the ERP's users with it.", file=sys.stderr)
        return 2

    case_file = load_cases(args.cases)
    data_dir = Path(tempfile.mkdtemp(prefix="workcell-bench-"))
    try:
        settings = bench_settings(os.environ, data_dir, case_file, args.answer_max_usd, args.max_usd)
        runtime = None
        if args.runtime == "claude":
            from agent_workcell.runtime.claude import ClaudeRuntime

            check_claude_access(settings)
            runtime = ClaudeRuntime(account_login=settings.claude_account_login)
            planned = len(case_file.cases if not args.only else args.only.split(",")) * args.repeats
            print(f"Up to {planned} answers, at most ${args.answer_max_usd:.2f} each; the run stops before passing "
                  f"${args.max_usd:.2f}.", file=sys.stderr)
        document = run_benchmark(settings, owner_url, case_file, runtime, repeats=args.repeats,
                                 max_usd=args.max_usd, only=[item for item in args.only.split(",") if item],
                                 seed=args.seed, progress=lambda line: print(line, file=sys.stderr))
    except (ConfigError, BenchError, GuardError, psycopg.Error) as error:
        print(error, file=sys.stderr)
        return 2
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)

    print(json.dumps(document["summary"], indent=2))
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        stamp = document["date"][:10]
        path = out / f"answers-{stamp}-{document['runtime']}-{document['model']}-{len(document['results'])}.json"
        path.write_text(json.dumps(document, indent=2, default=str, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"Written: {path}", file=sys.stderr)
    return 0

