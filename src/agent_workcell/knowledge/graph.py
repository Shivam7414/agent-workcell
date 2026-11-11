"""Load the knowledge graph: the business meaning of the data, written by hand as TOML files.

    <directory>/tables/<table>.toml     one file per table: what a row is, and every column
    <directory>/calculations.toml       named calculations, each with SQL that the check runs
    <directory>/rules.toml              business rules, each pointing to the code that implements it
    <directory>/ignored.toml            optional: tables that are deliberately not described, with a reason

A table file:

    table = "purchase_orders"
    record = "One purchase order: a request to one supplier for goods."
    aliases = ["PO", "order"]

    [columns.status]
    description = "Where the order is in its life."
    values = { draft = "Draft", sent = "Sent to Supplier" }   # stored value -> the words people see

    [columns.buyer_id]
    description = "The buyer who owns the order."
    link = "users.id"

    [ignored]
    created_at = "Technical time stamp. Use order_date for business questions."

Table and column names are not copied from the database: the check compares these files with the live catalog. This
module only checks the files' own structure.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from agent_workcell.policy import NAME

TABLE_KEYS = {"table", "record", "aliases", "columns", "ignored"}
COLUMN_KEYS = {"description", "aliases", "link", "not_a_link", "values"}
CALCULATION_KEYS = {"name", "description", "aliases", "table", "expression", "tables", "query"}
RULE_KEYS = {"text", "tables", "source"}
SOURCE = re.compile(r"^(?P<path>[^:]+)::(?P<name>.+)$")


class KnowledgeError(ValueError):
    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("The knowledge files are not valid:\n" + "\n".join(f"- {problem}" for problem in problems))


@dataclass(frozen=True)
class Column:
    name: str
    description: str
    aliases: tuple[str, ...] = ()
    link: tuple[str, str] | None = None  # (table, column) this column points to
    not_a_link: str | None = None  # why a column that looks like a link is not one
    values: dict[str, str] = field(default_factory=dict)  # stored value -> screen words


@dataclass(frozen=True)
class Table:
    name: str
    record: str
    aliases: tuple[str, ...]
    columns: dict[str, Column]
    ignored: dict[str, str]  # column -> reason it is not described


@dataclass(frozen=True)
class Calculation:
    id: str
    name: str
    description: str
    aliases: tuple[str, ...]
    tables: tuple[str, ...]
    expression: str | None  # SQL expression on the single table in `tables`
    query: str | None  # or a complete SELECT


@dataclass(frozen=True)
class Rule:
    id: str
    text: str
    tables: tuple[str, ...]
    source_path: str  # the file that implements the rule
    source_name: str  # a name or phrase that must appear in that file


@dataclass(frozen=True)
class Graph:
    tables: dict[str, Table]
    calculations: dict[str, Calculation]
    rules: dict[str, Rule]
    ignored_tables: dict[str, str]


def load_graph(directory: str | Path) -> Graph:
    directory = Path(directory)
    problems: list[str] = []

    tables: dict[str, Table] = {}
    files = sorted((directory / "tables").glob("*.toml"))
    if not files:
        problems.append(f"{directory / 'tables'}: no table files")
    for path in files:
        table = _table(path, _read(path, problems), problems)
        if table:
            tables[table.name] = table

    calculations = {key: _calculation(key, spec, tables, problems)
                    for key, spec in _read(directory / "calculations.toml", problems).items()}
    rules = {key: _rule(key, spec, tables, problems) for key, spec in _read(directory / "rules.toml", problems).items()}

    ignored_tables: dict[str, str] = {}
    if (directory / "ignored.toml").exists():
        raw = _read(directory / "ignored.toml", problems)
        ignored_tables = raw.get("tables", {})
        for name, reason in ignored_tables.items():
            if not NAME.match(name) or not isinstance(reason, str) or not reason.strip():
                problems.append(f"ignored.toml: table {name!r} needs a valid name and a reason")

    for table in tables.values():
        for column in table.columns.values():
            if column.link and (column.link[0] not in tables or column.link[1] not in tables[column.link[0]].columns):
                problems.append(f"{table.name}.{column.name}: link {'.'.join(column.link)} points to a table or "
                                "column the knowledge does not describe")

    if problems:
        raise KnowledgeError(problems)
    return Graph(tables, calculations, rules, ignored_tables)


def _read(path: Path, problems: list[str]) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        problems.append(f"{path.name}: file is missing")
    except tomllib.TOMLDecodeError as error:
        problems.append(f"{path.name}: not valid TOML: {error}")
    return {}


def _words(value: object, where: str, problems: list[str]) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        problems.append(f"{where}: aliases must be a list of words")
        return ()
    return tuple(value)


def _text(value: object, where: str, problems: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{where}: must be a non-empty text")
        return ""
    return " ".join(value.split())


def _table(path: Path, raw: dict, problems: list[str]) -> Table | None:
    if not raw:
        return None
    where = f"tables/{path.name}"
    for key in sorted(set(raw) - TABLE_KEYS):
        problems.append(f"{where}: unknown setting '{key}'")
    name = raw.get("table")
    if name != path.stem or not NAME.match(str(name)):
        problems.append(f"{where}: 'table' must be the table name, and the file must be named after it")
        return None

    columns = {}
    for column, spec in raw.get("columns", {}).items():
        columns[column] = _column(f"{where}: column {column}", column, spec, problems)
    ignored = raw.get("ignored", {})
    for column, reason in ignored.items():
        if not NAME.match(column):
            problems.append(f"{where}: ignored column {column!r} is not a valid name")
        _text(reason, f"{where}: ignored.{column}", problems)
        if column in columns:
            problems.append(f"{where}: column {column} is both described and ignored")

    return Table(name, _text(raw.get("record"), f"{where}: record", problems),
                 _words(raw.get("aliases", []), where, problems), columns, dict(ignored))


def _column(where: str, name: str, spec: object, problems: list[str]) -> Column:
    if not NAME.match(name):
        problems.append(f"{where}: not a valid column name")
    if not isinstance(spec, dict):
        problems.append(f"{where}: must be a TOML table")
        return Column(name, "")
    for key in sorted(set(spec) - COLUMN_KEYS):
        problems.append(f"{where}: unknown setting '{key}'")

    link = None
    if "link" in spec:
        parts = str(spec["link"]).split(".")
        if len(parts) != 2 or not all(NAME.match(part) for part in parts):
            problems.append(f"{where}: link must be written as table.column")
        else:
            link = (parts[0], parts[1])
    not_a_link = _text(spec["not_a_link"], f"{where}: not_a_link", problems) if "not_a_link" in spec else None
    if link and not_a_link:
        problems.append(f"{where}: has both link and not_a_link")

    values = spec.get("values", {})
    if not isinstance(values, dict) or not all(isinstance(word, str) and word.strip() for word in values.values()):
        problems.append(f"{where}: values must map each stored value to the words people see")
        values = {}

    return Column(name, _text(spec.get("description"), f"{where}: description", problems),
                  _words(spec.get("aliases", []), where, problems), link, not_a_link, dict(values))


def _calculation(key: str, spec: object, tables: dict[str, Table], problems: list[str]) -> Calculation:
    where = f"calculations.toml: {key}"
    if not NAME.match(key) or not isinstance(spec, dict):
        problems.append(f"{where}: needs a valid name and a TOML table")
        return Calculation(key, "", "", (), (), None, None)
    for item in sorted(set(spec) - CALCULATION_KEYS):
        problems.append(f"{where}: unknown setting '{item}'")

    expression, query = spec.get("expression"), spec.get("query")
    if (expression is None) == (query is None):
        problems.append(f"{where}: give either expression (with table) or query (with tables)")
    if expression is not None:
        names = (spec.get("table"),)
        if "tables" in spec:
            problems.append(f"{where}: an expression belongs to one table; use 'table'")
    else:
        names = tuple(spec.get("tables", ()))
    if not names or any(name not in tables for name in names):
        problems.append(f"{where}: its tables must be described in the knowledge")
    for sql_text in (expression, query):
        if sql_text is not None and ";" in str(sql_text):
            problems.append(f"{where}: SQL must not contain ';'")

    return Calculation(key, _text(spec.get("name"), f"{where}: name", problems),
                       _text(spec.get("description"), f"{where}: description", problems),
                       _words(spec.get("aliases", []), where, problems), tuple(map(str, names)),
                       _text(expression, f"{where}: expression", problems) if expression is not None else None,
                       _text(query, f"{where}: query", problems) if query is not None else None)


def _rule(key: str, spec: object, tables: dict[str, Table], problems: list[str]) -> Rule:
    where = f"rules.toml: {key}"
    if not NAME.match(key) or not isinstance(spec, dict):
        problems.append(f"{where}: needs a valid name and a TOML table")
        return Rule(key, "", (), "", "")
    for item in sorted(set(spec) - RULE_KEYS):
        problems.append(f"{where}: unknown setting '{item}'")
    names = tuple(spec.get("tables", ()))
    if not names or any(name not in tables for name in names):
        problems.append(f"{where}: its tables must be described in the knowledge")
    source = SOURCE.match(str(spec.get("source", "")))
    if not source:
        problems.append(f"{where}: source must be written as path::name, pointing to the code that implements it")
    return Rule(key, _text(spec.get("text"), f"{where}: text", problems), names,
                source["path"] if source else "", source["name"] if source else "")
