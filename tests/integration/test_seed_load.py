"""The schema and the seed data load into a real PostgreSQL database."""

import psycopg
import pytest

from reference_erp import seed

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def loaded(erp_test_url):
    with psycopg.connect(erp_test_url) as conn:
        counts = seed.load(conn, reset=True)
        yield conn, counts


def test_every_table_is_loaded_with_the_generated_rows(loaded):
    conn, counts = loaded
    expected = {table: 0 for table in seed.COLUMNS}
    for batch in seed.generate():
        for table, rows in batch.items():
            expected[table] += len(rows)

    assert counts == expected
    for table, count in expected.items():
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == count


def test_new_rows_get_an_id_after_the_seeded_ones(loaded):
    conn, counts = loaded
    new_id = conn.execute(
        "INSERT INTO departments (code, name, annual_budget) VALUES ('TST', 'Test', 0) RETURNING id"
    ).fetchone()[0]
    conn.rollback()

    assert new_id > counts["departments"]


def test_loading_twice_is_refused_without_reset(loaded):
    conn, _ = loaded
    with pytest.raises(RuntimeError, match="already have rows"):
        seed.load(conn)
    conn.rollback()


def test_money_in_the_database_adds_up(loaded):
    conn, _ = loaded
    wrong = conn.execute(
        """
        SELECT COUNT(*) FROM supplier_invoices i
        WHERE i.subtotal <> (SELECT SUM(l.amount) FROM supplier_invoice_lines l WHERE l.supplier_invoice_id = i.id)
           OR i.total_amount <> i.subtotal + i.tax_amount
        """
    ).fetchone()[0]

    assert wrong == 0
