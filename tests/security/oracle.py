"""The independent oracle for the guard tests.

It writes the reference ERP's role rules (reference_erp/README.md, "Roles") a second time, in Python, without
reading the policy file. For each test person it builds a schema that physically holds only the rows and columns that
person may see. The same query then runs on the guard and on the oracle schema, and the results must match. A guard
bug and an oracle bug would have to agree exactly to hide each other.
"""

from __future__ import annotations

from collections import defaultdict

from psycopg import sql

from agent_workcell.guard import Principal
from reference_erp import seed

SENT = {"sent", "partially_received", "received", "closed"}
HIDDEN_COLUMNS = {"suppliers": {"tax_id", "bank_account"}, "users": {"password_hash"}}


def generated_tables(seed_value: int = 42) -> dict[str, list[dict]]:
    tables: dict[str, list[dict]] = defaultdict(list)
    for batch in seed.generate(seed_value):
        for table, rows in batch.items():
            tables[table].extend(dict(zip(seed.COLUMNS[table], row, strict=True)) for row in rows)
    return dict(tables)


def principals_under_test(tables: dict[str, list[dict]]) -> dict[str, Principal | None]:
    """The people the attack suite runs as. None is a connection with nobody bound."""
    users = tables["users"]
    orders = tables["purchase_orders"]
    admin = next(user for user in users if user["role"] == "admin")
    buyer = next(user for user in users if user["id"] == orders[0]["buyer_id"])

    sent_per_supplier: dict[int, int] = defaultdict(int)
    for order in orders:
        if order["status"] in SENT:
            sent_per_supplier[order["supplier_id"]] += 1
    big = max(sent_per_supplier, key=lambda supplier: (sent_per_supplier[supplier], -supplier))
    small = min(sent_per_supplier, key=lambda supplier: (sent_per_supplier[supplier], supplier))

    def supplier_user(supplier_id: int) -> Principal:
        user = next(user for user in users if user["supplier_id"] == supplier_id)
        return Principal("supplier_user", user["id"], {"supplier_id": supplier_id})

    return {
        "admin": Principal("admin", admin["id"]),
        "buyer": Principal("buyer", buyer["id"]),
        "supplier_big": supplier_user(big),
        "supplier_small": supplier_user(small),
        # The host application forgot the supplier id. The person must not get any supplier's rows.
        "supplier_without_id": Principal("supplier_user", next(u for u in users if u["supplier_id"])["id"]),
        "nobody": None,
    }


def visible_rows(tables: dict[str, list[dict]], principal: Principal | None) -> dict[str, list[dict]]:
    """The rows each table should show this person, with hidden columns removed."""
    everything = {table: list(rows) for table, rows in tables.items()}
    if principal is None:
        rows = {table: [] for table in tables}
    elif principal.role == "admin":
        rows = everything
    elif principal.role == "buyer":
        rows = _buyer(tables, principal.user_id)
    elif principal.role == "supplier_user":
        rows = _supplier(tables, principal.attributes.get("supplier_id"))
    else:
        rows = {table: [] for table in tables}

    return {table: [{column: value for column, value in row.items() if column not in HIDDEN_COLUMNS.get(table, ())}
                    for row in table_rows]
            for table, table_rows in rows.items()}


def _buyer(tables: dict[str, list[dict]], user_id: object) -> dict[str, list[dict]]:
    orders = [order for order in tables["purchase_orders"] if order["buyer_id"] == user_id]
    order_ids = {order["id"] for order in orders}
    receipts = [r for r in tables["goods_receipts"] if r["purchase_order_id"] in order_ids]
    receipt_ids = {r["id"] for r in receipts}
    invoices = [i for i in tables["supplier_invoices"] if i["purchase_order_id"] in order_ids]
    invoice_ids = {i["id"] for i in invoices}
    return {
        "departments": tables["departments"],
        "products": tables["products"],
        "suppliers": tables["suppliers"],
        "users": [user for user in tables["users"] if user["role"] in ("admin", "buyer")],
        "purchase_orders": orders,
        "purchase_order_lines": [line for line in tables["purchase_order_lines"]
                                 if line["purchase_order_id"] in order_ids],
        "goods_receipts": receipts,
        "goods_receipt_lines": [line for line in tables["goods_receipt_lines"]
                                if line["goods_receipt_id"] in receipt_ids],
        "supplier_invoices": invoices,
        "supplier_invoice_lines": [line for line in tables["supplier_invoice_lines"]
                                   if line["supplier_invoice_id"] in invoice_ids],
        "payments": [],
        "payment_allocations": [],
        "audit_log": [],
    }


def _supplier(tables: dict[str, list[dict]], supplier_id: object) -> dict[str, list[dict]]:
    if supplier_id is None:
        # In SQL, a comparison with an empty value is never true. A supplier user with no supplier id matches nothing.
        supplier_id = object()
    orders = [o for o in tables["purchase_orders"] if o["supplier_id"] == supplier_id and o["status"] in SENT]
    order_ids = {order["id"] for order in orders}
    receipts = [r for r in tables["goods_receipts"] if r["purchase_order_id"] in order_ids]
    receipt_ids = {r["id"] for r in receipts}
    invoices = [i for i in tables["supplier_invoices"] if i["supplier_id"] == supplier_id]
    invoice_ids = {i["id"] for i in invoices}
    payments = [p for p in tables["payments"] if p["supplier_id"] == supplier_id]
    payment_ids = {p["id"] for p in payments}
    return {
        "departments": [],
        "products": tables["products"],
        "suppliers": [s for s in tables["suppliers"] if s["id"] == supplier_id],
        "users": [user for user in tables["users"] if user["supplier_id"] == supplier_id],
        "purchase_orders": orders,
        "purchase_order_lines": [line for line in tables["purchase_order_lines"]
                                 if line["purchase_order_id"] in order_ids],
        "goods_receipts": receipts,
        "goods_receipt_lines": [line for line in tables["goods_receipt_lines"]
                                if line["goods_receipt_id"] in receipt_ids],
        "supplier_invoices": invoices,
        "supplier_invoice_lines": [line for line in tables["supplier_invoice_lines"]
                                   if line["supplier_invoice_id"] in invoice_ids],
        "payments": payments,
        "payment_allocations": [a for a in tables["payment_allocations"] if a["payment_id"] in payment_ids],
        "audit_log": [],
    }


def build_schema(conn, schema: str, rows: dict[str, list[dict]]) -> None:
    """A schema with a copy of each table that holds only `rows`. Built from the real tables' definitions."""
    conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {schema} CASCADE; CREATE SCHEMA {schema}").format(
        schema=sql.Identifier(schema)))
    for table, columns in seed.COLUMNS.items():
        target = sql.Identifier(schema, table)
        conn.execute(sql.SQL("CREATE TABLE {} (LIKE {})").format(target, sql.Identifier("public", table)))
        for column in sorted(HIDDEN_COLUMNS.get(table, ())):
            conn.execute(sql.SQL("ALTER TABLE {} DROP COLUMN {}").format(target, sql.Identifier(column)))
        kept = [column for column in columns if column not in HIDDEN_COLUMNS.get(table, ())]
        with conn.cursor().copy(sql.SQL("COPY {} ({}) FROM STDIN").format(
                target, sql.SQL(", ").join(map(sql.Identifier, kept)))) as copy:
            for row in rows[table]:
                copy.write_row([row[column] for column in kept])
    conn.commit()
