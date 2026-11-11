"""Generate the data guide for one role: every table and column that role may read, with its meaning.

The guide goes to the model with every request. It is generated from the knowledge files, the access policy and the
live catalog, and it is never edited by hand. It leaves out every table and column the role cannot read, so the model
is not told about data it cannot see.
"""

from __future__ import annotations

from agent_workcell.knowledge.catalog import Catalog
from agent_workcell.knowledge.graph import Calculation, Graph
from agent_workcell.policy import Policy, readable_columns


def cell(text: str) -> str:
    return text.replace("|", "\\|")


def calculation_sql(calculation: Calculation) -> str:
    if calculation.query:
        return calculation.query
    # The expression is trusted knowledge SQL, like a policy row rule, and the table name passed NAME validation.
    return f"SELECT {calculation.expression} FROM {calculation.tables[0]}"  # noqa: S608


def visible_calculations(graph: Graph, readable: dict[str, tuple[str, ...]]) -> list[Calculation]:
    return [calc for calc in graph.calculations.values() if all(table in readable for table in calc.tables)]


def column_meaning(graph: Graph, table: str, column: str, readable: dict[str, tuple[str, ...]]) -> str:
    known = graph.tables[table].columns.get(column) if table in graph.tables else None
    if known is None:
        return graph.tables[table].ignored.get(column, "") if table in graph.tables else ""
    parts = [known.description]
    if known.aliases:
        parts.append("Also called: " + ", ".join(known.aliases) + ".")
    if known.link and known.link[0] in readable and known.link[1] in readable[known.link[0]]:
        parts.append(f"Links to {known.link[0]}.{known.link[1]}.")
    if known.not_a_link:
        parts.append(known.not_a_link)
    if known.values:
        parts.append("Values: " + ", ".join(f'`{code}` = "{words}"' for code, words in known.values.items()) + ".")
    return " ".join(parts)


def render_guide(graph: Graph, catalog: Catalog, policy: Policy, role: str) -> str:
    readable = readable_columns(policy, role)
    lines = [
        f'# Data guide for the role "{role}"',
        "",
        # This text reaches the model, so it names no commands, paths or hosts.
        "This guide is generated from the knowledge files and the database. Do not edit it by hand.",
        "",
        f"This role can read {len(readable)} tables. Tables and columns that are not listed here are not available.",
    ]

    for name, visible in readable.items():
        table = graph.tables.get(name)
        lines += ["", f"## {name}", ""]
        if table:
            lines.append(table.record + (" Also called: " + ", ".join(table.aliases) + "." if table.aliases else ""))
            lines.append("")
        lines += ["| Column | Type | Meaning |", "|---|---|---|"]
        for column, kind in catalog.columns.get(name, []):
            if column in visible:
                lines.append(f"| {column} | {kind} | {cell(column_meaning(graph, name, column, readable))} |")

    calculations = visible_calculations(graph, readable)
    if calculations:
        lines += ["", "## Calculations"]
        for calc in calculations:
            aliases = " Also called: " + ", ".join(calc.aliases) + "." if calc.aliases else ""
            lines += ["", f"### {calc.name} (`{calc.id}`)", "", calc.description + aliases, "", "```sql",
                      calculation_sql(calc), "```"]

    rules = [rule for rule in graph.rules.values() if any(table in readable for table in rule.tables)]
    if rules:
        lines += ["", "## Rules", ""]
        lines += [f"- **{rule.id}** ({', '.join(t for t in rule.tables if t in readable)}): {rule.text}"
                  for rule in rules]

    return "\n".join(lines) + "\n"
