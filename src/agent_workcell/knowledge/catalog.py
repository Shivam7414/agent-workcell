"""What the live database says about its tables: column names, types, and the labels of enum types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Catalog:
    columns: dict[str, list[tuple[str, str]]]  # table -> [(column, type)] in table order
    enum_labels: dict[tuple[str, str], tuple[str, ...]]  # (table, column) -> labels, for enum columns

    def column_type(self, table: str, column: str) -> str | None:
        return dict(self.columns.get(table, [])).get(column)


def read_catalog(conn, schema: str) -> Catalog:
    columns: dict[str, list[tuple[str, str]]] = {}
    enum_labels: dict[tuple[str, str], tuple[str, ...]] = {}
    rows = conn.execute("""
        SELECT c.relname, a.attname, pg_catalog.format_type(a.atttypid, a.atttypmod),
               (SELECT array_agg(e.enumlabel ORDER BY e.enumsortorder) FROM pg_catalog.pg_enum e
                WHERE e.enumtypid = a.atttypid)
        FROM pg_catalog.pg_class c
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_catalog.pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        WHERE n.nspname = %s AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
        ORDER BY c.relname, a.attnum
    """, [schema]).fetchall()
    for table, column, kind, labels in rows:
        columns.setdefault(table, []).append((column, kind.removeprefix(f"{schema}.")))
        if labels:
            enum_labels[(table, column)] = tuple(labels)
    return Catalog(columns, enum_labels)
