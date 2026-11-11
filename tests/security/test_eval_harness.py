"""The benchmark harness, end to end on the test database, with scripted runtimes. No model, no cost.

The oracle runtime answers each case with its gold SQL, so every case must pass: this proves the cases, the gold
SQL (through the guard) and the graders fit together. A runtime that answers wrongly and leaks must be caught.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import psycopg
import pytest

from agent_workcell.config import Settings
from agent_workcell.eval import run
from agent_workcell.eval.cases import load_cases
from agent_workcell.runtime import FakeRuntime, Say
from reference_erp import seed
from tests.security.conftest import NAMES, POLICY_PATH
from tests.security.servers import HOST_KEY, KNOWLEDGE, free_port

pytestmark = pytest.mark.db

CASES = Path(__file__).parents[2] / "reference_erp" / "eval_cases.toml"


def settings_for(guard_db, data_dir: Path) -> Settings:
    return Settings(policy=POLICY_PATH, knowledge=KNOWLEDGE, agent_url=guard_db.agent_url,
                    binder_url=guard_db.binder_url, host_key=HOST_KEY, data_dir=data_dir, port=free_port(),
                    runtime="fake", answer_timeout_s=30, answer_max_usd=0.25, daily_answers=100_000, daily_usd=1000,
                    concurrent_answers=1, today=seed.ANCHOR_DATE)


def test_every_reference_case_passes_with_the_oracle_and_nothing_leaks(guard_db, tmp_path):
    document = run.run_benchmark(settings_for(guard_db, tmp_path), guard_db.owner_url, load_cases(CASES), None,
                                 names=NAMES)
    summary = document["summary"]
    wrong = [(result["case"], result["reason"]) for result in document["results"] if result["correct"] is False]
    assert wrong == []
    assert summary["answers_graded"] == summary["answers_correct"] >= 20
    assert summary["probe_answers"] >= 5 and summary["permission_violations"] == 0
    for field in ("date", "commit", "runtime", "runtime_version", "model", "seed", "scale_orders", "today", "cases",
                  "runs_per_case"):
        assert field in document, field
    assert document["model"] == "none" and document["today"] == "2026-06-30"
    assert all(result["tool_calls"] for result in document["results"] if result["expect"] != "none"), \
        "each answer records the queries behind it"


def test_a_wrong_answer_is_graded_wrong_and_a_leak_is_a_violation(guard_db, tmp_path):
    with psycopg.connect(guard_db.owner_url) as owner:
        account = owner.execute("SELECT bank_account FROM suppliers WHERE bank_account IS NOT NULL ORDER BY id"
                                " LIMIT 1").fetchone()[0]
    liar = FakeRuntime([Say(f"There are 999999 of them, worth 1.00 in total. The account is {account}.")])
    only = ["buyer-sent-orders", "buyer-sent-value", "buyer-probe-bank-accounts", "admin-overdue-file"]
    document = run.run_benchmark(settings_for(guard_db, tmp_path), guard_db.owner_url, load_cases(CASES), liar,
                                 max_usd=10, only=only, names=NAMES)
    by_case = {result["case"]: result for result in document["results"]}
    assert by_case["buyer-sent-orders"]["correct"] is False
    assert by_case["buyer-sent-value"]["correct"] is False
    assert by_case["admin-overdue-file"]["correct"] is False and "no file" in by_case["admin-overdue-file"]["reason"]
    assert by_case["buyer-probe-bank-accounts"]["violation"] is True
    assert document["summary"]["permission_violations"] >= 1 and document["summary"]["answers_correct"] == 0


def test_the_harness_stops_before_an_answer_could_pass_the_budget(guard_db, tmp_path):
    document = run.run_benchmark(settings_for(guard_db, tmp_path), guard_db.owner_url, load_cases(CASES),
                                 FakeRuntime([Say("ok")]), max_usd=0.6, only=["buyer-sent-orders", "buyer-sent-value",
                                                                              "admin-suppliers-on-hold"],
                                 repeats=2, names=NAMES)
    # Each answer reserves its 0.25 cap. The fake reports a cost of 0, so the reservation never runs out: all run.
    assert document["summary"]["not_run_budget"] == 0
    document = run.run_benchmark(settings_for(guard_db, tmp_path / "b"), guard_db.owner_url, load_cases(CASES),
                                 FakeRuntime([Say("ok")]), max_usd=0.2, only=["buyer-sent-orders"], names=NAMES)
    assert document["summary"]["not_run_budget"] == 1 and document["summary"]["answers_run"] == 0


def test_an_answer_of_unknown_cost_counts_at_the_cap(guard_db, tmp_path):
    class Unknown(FakeRuntime):
        async def run(self, request):
            async for event in super().run(request):
                yield dataclasses.replace(event, cost_usd=None) if event.type == "finished" else event

    document = run.run_benchmark(settings_for(guard_db, tmp_path), guard_db.owner_url, load_cases(CASES),
                                 Unknown([Say("ok")]), max_usd=0.5,
                                 only=["buyer-sent-orders", "buyer-sent-value", "admin-suppliers-on-hold"],
                                 names=NAMES)
    assert document["summary"]["answers_run"] == 2 and document["summary"]["not_run_budget"] == 1


def test_the_harness_refuses_a_case_whose_gold_finds_nothing(guard_db, tmp_path):
    text = CASES.read_text(encoding="utf-8").replace(
        "gold = \"SELECT count(*) FROM suppliers WHERE status = 'on_hold' AND deleted_at IS NULL\"",
        "gold = \"SELECT id FROM suppliers WHERE false\"")
    broken = tmp_path / "cases.toml"
    broken.write_text(text, encoding="utf-8")
    with pytest.raises(run.BenchError, match="returned nothing"):
        run.run_benchmark(settings_for(guard_db, tmp_path / "d"), guard_db.owner_url, load_cases(broken), None,
                          only=["admin-suppliers-on-hold"], names=NAMES)
