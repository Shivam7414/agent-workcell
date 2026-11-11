"""The knowledge loader refuses broken files with a clear problem, and accepts the reference knowledge."""

from pathlib import Path

import pytest

from agent_workcell.knowledge import KnowledgeError, load_graph
from reference_erp import seed

REFERENCE = Path(__file__).parents[2] / "reference_erp" / "knowledge"

TABLE = """
table = "orders"
record = "One order."
aliases = ["order"]

[columns.id]
description = "Internal number."

[columns.buyer_id]
description = "The buyer."
link = "people.id"

[ignored]
created_at = "Technical time stamp."
"""
PEOPLE = """
table = "people"
record = "One person."

[columns.id]
description = "Internal number."
"""
CALCULATIONS = """
[order_count]
name = "Order count"
description = "How many orders."
tables = ["orders"]
query = "SELECT count(*) FROM orders"
"""
RULES = """
[orders_rule]
text = "A rule about orders."
tables = ["orders"]
source = "app/orders.py::def approve"
"""


def write(directory: Path, table: str = TABLE, calculations: str = CALCULATIONS, rules: str = RULES) -> Path:
    (directory / "tables").mkdir(parents=True, exist_ok=True)
    (directory / "tables" / "orders.toml").write_text(table, encoding="utf-8")
    (directory / "tables" / "people.toml").write_text(PEOPLE, encoding="utf-8")
    (directory / "calculations.toml").write_text(calculations, encoding="utf-8")
    (directory / "rules.toml").write_text(rules, encoding="utf-8")
    return directory


def problems_of(directory: Path) -> list[str]:
    with pytest.raises(KnowledgeError) as error:
        load_graph(directory)
    return error.value.problems


def test_correct_files_load(tmp_path):
    graph = load_graph(write(tmp_path))
    assert graph.tables["orders"].columns["buyer_id"].link == ("people", "id")
    assert graph.tables["orders"].ignored == {"created_at": "Technical time stamp."}
    assert graph.calculations["order_count"].tables == ("orders",)
    assert graph.rules["orders_rule"].source_path == "app/orders.py"
    assert graph.rules["orders_rule"].source_name == "def approve"


@pytest.mark.parametrize(("part", "old", "new", "expected"), [
    ("table", 'table = "orders"', 'table = "order"', "the file must be named after it"),
    ("table", 'record = "One order."', 'record = ""', "record: must be a non-empty text"),
    ("table", 'aliases = ["order"]', 'aliases = ["order"]\nowner = "x"', "unknown setting 'owner'"),
    ("table", 'description = "The buyer."', 'description = "The buyer."\nlink_to = "x"', "unknown setting 'link_to'"),
    ("table", 'description = "Internal number."', 'description = " "', "description: must be a non-empty text"),
    ("table", 'link = "people.id"', 'link = "people"', "link must be written as table.column"),
    ("table", 'link = "people.id"', 'link = "stock.id"', "points to a table or column the knowledge does not"),
    ("table", 'link = "people.id"', 'link = "people.id"\nnot_a_link = "No."', "has both link and not_a_link"),
    ("table", 'created_at = "Technical time stamp."', 'buyer_id = "Not needed."', "both described and ignored"),
    ("table", 'created_at = "Technical time stamp."', 'created_at = ""', "ignored.created_at: must be a non-empty"),
    ("table", 'record = "One order."', 'record = "One order."\n[columns.status]\ndescription = "S."\nvalues = []',
     "values must map each stored value"),
    ("calculations", 'query = "SELECT count(*) FROM orders"', 'query = "SELECT 1"\nexpression = "1"',
     "give either expression"),
    ("calculations", 'tables = ["orders"]', 'tables = ["stock"]', "its tables must be described"),
    ("calculations", "FROM orders", "FROM orders; DROP TABLE orders", "must not contain ';'"),
    ("rules", 'source = "app/orders.py::def approve"', 'source = "app/orders.py"', "source must be written as"),
    ("rules", 'tables = ["orders"]', 'tables = ["stock"]', "its tables must be described"),
    ("rules", 'text = "A rule about orders."', 'text = "A rule.\n', "not valid TOML"),
])
def test_broken_knowledge_is_refused_with_a_clear_problem(tmp_path, part, old, new, expected):
    texts = {"table": TABLE, "calculations": CALCULATIONS, "rules": RULES}
    assert old in texts[part]
    texts[part] = texts[part].replace(old, new)
    write(tmp_path, texts["table"], texts["calculations"], texts["rules"])
    problems = problems_of(tmp_path)
    assert any(expected in problem for problem in problems), problems


def test_a_missing_file_is_reported(tmp_path):
    write(tmp_path)
    (tmp_path / "rules.toml").unlink()
    assert any("rules.toml: file is missing" in problem for problem in problems_of(tmp_path))


def test_the_reference_knowledge_loads_and_covers_every_reference_table():
    graph = load_graph(REFERENCE)
    assert set(graph.tables) == set(seed.COLUMNS)
    for name, columns in seed.COLUMNS.items():
        table = graph.tables[name]
        assert set(table.columns) | set(table.ignored) == set(columns), name


def test_every_reference_column_that_looks_like_a_link_is_explained():
    graph = load_graph(REFERENCE)
    for table in graph.tables.values():
        for column in table.columns.values():
            if column.name.endswith(("_id", "_by")):
                assert column.link or column.not_a_link, f"{table.name}.{column.name}"
