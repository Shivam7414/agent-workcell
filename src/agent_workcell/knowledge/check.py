"""Check the knowledge graph against the live database. Any "error" fails the build.

- Every table and column is described, or ignored with a reason. Nothing is described that does not exist.
- Every stored value of an enum column, and every value found in a column with listed values, has screen words.
- Every column that looks like a link (name ends in _id or _by) is a link or says why it is not.
- Every link matches real rows: at least MIN_MATCH of the filled values find their target.
- Every calculation's SQL runs.
- Every rule's source file exists and contains the name it points to.
- Every generated guide is up to date.

Run it on a database that holds representative data, such as the reference ERP's test database.
"""

from __future__ import annotations

from pathlib import Path

import psycopg
from psycopg import sql

from agent_workcell.guard.doctor import Problem
from agent_workcell.knowledge.catalog import Catalog, read_catalog
from agent_workcell.knowledge.graph import Graph
from agent_workcell.knowledge.guide import calculation_sql, render_guide
from agent_workcell.policy import Policy

MIN_MATCH = 0.95
LINK_SUFFIXES = ("_id", "_by")
TIMEOUT_MS = "10000"


def check(conn, graph: Graph, policy: Policy, guide_dir: Path, source_root: Path) -> list[Problem]:
    conn.autocommit = True  # each calculation runs in its own read-only transaction
    catalog = read_catalog(conn, policy.schema)
    problems = _described(graph, catalog)
    problems += _values(conn, graph, catalog, policy.schema)
    problems += _link_names(graph, catalog)
    problems += _link_matches(conn, graph, catalog, policy.schema)
    problems += _calculations(conn, graph, policy.schema)
    problems += _rules(graph, source_root)
    problems += _guides(graph, catalog, policy, guide_dir)
    return problems


def _error(message: str) -> Problem:
    return Problem("error", message)


def _described(graph: Graph, catalog: Catalog) -> list[Problem]:
    problems = []
    for table in sorted(set(catalog.columns) - set(graph.tables) - set(graph.ignored_tables)):
        problems.append(_error(f"table {table} is not described, and not ignored with a reason"))
    for table in sorted((set(graph.tables) | set(graph.ignored_tables)) - set(catalog.columns)):
        problems.append(_error(f"table {table} is in the knowledge but not in the database"))
    for name, table in graph.tables.items():
        live = [column for column, _ in catalog.columns.get(name, [])]
        if not live:
            continue
        for column in live:
            if column not in table.columns and column not in table.ignored:
                problems.append(_error(f"{name}.{column} is not described, and not ignored with a reason"))
        for column in sorted((set(table.columns) | set(table.ignored)) - set(live)):
            problems.append(_error(f"{name}.{column} is in the knowledge but not in the database"))
    return problems


def _values(conn, graph: Graph, catalog: Catalog, schema: str) -> list[Problem]:
    problems = []
    for name, table in graph.tables.items():
        for column, _ in catalog.columns.get(name, []):
            known = table.columns.get(column)
            labels = catalog.enum_labels.get((name, column))
            described = set(known.values) if known else set()
            if labels:
                for label in labels:
                    if label not in described:
                        problems.append(_error(f"{name}.{column}: stored value '{label}' has no screen words"))
                for code in sorted(described - set(labels)):
                    problems.append(_error(f"{name}.{column}: '{code}' is not a value of this column's type"))
            elif known and known.values:
                found = conn.execute(sql.SQL("SELECT DISTINCT {col}::text FROM {table} WHERE {col} IS NOT NULL").format(
                    col=sql.Identifier(column), table=sql.Identifier(schema, name))).fetchall()
                for (value,) in sorted(found):
                    if value not in described:
                        problems.append(_error(f"{name}.{column}: stored value '{value}' has no screen words"))
    return problems


def _link_names(graph: Graph, catalog: Catalog) -> list[Problem]:
    problems = []
    for name, table in graph.tables.items():
        for column, _ in catalog.columns.get(name, []):
            known = table.columns.get(column)
            if column.endswith(LINK_SUFFIXES) and known and not known.link and not known.not_a_link:
                problems.append(_error(f"{name}.{column} looks like a link. Add 'link', or 'not_a_link' with a "
                                       "reason."))
    return problems


def _link_matches(conn, graph: Graph, catalog: Catalog, schema: str) -> list[Problem]:
    problems = []
    for name, table in graph.tables.items():
        for column in table.columns.values():
            if not column.link or catalog.column_type(name, column.name) is None:
                continue
            target, target_column = column.link
            if catalog.column_type(target, target_column) is None:
                continue
            try:
                filled, matched = conn.execute(sql.SQL(
                    "SELECT count(*), count(*) FILTER (WHERE EXISTS "
                    "(SELECT 1 FROM {target} t WHERE t.{target_column} = s.{column})) "
                    "FROM {table} s WHERE s.{column} IS NOT NULL"
                ).format(target=sql.Identifier(schema, target), target_column=sql.Identifier(target_column),
                         table=sql.Identifier(schema, name), column=sql.Identifier(column.name))).fetchone()
            except psycopg.Error as error:
                problems.append(_error(f"{name}.{column.name} -> {target}.{target_column} cannot be joined: "
                                       f"{error.diag.message_primary}"))
                continue
            if filled and matched / filled < MIN_MATCH:
                problems.append(_error(f"{name}.{column.name} -> {target}.{target_column} matches only "
                                       f"{matched} of {filled} filled rows"))
    return problems


def _calculations(conn, graph: Graph, schema: str) -> list[Problem]:
    problems = []
    for calc in graph.calculations.values():
        try:
            with conn.transaction():
                conn.execute("SET TRANSACTION READ ONLY")
                conn.execute("SELECT set_config('statement_timeout', %s, true)", [TIMEOUT_MS])
                conn.execute(sql.SQL("SET LOCAL search_path = {}").format(sql.Identifier(schema)))
                conn.execute(sql.SQL("SELECT * FROM ({}) AS calculation LIMIT 1").format(
                    sql.SQL(calculation_sql(calc))))
        except psycopg.Error as error:
            problems.append(_error(f"calculation {calc.id} does not run: {error.diag.message_primary}"))
    return problems


def _rules(graph: Graph, source_root: Path) -> list[Problem]:
    problems = []
    for rule in graph.rules.values():
        path = source_root / rule.source_path
        if not path.is_file():
            problems.append(_error(f"rule {rule.id}: source file {rule.source_path} does not exist"))
        elif rule.source_name not in path.read_text(encoding="utf-8"):
            problems.append(_error(f"rule {rule.id}: '{rule.source_name}' is not in {rule.source_path}"))
    return problems


def _guides(graph: Graph, catalog: Catalog, policy: Policy, guide_dir: Path) -> list[Problem]:
    problems = []
    for role in policy.roles:
        path = guide_dir / f"{role}.md"
        if not path.is_file() or path.read_text(encoding="utf-8") != render_guide(graph, catalog, policy, role):
            problems.append(_error(f"guide {path.name} is missing or stale. Run: python -m agent_workcell.knowledge "
                                   "guide"))
    return problems
