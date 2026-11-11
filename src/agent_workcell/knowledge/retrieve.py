"""Find the knowledge a question needs: by exact names and words, plus the shortest join path between the tables.

No embeddings. Business questions name things: "PO 4500000123", "invoices in review", "paid to suppliers". Those
words are matched against table and column names, aliases, screen words of stored values, and calculation names.
Only tables and columns the person's role may read are considered; visibility comes from the access policy.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

from agent_workcell.knowledge.graph import Calculation, Graph, Rule
from agent_workcell.knowledge.guide import calculation_sql
from agent_workcell.policy import Policy, readable_columns


def normalize(text: str) -> str:
    return " " + " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split()) + " "


def variants(words: str) -> set[str]:
    """A phrase and its simple singular and plural forms."""
    phrase = normalize(words).strip()
    if not phrase:
        return set()
    found = {phrase, phrase + "s", phrase + "es"}
    if phrase.endswith("s"):
        found.add(phrase[:-1])
    return found


@dataclass(frozen=True)
class Join:
    table: str
    column: str
    target_table: str
    target_column: str
    meaning: str

    def condition(self) -> str:
        return f"{self.table}.{self.column} = {self.target_table}.{self.target_column}"


@dataclass
class Context:
    role: str
    tables: dict[str, list[str]] = field(default_factory=dict)  # table -> the words that selected it
    columns: dict[str, set[str]] = field(default_factory=dict)  # table -> matched columns
    values: list[tuple[str, str, str, str]] = field(default_factory=list)  # table, column, stored value, screen words
    calculations: list[Calculation] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    joins: list[Join] = field(default_factory=list)
    unjoined: list[tuple[str, str]] = field(default_factory=list)  # pairs of matched tables with no join path

    def add_table(self, table: str, reason: str) -> None:
        self.tables.setdefault(table, [])
        if reason not in self.tables[table]:
            self.tables[table].append(reason)


class Retriever:
    """Built once per role. `find(question)` is cheap."""

    def __init__(self, graph: Graph, policy: Policy, role: str):
        self.graph = graph
        self.role = role
        self.readable = {name: set(columns) for name, columns in readable_columns(policy, role).items()
                         if name in graph.tables}
        self.table_terms: list[tuple[str, str]] = []  # (variant, table)
        self.column_terms: list[tuple[str, str, str]] = []  # (variant, table, column)
        self.value_terms: list[tuple[str, str, str, str]] = []  # (variant, table, column, stored value)
        self.calculation_terms: list[tuple[str, str]] = []  # (variant, calculation id)

        for name, visible in self.readable.items():
            table = graph.tables[name]
            for words in (name, *table.aliases):
                self.table_terms += [(v, name) for v in variants(words)]
            for column in table.columns.values():
                if column.name not in visible:
                    continue
                for words in (column.name, *column.aliases):
                    self.column_terms += [(v, name, column.name) for v in variants(words)]
                for code, screen in column.values.items():
                    for words in (code, screen):
                        self.value_terms += [(v, name, column.name, code) for v in variants(words)]
        for calc in graph.calculations.values():
            if all(table in self.readable for table in calc.tables):
                for words in (calc.id, calc.name, *calc.aliases):
                    self.calculation_terms += [(v, calc.id) for v in variants(words)]

        self.edges: dict[str, list[Join]] = {name: [] for name in self.readable}
        for name, visible in self.readable.items():
            for column in graph.tables[name].columns.values():
                if column.link and column.name in visible:
                    target, target_column = column.link
                    if target in self.readable and target_column in self.readable[target]:
                        join = Join(name, column.name, target, target_column, column.description)
                        self.edges[name].append(join)
                        self.edges[target].append(join)

    def find(self, question: str) -> Context:
        text = normalize(question)
        context = Context(self.role)

        def said(variant: str) -> bool:
            return f" {variant} " in text

        for variant, table in self.table_terms:
            if said(variant):
                context.add_table(table, variant)
        for variant, calc_id in self.calculation_terms:
            calc = self.graph.calculations[calc_id]
            if said(variant) and calc not in context.calculations:
                context.calculations.append(calc)
                for table in calc.tables:
                    context.add_table(table, f"calculation {calc_id}")

        # A column or value word selects its table only when it is unique among the readable tables and is not the
        # name of a table. Common words ("status", "approved") and table names ("purchase order" is also a value of
        # audit_log.entity_type) only add detail to tables the question already named.
        table_words = {variant for variant, _ in self.table_terms}

        def selects(variant: str, table: str, unique: bool) -> bool:
            return table in context.tables or (unique and variant not in table_words)

        column_hits = [(v, t, c) for v, t, c in self.column_terms if said(v)]
        for variant, table, column in column_hits:
            if selects(variant, table, len({t for w, t, _ in column_hits if w == variant}) == 1):
                context.add_table(table, variant)
                context.columns.setdefault(table, set()).add(column)
        value_hits = [(v, t, c, code) for v, t, c, code in self.value_terms if said(v)]
        for variant, table, column, code in value_hits:
            if selects(variant, table, len({(t, c) for w, t, c, _ in value_hits if w == variant}) == 1):
                context.add_table(table, variant)
                context.columns.setdefault(table, set()).add(column)
                entry = (table, column, code, self.graph.tables[table].columns[column].values[code])
                if entry not in context.values:
                    context.values.append(entry)

        context.rules = [rule for rule in self.graph.rules.values()
                         if any(table in context.tables for table in rule.tables)]
        self._join_paths(context)
        return context

    def _join_paths(self, context: Context) -> None:
        matched = sorted(context.tables)
        for index, start in enumerate(matched):
            for goal in matched[index + 1:]:
                path = self._shortest_path(start, goal)
                if path is None:
                    context.unjoined.append((start, goal))
                    continue
                for here, there in zip(path, path[1:], strict=False):
                    for join in self.edges[here]:
                        if {join.table, join.target_table} == {here, there} and join not in context.joins:
                            context.joins.append(join)
                    if there not in context.tables:
                        context.add_table(there, "on the join path")

    def _shortest_path(self, start: str, goal: str) -> list[str] | None:
        """Breadth-first search over (table, reached as a link target). A path that reaches a table as the target of a
        link may not go on to another table that links to the same one: orders -> suppliers <- payments would pair
        every order of a supplier with every payment of that supplier and multiply the rows."""
        State = tuple[str, bool]
        first: State = (start, False)
        previous: dict[State, State | None] = {first: None}
        queue = deque([first])
        while queue:
            state = queue.popleft()
            here, reached_as_target = state
            if here == goal:
                path = [state]
                while previous[path[-1]] is not None:
                    path.append(previous[path[-1]])
                return [table for table, _ in reversed(path)]
            steps = set()
            for join in self.edges[here]:
                if join.table == here:
                    steps.add((join.target_table, True))
                elif not reached_as_target:
                    steps.add((join.table, False))
            for step in sorted(steps):
                if step not in previous:
                    previous[step] = state
                    queue.append(step)
        return None


def render_context(graph: Graph, context: Context) -> str:
    if not context.tables:
        return "No table, column, value or calculation in the question matched the knowledge. Use the data guide."
    lines = []
    for table, reasons in context.tables.items():
        lines.append(f"## {table}: {graph.tables[table].record} (matched: {', '.join(reasons)})")
        for column in sorted(context.columns.get(table, ())):
            lines.append(f"- {column}: {graph.tables[table].columns[column].description}")
    if context.values:
        lines += ["", "## Stored values"]
        lines += [f'- {table}.{column} = \'{code}\' is shown as "{words}"'
                  for table, column, code, words in context.values]
    if context.joins:
        lines += ["", "## Joins"]
        lines += [f"- {join.condition()}  ({join.meaning})" for join in context.joins]
    for start, goal in context.unjoined:
        lines.append(f"- No join path between {start} and {goal} for this role.")
    if context.calculations:
        lines += ["", "## Calculations"]
        for calc in context.calculations:
            lines += [f"- {calc.name}: {calc.description}", f"  SQL: {calculation_sql(calc)}"]
    if context.rules:
        lines += ["", "## Rules"]
        lines += [f"- {rule.text}" for rule in context.rules]
    return "\n".join(lines) + "\n"
