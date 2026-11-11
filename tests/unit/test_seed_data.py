"""The seed data must be the same on every run, and must add up. No database needed."""

import hashlib
from collections import defaultdict
from decimal import Decimal

import pytest

from reference_erp import seed


def rows_by_table(seed_value: int = 42) -> dict[str, list[tuple]]:
    tables: dict[str, list[tuple]] = defaultdict(list)
    for batch in seed.generate(seed_value):
        for table, rows in batch.items():
            tables[table].extend(rows)
    return tables


def as_dicts(tables: dict[str, list[tuple]], table: str) -> list[dict]:
    return [dict(zip(seed.COLUMNS[table], row, strict=True)) for row in tables[table]]


def fingerprint(tables: dict[str, list[tuple]]) -> str:
    return hashlib.sha256(repr(sorted(tables.items())).encode()).hexdigest()


@pytest.fixture(scope="module")
def tables() -> dict[str, list[tuple]]:
    return rows_by_table()


def test_same_seed_gives_the_same_rows(tables):
    assert fingerprint(rows_by_table()) == fingerprint(tables)


def test_another_seed_gives_other_rows(tables):
    assert fingerprint(rows_by_table(7)) != fingerprint(tables)


def test_every_table_has_rows_with_unique_ids(tables):
    for table in seed.COLUMNS:
        ids = [row[0] for row in tables[table]]
        assert ids, f"{table} is empty"
        assert len(ids) == len(set(ids)), f"{table} has repeated ids"
    assert len(tables["purchase_orders"]) == seed.ORDERS_PER_SCALE


def test_rows_match_their_columns(tables):
    for table, columns in seed.COLUMNS.items():
        assert {len(row) for row in tables[table]} == {len(columns)}, table


def test_links_point_to_rows_that_exist(tables):
    ids = {table: {row[0] for row in rows} for table, rows in tables.items()}
    links = [
        ("users", "supplier_id", "suppliers"),
        ("purchase_orders", "supplier_id", "suppliers"),
        ("purchase_orders", "buyer_id", "users"),
        ("purchase_orders", "department_id", "departments"),
        ("purchase_orders", "approved_by", "users"),
        ("purchase_order_lines", "purchase_order_id", "purchase_orders"),
        ("purchase_order_lines", "product_id", "products"),
        ("goods_receipts", "purchase_order_id", "purchase_orders"),
        ("goods_receipt_lines", "goods_receipt_id", "goods_receipts"),
        ("goods_receipt_lines", "purchase_order_line_id", "purchase_order_lines"),
        ("supplier_invoices", "supplier_id", "suppliers"),
        ("supplier_invoices", "purchase_order_id", "purchase_orders"),
        ("supplier_invoices", "submitted_by", "users"),
        ("supplier_invoices", "reviewed_by", "users"),
        ("supplier_invoice_lines", "supplier_invoice_id", "supplier_invoices"),
        ("supplier_invoice_lines", "purchase_order_line_id", "purchase_order_lines"),
        ("payments", "supplier_id", "suppliers"),
        ("payment_allocations", "payment_id", "payments"),
        ("payment_allocations", "supplier_invoice_id", "supplier_invoices"),
    ]
    for table, column, parent in links:
        for row in as_dicts(tables, table):
            assert row[column] is None or row[column] in ids[parent], f"{table}.{column} = {row[column]}"


def test_supplier_users_and_only_they_have_a_supplier(tables):
    for user in as_dicts(tables, "users"):
        assert (user["role"] == "supplier_user") == (user["supplier_id"] is not None)
    assert {user["role"] for user in as_dicts(tables, "users")} == {"admin", "buyer", "supplier_user"}


def test_an_invoice_is_submitted_by_a_user_of_its_own_supplier(tables):
    supplier_of = {user["id"]: user["supplier_id"] for user in as_dicts(tables, "users")}
    for invoice in as_dicts(tables, "supplier_invoices"):
        assert supplier_of[invoice["submitted_by"]] == invoice["supplier_id"]


def test_invoice_totals_add_up(tables):
    line_sums: dict[int, Decimal] = defaultdict(Decimal)
    for line in as_dicts(tables, "supplier_invoice_lines"):
        line_sums[line["supplier_invoice_id"]] += line["amount"]
    for invoice in as_dicts(tables, "supplier_invoices"):
        assert invoice["subtotal"] == line_sums[invoice["id"]]
        assert invoice["total_amount"] == invoice["subtotal"] + invoice["tax_amount"]


def test_the_suppliers_invoice_number_repeats_across_suppliers_but_not_within_one(tables):
    invoices = as_dicts(tables, "supplier_invoices")
    pairs = [(invoice["supplier_id"], invoice["invoice_number"]) for invoice in invoices]
    assert len(pairs) == len(set(pairs))
    assert len({number for _, number in pairs}) < len(pairs)


def test_payments_add_up_and_failed_ones_do_not_count(tables):
    payments = {payment["id"]: payment for payment in as_dicts(tables, "payments")}
    per_payment: dict[int, Decimal] = defaultdict(Decimal)
    paid_per_invoice: dict[int, Decimal] = defaultdict(Decimal)
    for allocation in as_dicts(tables, "payment_allocations"):
        payment = payments[allocation["payment_id"]]
        per_payment[payment["id"]] += allocation["amount"]
        if payment["status"] != "failed":
            paid_per_invoice[allocation["supplier_invoice_id"]] += allocation["amount"]

    for payment in payments.values():
        assert per_payment[payment["id"]] == payment["amount"]

    for invoice in as_dicts(tables, "supplier_invoices"):
        expected = invoice["total_amount"] if invoice["status"] in ("paid", "payment_scheduled") else Decimal(0)
        assert paid_per_invoice[invoice["id"]] == expected, invoice["document_number"]

    assert any(payment["status"] == "failed" for payment in payments.values())


def test_a_partially_received_order_is_never_fully_received(tables):
    ordered: dict[int, Decimal] = defaultdict(Decimal)
    for line in as_dicts(tables, "purchase_order_lines"):
        ordered[line["purchase_order_id"]] += line["quantity"]

    posted = {receipt["id"]: receipt["purchase_order_id"] for receipt in as_dicts(tables, "goods_receipts")
              if receipt["status"] == "posted"}
    received: dict[int, Decimal] = defaultdict(Decimal)
    for line in as_dicts(tables, "goods_receipt_lines"):
        if line["goods_receipt_id"] in posted:
            received[posted[line["goods_receipt_id"]]] += line["quantity_received"]

    partial = [order for order in as_dicts(tables, "purchase_orders") if order["status"] == "partially_received"]
    assert partial
    for order in partial:
        assert 0 < received[order["id"]] < ordered[order["id"]]


def test_the_traps_are_in_the_data(tables):
    orders = as_dicts(tables, "purchase_orders")
    notes = {order["notes"] for order in orders}
    assert notes >= set(seed.HOSTILE_NOTES)
    assert any(order["deleted_at"] for order in orders)
    assert any(receipt["status"] == "reversed" for receipt in as_dicts(tables, "goods_receipts"))
    assert any(invoice["purchase_order_id"] is None for invoice in as_dicts(tables, "supplier_invoices"))
    assert any(line["discount_pct"] is None for line in as_dicts(tables, "purchase_order_lines"))
    assert any(line["tax_rate"] is None for line in as_dicts(tables, "purchase_order_lines"))


def test_scale_must_be_positive():
    with pytest.raises(ValueError):
        next(seed.generate(scale=0))
