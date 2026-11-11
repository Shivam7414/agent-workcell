"""Benchmark cases: questions, the people who ask them, and gold SQL.

A case file is TOML:

    today = "2026-06-30"            # the data's "today"; the assistant is given this date

    [people.buyer]
    role = "buyer"
    # Run as the database owner (the host application's own view of its users). It returns one row: user_id, then
    # any of the policy's attributes (such as supplier_id), an optional name, and any other columns, which become
    # {placeholders} for questions and SQL.
    sql = "SELECT id AS user_id, full_name AS name FROM users WHERE ..."

    [[cases]]
    id = "buyer-sent-orders"
    person = "buyer"
    question = "How many of my purchase orders are Sent to Supplier?"
    expect = "value"                # value | values | file | none
    gold = "SELECT count(*) FROM purchase_orders WHERE status = 'sent' AND deleted_at IS NULL"
    forbidden = "SELECT ..."        # optional: values this person must never be shown

- Gold SQL runs through the data guard as the same person, so it is exactly what that person may see.
- `forbidden` runs as the database owner, without the guard, to list values the person must never see.
- `expect = "none"` marks a permission probe: there is no answer to check, only that nothing forbidden leaks.
"""

from __future__ import annotations

import datetime as dt
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

EXPECT = ("value", "values", "file", "none")
ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


class CaseError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("The case file has problems:\n- " + "\n- ".join(problems))


@dataclass(frozen=True)
class Person:
    key: str
    role: str
    sql: str


@dataclass(frozen=True)
class Case:
    id: str
    person: str
    question: str
    expect: str
    gold: str | None = None
    forbidden: str | None = None
    note: str = ""


@dataclass(frozen=True)
class CaseFile:
    today: dt.date
    people: dict[str, Person]
    cases: list[Case]


def parse_cases(text: str) -> CaseFile:
    data = tomllib.loads(text)
    problems: list[str] = []

    try:
        today = dt.date.fromisoformat(str(data.get("today", "")))
    except ValueError:
        problems.append("today must be a date such as 2026-06-30")
        today = dt.date.min

    people: dict[str, Person] = {}
    for key, raw in (data.get("people") or {}).items():
        if not isinstance(raw, dict) or not raw.get("role") or not raw.get("sql"):
            problems.append(f"people.{key}: needs role and sql")
            continue
        people[key] = Person(key, str(raw["role"]), str(raw["sql"]))

    cases: list[Case] = []
    seen: set[str] = set()
    for number, raw in enumerate(data.get("cases") or [], start=1):
        label = f"case {number} ({raw.get('id', 'no id')})"
        case_id = str(raw.get("id", ""))
        if not ID.fullmatch(case_id):
            problems.append(f"{label}: id must be lowercase letters, digits and dashes")
        elif case_id in seen:
            problems.append(f"{label}: the id is used twice")
        seen.add(case_id)
        if raw.get("person") not in people:
            problems.append(f"{label}: unknown person {raw.get('person')!r}")
        if not str(raw.get("question", "")).strip():
            problems.append(f"{label}: needs a question")
        expect = raw.get("expect")
        if expect not in EXPECT:
            problems.append(f"{label}: expect must be one of {', '.join(EXPECT)}")
        if expect in ("value", "values", "file") and not raw.get("gold"):
            problems.append(f"{label}: expect = {expect!r} needs gold SQL")
        if expect == "none" and not raw.get("forbidden"):
            problems.append(f"{label}: a probe (expect = 'none') needs forbidden SQL")
        unknown = set(raw) - {"id", "person", "question", "expect", "gold", "forbidden", "note"}
        if unknown:
            problems.append(f"{label}: unknown keys {sorted(unknown)}")
        cases.append(Case(case_id, str(raw.get("person", "")), str(raw.get("question", "")).strip(),
                          str(expect), raw.get("gold"), raw.get("forbidden"), str(raw.get("note", ""))))

    if not cases:
        problems.append("there are no cases")
    if problems:
        raise CaseError(problems)
    return CaseFile(today, people, cases)


def load_cases(path: str | Path) -> CaseFile:
    return parse_cases(Path(path).read_text(encoding="utf-8"))
