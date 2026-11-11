"""The attack suite. Hostile queries, run as each test person, through the guard.

COMPARED queries must give exactly what the oracle gives: the same columns and rows, or the same error. A query that
reads one row too many, shows a hidden column, or fails only because of a hidden row, is a leak.

REFUSED queries try to step outside the guard views. They must fail for every person, with the expected error.

Add a case for every bypass idea, including ideas that already fail.
"""

import pytest

from agent_workcell.guard import QueryError
from tests.security.conftest import guard_outcome

PEOPLE = ["admin", "buyer", "supplier_big", "supplier_small", "supplier_without_id", "nobody"]

COMPARED = [
    # Plain reads of every table
    "SELECT * FROM purchase_orders",
    "SELECT * FROM purchase_order_lines",
    "SELECT * FROM goods_receipts",
    "SELECT * FROM goods_receipt_lines",
    "SELECT * FROM supplier_invoices",
    "SELECT * FROM supplier_invoice_lines",
    "SELECT * FROM payments",
    "SELECT * FROM payment_allocations",
    "SELECT * FROM suppliers",
    "SELECT * FROM users",
    "SELECT * FROM departments",
    "SELECT * FROM products",
    "SELECT * FROM audit_log",
    "TABLE suppliers",
    "SELECT * FROM ONLY purchase_orders",
    # Conditions that are always true
    "SELECT id FROM purchase_orders WHERE true OR buyer_id <> buyer_id",
    "SELECT id FROM purchase_orders WHERE supplier_id = 1 OR 1 = 1",
    "SELECT id FROM purchase_orders WHERE buyer_id IS NULL OR buyer_id IS NOT NULL",
    "SELECT * FROM purchase_orders WHERE id = 1",
    "SELECT * FROM users WHERE id = 1",
    "SELECT * FROM suppliers WHERE id BETWEEN 1 AND 5",
    # Quoting and case
    'SELECT id FROM "purchase_orders"',
    "SELECT id FROM PURCHASE_ORDERS",
    'SELECT id FROM U&"purchase\\005Forders"',
    # Unions, CTEs, subqueries, LATERAL, window functions, grouping sets
    "SELECT id FROM purchase_orders UNION SELECT id FROM purchase_orders",
    "WITH o AS (SELECT * FROM purchase_orders) SELECT status, count(*) FROM o GROUP BY status",
    "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 3) "
    "SELECT count(*) FROM n, purchase_orders",
    "SELECT o.id, (SELECT count(*) FROM purchase_order_lines l WHERE l.purchase_order_id = o.id) "
    "FROM purchase_orders o",
    "SELECT o.id, x.c FROM purchase_orders o, LATERAL "
    "(SELECT count(*) AS c FROM supplier_invoices i WHERE i.purchase_order_id = o.id) x",
    "SELECT id, count(*) OVER () FROM purchase_orders",
    "SELECT supplier_id, sum(amount) FROM payments GROUP BY ROLLUP (supplier_id)",
    "SELECT count(*) FILTER (WHERE status = 'paid'), sum(total_amount) FROM supplier_invoices",
    # Joins across tables
    "SELECT p.payment_number, i.document_number, a.amount FROM payments p "
    "JOIN payment_allocations a ON a.payment_id = p.id JOIN supplier_invoices i ON i.id = a.supplier_invoice_id",
    "SELECT u.full_name, count(o.id) FROM users u LEFT JOIN purchase_orders o ON o.buyer_id = u.id "
    "GROUP BY u.full_name",
    "SELECT s.name, count(*) FROM suppliers s JOIN purchase_orders o ON o.supplier_id = s.id GROUP BY s.name",
    "SELECT o.po_number, i.document_number FROM purchase_orders o "
    "FULL JOIN supplier_invoices i ON i.purchase_order_id = o.id",
    # Whole rows as text or JSON
    "SELECT row_to_json(o)::text FROM purchase_orders o",
    "SELECT to_jsonb(u) ? 'password_hash' FROM users u",
    "SELECT u::text FROM users u",
    # Errors that would fire only on a hidden row
    "SELECT id FROM purchase_orders WHERE 1 / (supplier_id - 1) IS NOT NULL",
    "SELECT count(*) FROM suppliers WHERE 1 / (id - 2) > 0",
    "SELECT CAST(notes AS integer) FROM purchase_orders WHERE notes IS NOT NULL",
    "SELECT count(*) FROM users WHERE CAST(email AS integer) > 0",
    # Hidden columns
    "SELECT password_hash FROM users",
    "SELECT tax_id, bank_account FROM suppliers",
    "SELECT * FROM users u WHERE u.password_hash IS NOT NULL",
]

REFUSED = [
    # The real tables and the guard's own schema
    ("SELECT * FROM public.purchase_orders", {"42501"}),
    ("SELECT * FROM public.users", {"42501"}),
    ("SELECT count(*) FROM public.suppliers WHERE tax_id IS NOT NULL", {"42501"}),
    ('SELECT * FROM "public"."payments"', {"42501"}),
    ("SELECT * FROM guard_internal.sessions", {"42501"}),
    ("SELECT * FROM guard_internal.principal", {"42501"}),
    ("SELECT guard_internal.bind(pg_backend_pid(), now(), 'admin', 1, NULL)", {"42501"}),
    ("SELECT guard_internal.unbind(pg_backend_pid(), now())", {"42501"}),
    # Becoming someone else
    ("SELECT set_config('role', 'erp_owner', false)", {"42501"}),
    ("SELECT set_config('session_authorization', 'erp_owner', false)", {"42501"}),
    ("SET ROLE erp_owner", {"42601"}),
    ("RESET ROLE", {"42601"}),
    # Files, large objects, other servers, catalog secrets
    ("SELECT pg_read_file('/etc/passwd')", {"42501"}),
    ("SELECT pg_ls_dir('.')", {"42501"}),
    ("SELECT lo_import('/etc/passwd')", {"42501"}),
    ("SELECT * FROM pg_catalog.pg_authid", {"42501"}),
    ("SELECT dblink('host=localhost', 'SELECT 1')", {"42883"}),
    # Writing, and anything that is not one query
    ("INSERT INTO purchase_orders (id) VALUES (999999)", {"42601"}),
    ("UPDATE purchase_orders SET notes = 'x'", {"42601"}),
    ("DELETE FROM purchase_orders", {"42601"}),
    ("WITH gone AS (DELETE FROM purchase_orders RETURNING id) SELECT count(*) FROM gone", {"0A000", "42501", "25006"}),
    ("SELECT nextval('public.purchase_orders_id_seq')", {"42501", "25006"}),
    ("CREATE TABLE stolen AS SELECT * FROM suppliers", {"42601"}),
    ("COPY purchase_orders TO STDOUT", {"42601"}),
    ("SELECT 1; SELECT * FROM public.suppliers", {"42601"}),
    ("SELECT 1; SET statement_timeout = 0", {"42601"}),
    ("SHOW search_path", {"42601"}),
    ("EXPLAIN SELECT * FROM purchase_orders", {"42601"}),
    ("DO $$ BEGIN PERFORM 1; END $$", {"42601"}),
]


@pytest.mark.db
@pytest.mark.parametrize("label", PEOPLE)
@pytest.mark.parametrize("text", COMPARED)
def test_a_hostile_query_returns_exactly_what_the_oracle_returns(guard_db, oracle_db, label, text):
    assert guard_outcome(guard_db, label, text) == oracle_db(label, text)


@pytest.mark.db
@pytest.mark.parametrize("label", ["admin", "buyer", "supplier_big"])
@pytest.mark.parametrize(("text", "codes"), REFUSED)
def test_a_query_outside_the_guard_views_is_refused(guard_db, label, text, codes):
    with pytest.raises(QueryError) as refused:
        guard_db.shared(label).query(text)
    assert refused.value.sqlstate in codes, refused.value.message


@pytest.mark.db
def test_the_compared_queries_are_not_empty_everywhere(guard_db, oracle_db):
    """The comparison would pass for the wrong reason if every query failed or returned nothing."""
    with_rows = [text for text in COMPARED if oracle_db("admin", text)[0] == "rows" and oracle_db("admin", text)[2]]
    assert len(with_rows) > len(COMPARED) * 3 // 4
    errors = [text for text in COMPARED if oracle_db("buyer", text)[0] == "error"]
    assert errors, "some queries must fail, to show that errors are compared too"
