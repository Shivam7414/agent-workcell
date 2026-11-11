"""The access policy: for each role, which tables, which rows and which columns the agent may read.

The policy is a TOML file written and reviewed by the people who own the data. It is trusted input. Row rules are SQL
conditions, and they reach the database as written. Everything else (names, roles, placeholders) is checked here, so
that a typo stops the load instead of silently widening or narrowing access.

    version = 1
    schema = "public"
    roles = ["admin", "buyer"]

    [principal]              # what the host application tells us about the person, besides the role
    user_id = "bigint"       # required
    supplier_id = "bigint"

    [tables.purchase_orders]
    visible = ["id", "po_number", "buyer_id"]
    hidden = ["internal_note"]
    rows.admin = "true"
    rows.buyer = "buyer_id = {user_id}"

A row rule refers to the table's own columns by name and to the person's attributes as {name}. A role with no rule
for a table gets no rows from it.
"""

from __future__ import annotations

import hashlib
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

NAME = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
ATTRIBUTE_TYPES = ("bigint", "integer", "text", "uuid")
TOP_LEVEL_KEYS = {"version", "schema", "roles", "principal", "tables"}
TABLE_KEYS = {"visible", "hidden", "rows"}


class PolicyError(ValueError):
    """The policy file is not valid. `problems` lists every problem found, not only the first."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("The policy is not valid:\n" + "\n".join(f"- {problem}" for problem in problems))


@dataclass(frozen=True)
class TablePolicy:
    name: str
    visible: tuple[str, ...]
    hidden: tuple[str, ...]
    rows: dict[str, str]  # role -> SQL condition, with {attribute} placeholders


@dataclass(frozen=True)
class Policy:
    schema: str
    roles: tuple[str, ...]
    attributes: dict[str, str]  # attribute -> SQL type. Always starts with user_id.
    tables: dict[str, TablePolicy]
    digest: str  # sha256 of the file. The installed guard records it, and the doctor compares it.


def readable_columns(policy: Policy, role: str) -> dict[str, tuple[str, ...]]:
    """The tables a role gets rows from, in policy order, with their visible columns. Other parts of the project use
    this instead of keeping visibility rules of their own."""
    return {name: table.visible for name, table in policy.tables.items() if role in table.rows}


def load_policy(path: str | Path) -> Policy:
    data = Path(path).read_bytes()
    return parse_policy(data.decode("utf-8"), hashlib.sha256(data).hexdigest())


def parse_policy(text: str, digest: str | None = None) -> Policy:
    try:
        raw = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise PolicyError([f"not valid TOML: {error}"]) from None

    problems: list[str] = []

    for key in sorted(set(raw) - TOP_LEVEL_KEYS):
        problems.append(f"unknown setting '{key}'")
    if raw.get("version") != 1:
        problems.append("'version' must be 1")

    schema = raw.get("schema")
    if not isinstance(schema, str) or not NAME.match(schema):
        problems.append("'schema' must be the name of the schema that holds the business tables")
        schema = ""

    roles = raw.get("roles")
    if not isinstance(roles, list) or not roles:
        problems.append("'roles' must be a list with at least one role")
        roles = []
    for role in roles:
        if not isinstance(role, str) or not NAME.match(role):
            problems.append(f"role {role!r} is not a valid name")
    if len(set(map(str, roles))) != len(roles):
        problems.append("'roles' lists a role twice")

    attributes = _attributes(raw.get("principal"), problems)

    tables_raw = raw.get("tables")
    if not isinstance(tables_raw, dict) or not tables_raw:
        problems.append("'tables' must describe at least one table")
        tables_raw = {}
    tables = {name: _table(name, spec, roles, attributes, problems) for name, spec in tables_raw.items()}

    if problems:
        raise PolicyError(problems)
    return Policy(schema, tuple(roles), attributes, tables, digest or hashlib.sha256(text.encode()).hexdigest())


def _attributes(raw: object, problems: list[str]) -> dict[str, str]:
    if not isinstance(raw, dict) or "user_id" not in raw:
        problems.append("'principal' must list the person's attributes, and must include user_id")
        return {"user_id": "bigint"}
    attributes = {"user_id": raw["user_id"]}
    attributes.update({name: kind for name, kind in raw.items() if name != "user_id"})
    for name, kind in attributes.items():
        if not NAME.match(name):
            problems.append(f"principal attribute {name!r} is not a valid name")
        if kind not in ATTRIBUTE_TYPES:
            problems.append(f"principal attribute '{name}' has type {kind!r}; use one of {', '.join(ATTRIBUTE_TYPES)}")
    return attributes


def _table(name: str, spec: object, roles: list, attributes: dict[str, str], problems: list[str]) -> TablePolicy:
    where = f"table '{name}'"
    if not NAME.match(name):
        problems.append(f"{where}: not a valid table name")
    if not isinstance(spec, dict):
        problems.append(f"{where}: must be a TOML table")
        return TablePolicy(name, (), (), {})

    for key in sorted(set(spec) - TABLE_KEYS):
        problems.append(f"{where}: unknown setting '{key}'")

    visible = _names(spec.get("visible", []), f"{where}: visible", problems)
    hidden = _names(spec.get("hidden", []), f"{where}: hidden", problems)
    if not visible:
        problems.append(f"{where}: no visible columns. Remove the table instead.")
    for column in sorted(set(visible) & set(hidden)):
        problems.append(f"{where}: column '{column}' is both visible and hidden")
    for column in sorted(set(visible) | set(hidden)):
        if column.startswith("principal_"):
            problems.append(f"{where}: column '{column}' clashes with the guard's principal_ names")

    rows = spec.get("rows", {})
    if not isinstance(rows, dict) or not rows:
        problems.append(f"{where}: no role gets any rows. Remove the table instead.")
        rows = {}
    for role, rule in rows.items():
        if role not in roles:
            problems.append(f"{where}: rows.{role} is for a role that is not in 'roles'")
        problems.extend(f"{where}: rows.{role} {problem}" for problem in rule_problems(rule, attributes))

    return TablePolicy(name, tuple(visible), tuple(hidden), dict(rows))


def _names(value: object, where: str, problems: list[str]) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        problems.append(f"{where} must be a list of column names")
        return []
    for item in value:
        if not NAME.match(item):
            problems.append(f"{where}: {item!r} is not a valid column name")
    if len(set(value)) != len(value):
        problems.append(f"{where} lists a column twice")
    return value


def rule_problems(rule: object, attributes: dict[str, str]) -> list[str]:
    """Checks on a row rule's text. The rule is trusted SQL, so these catch mistakes, not attacks."""
    if not isinstance(rule, str) or not rule.strip():
        return ["must be a non-empty SQL condition"]
    problems = []
    if ";" in rule:
        problems.append("must not contain ';'. A rule is one condition.")
    if "--" in rule or "/*" in rule:
        problems.append("must not contain SQL comments")
    for name in PLACEHOLDER.findall(rule):
        if name not in attributes:
            problems.append(f"uses {{{name}}}, which is not a principal attribute")
    if "{" in PLACEHOLDER.sub("", rule) or "}" in PLACEHOLDER.sub("", rule):
        problems.append("has a brace that is not a {attribute} placeholder. Write arrays as ARRAY[...].")
    return problems
