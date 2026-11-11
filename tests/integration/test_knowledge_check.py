"""The knowledge check passes on the reference knowledge, and finds each kind of mistake in a broken graph."""

import dataclasses
import shutil
from pathlib import Path

import psycopg
import pytest

from agent_workcell.knowledge import load_graph
from agent_workcell.knowledge.check import check
from agent_workcell.knowledge.graph import Column, Rule
from agent_workcell.policy import load_policy
from reference_erp import seed

pytestmark = pytest.mark.db

ROOT = Path(__file__).parents[2]
KNOWLEDGE = ROOT / "reference_erp" / "knowledge"
POLICY = load_policy(ROOT / "reference_erp" / "policy.toml")


@pytest.fixture(scope="module")
def conn(erp_test_url):
    with psycopg.connect(erp_test_url) as connection:
        seed.load(connection, reset=True)
        yield connection


def run(conn, graph, guide_dir: Path = KNOWLEDGE / "guide") -> list[str]:
    return [problem.message for problem in check(conn, graph, POLICY, guide_dir, ROOT)]


def change_table(graph, name: str, **changes):
    tables = dict(graph.tables)
    tables[name] = dataclasses.replace(graph.tables[name], **changes)
    return dataclasses.replace(graph, tables=tables)


def change_column(graph, table: str, column: str, **changes):
    columns = dict(graph.tables[table].columns)
    columns[column] = dataclasses.replace(columns[column], **changes)
    return change_table(graph, table, columns=columns)


def without_column(graph, table: str, column: str):
    columns = {name: value for name, value in graph.tables[table].columns.items() if name != column}
    return change_table(graph, table, columns=columns)


def test_the_reference_knowledge_passes_every_check(conn):
    assert run(conn, load_graph(KNOWLEDGE)) == []


def broken_cases():
    graph = load_graph(KNOWLEDGE)
    status = graph.tables["purchase_orders"].columns["status"]
    action = graph.tables["audit_log"].columns["action"]
    calc = graph.calculations["order_line_value"]
    rule = graph.rules["reversed_receipts_do_not_count"]
    return [
        ("a column is not described", without_column(graph, "suppliers", "city"),
         "suppliers.city is not described"),
        ("a described column does not exist", change_table(graph, "suppliers", columns={
            **graph.tables["suppliers"].columns, "ghost": Column("ghost", "Not real.")}),
         "suppliers.ghost is in the knowledge but not in the database"),
        ("a table is not described", dataclasses.replace(graph, tables={
            name: table for name, table in graph.tables.items() if name != "departments"}),
         "table departments is not described"),
        ("an enum value has no screen words", change_column(graph, "purchase_orders", "status", values={
            code: words for code, words in status.values.items() if code != "sent"}),
         "purchase_orders.status: stored value 'sent' has no screen words"),
        ("a screen word is given for a value the type does not have", change_column(
            graph, "purchase_orders", "status", values={**status.values, "shipped": "Shipped"}),
         "'shipped' is not a value of this column's type"),
        ("a stored text value has no screen words", change_column(graph, "audit_log", "action", values={
            code: words for code, words in action.values.items() if code != "supplier_invoice.rejected"}),
         "audit_log.action: stored value 'supplier_invoice.rejected' has no screen words"),
        ("a column that looks like a link is not explained", change_column(
            graph, "purchase_orders", "buyer_id", link=None),
         "purchase_orders.buyer_id looks like a link"),
        ("a link matches few real rows", change_column(
            graph, "purchase_orders", "buyer_id", link=("departments", "id")),
         "purchase_orders.buyer_id -> departments.id matches only"),
        ("a link joins columns of incompatible types", change_column(
            graph, "purchase_orders", "buyer_id", link=("departments", "code")),
         "purchase_orders.buyer_id -> departments.code cannot be joined"),
        ("a calculation does not run", dataclasses.replace(graph, calculations={
            **graph.calculations, calc.id: dataclasses.replace(calc, expression="quantity * no_such_column")}),
         "calculation order_line_value does not run"),
        ("a rule points to a file that does not exist", dataclasses.replace(graph, rules={
            **graph.rules, rule.id: dataclasses.replace(rule, source_path="reference_erp/missing.py")}),
         "source file reference_erp/missing.py does not exist"),
        ("a rule points to a name the file does not contain", dataclasses.replace(graph, rules={
            **graph.rules, rule.id: Rule(rule.id, rule.text, rule.tables, rule.source_path, "Never posted.")}),
         "'Never posted.' is not in reference_erp/seed.py"),
    ]


@pytest.mark.parametrize(("case", "graph", "expected"), broken_cases(), ids=[case[0] for case in broken_cases()])
def test_the_check_finds_a_broken_graph(conn, case, graph, expected):
    problems = run(conn, graph)
    assert any(expected in problem for problem in problems), problems


def test_the_check_finds_a_stale_or_missing_guide(conn, tmp_path):
    graph = load_graph(KNOWLEDGE)
    shutil.copytree(KNOWLEDGE / "guide", tmp_path, dirs_exist_ok=True)
    assert run(conn, graph, tmp_path) == []

    edited = tmp_path / "buyer.md"
    edited.write_text(edited.read_text(encoding="utf-8") + "\nA hand edit.\n", encoding="utf-8")
    (tmp_path / "admin.md").unlink()
    problems = run(conn, graph, tmp_path)
    assert any("guide buyer.md is missing or stale" in problem for problem in problems)
    assert any("guide admin.md is missing or stale" in problem for problem in problems)


def test_the_check_finds_a_table_added_to_the_database_but_not_described(conn):
    conn.autocommit = True
    conn.execute("CREATE TABLE public.contracts (id bigint PRIMARY KEY)")
    try:
        problems = run(conn, load_graph(KNOWLEDGE))
    finally:
        conn.execute("DROP TABLE public.contracts")
    assert any("table contracts is not described" in problem for problem in problems)
