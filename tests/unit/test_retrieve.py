"""Retrieval and the guide: the right knowledge for a question, and nothing a role cannot read. No database needed."""

from pathlib import Path

import pytest

from agent_workcell.knowledge import Catalog, Retriever, load_graph, render_context, render_guide
from agent_workcell.policy import load_policy
from reference_erp import seed

ROOT = Path(__file__).parents[2] / "reference_erp"
GRAPH = load_graph(ROOT / "knowledge")
POLICY = load_policy(ROOT / "policy.toml")
# The guide needs column types. Without a database, every column gets the same placeholder type.
CATALOG = Catalog({table: [(column, "text") for column in columns] for table, columns in seed.COLUMNS.items()}, {})


@pytest.fixture(scope="module")
def retrievers():
    return {role: Retriever(GRAPH, POLICY, role) for role in POLICY.roles}


def find(retrievers, role: str, question: str):
    return retrievers[role].find(question)


def test_a_table_is_found_by_its_name_or_alias(retrievers):
    assert set(find(retrievers, "admin", "How many POs were raised last month?").tables) == {"purchase_orders"}
    assert "suppliers" in find(retrievers, "admin", "Which vendors are on hold?").tables


def test_screen_words_lead_to_the_stored_value(retrievers):
    context = find(retrievers, "admin", "List the invoices that are Scheduled for Payment")
    assert ("supplier_invoices", "status", "payment_scheduled", "Scheduled for Payment") in context.values

    context = find(retrievers, "admin", "Which orders are sent to supplier but not received?")
    assert ("purchase_orders", "status", "sent", "Sent to Supplier") in context.values


def test_a_common_word_alone_does_not_pull_in_every_table(retrievers):
    assert find(retrievers, "admin", "What is the status?").tables == {}
    assert "No table" in render_context(GRAPH, find(retrievers, "admin", "What is the status?"))


def test_the_join_path_goes_through_the_tables_in_between(retrievers):
    context = find(retrievers, "admin", "Which payments paid purchase order 4500000001?")
    conditions = {join.condition() for join in context.joins}
    assert conditions == {
        "payment_allocations.payment_id = payments.id",
        "payment_allocations.supplier_invoice_id = supplier_invoices.id",
        "supplier_invoices.purchase_order_id = purchase_orders.id",
    }
    assert {"payment_allocations", "supplier_invoices"} <= set(context.tables)


def test_every_link_between_two_tables_is_offered_so_the_model_can_choose(retrievers):
    context = find(retrievers, "admin", "Show each purchase order with its user")
    conditions = {join.condition() for join in context.joins}
    assert {"purchase_orders.buyer_id = users.id", "purchase_orders.approved_by = users.id"} <= conditions


def test_a_calculation_is_found_by_its_alias_and_brings_its_tables(retrievers):
    context = find(retrievers, "buyer", "What is the order value of my biggest order?")
    assert [calc.id for calc in context.calculations] == ["order_value"]
    assert {"purchase_orders", "purchase_order_lines"} <= set(context.tables)


def test_the_rules_of_the_matched_tables_come_along(retrievers):
    context = find(retrievers, "admin", "How much was received on each delivery?")
    assert "reversed_receipts_do_not_count" in [rule.id for rule in context.rules]


def test_a_role_gets_nothing_about_tables_it_cannot_read(retrievers):
    assert "payments" in find(retrievers, "admin", "List the payments").tables
    assert find(retrievers, "buyer", "List the payments").tables == {}
    assert "departments" in find(retrievers, "admin", "Budget per department").tables
    assert find(retrievers, "supplier_user", "Budget per department").tables == {}


def test_a_hidden_column_is_never_matched_or_shown(retrievers):
    assert "bank_account" in GRAPH.tables["suppliers"].columns, "the column must exist, or this proves nothing"
    context = find(retrievers, "admin", "What is the bank account of each supplier?")
    assert "bank_account" not in context.columns.get("suppliers", set())
    assert "bank_account" not in render_context(GRAPH, context)


def test_a_join_through_a_table_the_role_cannot_read_is_not_offered(retrievers):
    admin = find(retrievers, "admin", "Which invoices were paid and by which payment?")
    assert any(join.table == "payment_allocations" for join in admin.joins)
    buyer = find(retrievers, "buyer", "Which invoices were paid and by which payment?")
    assert not any("payment" in join.table for join in buyer.joins)


def test_each_guide_lists_only_what_its_role_can_read():
    admin = render_guide(GRAPH, CATALOG, POLICY, "admin")
    buyer = render_guide(GRAPH, CATALOG, POLICY, "buyer")
    supplier = render_guide(GRAPH, CATALOG, POLICY, "supplier_user")

    assert "## payments" in admin and "## audit_log" in admin
    assert "## payments" not in buyer and "## audit_log" not in buyer
    assert "## departments" not in supplier and "## audit_log" not in supplier
    for guide in (admin, buyer, supplier):
        assert "password_hash" not in guide and "bank_account" not in guide and "tax_id" not in guide


def test_a_guide_does_not_point_to_a_table_its_role_cannot_read():
    admin = render_guide(GRAPH, CATALOG, POLICY, "admin")
    supplier = render_guide(GRAPH, CATALOG, POLICY, "supplier_user")
    assert "Links to departments.id" in admin
    assert "Links to departments.id" not in supplier
    assert "| department_id |" in supplier, "the column itself is visible, only the link is not"


def test_a_guide_includes_the_calculations_and_rules_its_role_can_use():
    buyer = render_guide(GRAPH, CATALOG, POLICY, "buyer")
    assert "(`order_value`)" in buyer and "**no_stored_totals**" in buyer
    assert "(`amount_paid`)" not in buyer, "a buyer cannot read payments"
    assert "(`amount_paid`)" in render_guide(GRAPH, CATALOG, POLICY, "supplier_user")


def test_a_join_path_never_pairs_two_tables_through_a_shared_parent(retrievers):
    """purchase_orders -> suppliers <- payments is shorter, but pairs every order of a supplier with every payment of
    that supplier. The path must go through the allocations and invoices instead."""
    context = find(retrievers, "admin", "Purchase orders and their payments")
    conditions = {join.condition() for join in context.joins}
    assert "payments.supplier_id = suppliers.id" not in conditions
    assert "payment_allocations.payment_id = payments.id" in conditions
    assert "supplier_invoices.purchase_order_id = purchase_orders.id" in conditions
