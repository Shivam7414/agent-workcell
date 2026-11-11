"""The generated install SQL. No database needed; the database tests run the same SQL for real."""

import re
from pathlib import Path

import pytest

from agent_workcell.guard import Names, plan_sql
from agent_workcell.guard.plan import bind_call
from agent_workcell.policy import load_policy

POLICY = load_policy(Path(__file__).parents[2] / "reference_erp" / "policy.toml")
PLAN = plan_sql(POLICY)


def view_sql(table: str) -> str:
    match = re.search(rf'CREATE VIEW "guard"\."{table}".*?;\n', PLAN, re.S)
    assert match, table
    return match.group(0)


def test_every_policy_table_becomes_a_security_barrier_view():
    assert PLAN.count("CREATE VIEW \"guard\".") == len(POLICY.tables)
    for table in POLICY.tables:
        assert "WITH (security_barrier)" in view_sql(table)


def test_a_view_selects_only_the_visible_columns():
    for name, table in POLICY.tables.items():
        selected = re.findall(r't\."(\w+)"', view_sql(name).split("FROM")[0])
        assert selected == list(table.visible), name
    assert '"password_hash"' not in PLAN
    assert '"tax_id"' not in PLAN and '"bank_account"' not in PLAN


def test_the_agent_role_is_granted_only_the_guard_schema_and_its_views():
    grants = [line for line in PLAN.splitlines() if line.startswith("GRANT") and 'TO "workcell_agent"' in line]
    assert grants[0] == 'GRANT USAGE ON SCHEMA "guard" TO "workcell_agent";'
    assert grants[1].startswith("GRANT SELECT ON TABLE ")
    assert len(grants) == 2
    granted = re.findall(r'"(\w+)"\."(\w+)"', grants[1])
    assert granted == [("guard", table) for table in POLICY.tables]


def test_the_functions_that_write_identity_are_closed_to_public_and_open_only_to_the_binder():
    assert 'REVOKE ALL ON FUNCTION "guard_internal".bind(integer, timestamptz, text, bigint, bigint) FROM PUBLIC;' \
        in PLAN
    assert 'REVOKE ALL ON FUNCTION "guard_internal".unbind(integer, timestamptz) FROM PUBLIC;' in PLAN
    assert 'REVOKE ALL ON SCHEMA "guard_internal", "guard" FROM PUBLIC;' in PLAN
    assert re.search(r'GRANT EXECUTE ON FUNCTION .*?TO "workcell_binder";', PLAN, re.S)


def test_placeholders_become_the_bound_identity_and_no_brace_is_left():
    assert 'buyer_id = (SELECT "principal_user_id" FROM "guard_internal".principal)' in view_sql("purchase_orders")
    for table in POLICY.tables:
        assert "{" not in view_sql(table) and "}" not in view_sql(table)


def test_a_role_without_a_rule_gets_no_branch_in_the_view():
    payments = view_sql("payments")
    assert "'admin'" in payments and "'supplier_user'" in payments
    assert "'buyer'" not in payments


def test_the_plan_records_which_policy_it_was_made_from():
    assert f"agent-workcell policy sha256:{POLICY.digest}" in PLAN


def test_the_bind_call_has_one_placeholder_per_value():
    text = bind_call(POLICY).as_string()
    assert text.count("%s") == 3 + len(POLICY.attributes)


def test_object_names_that_are_not_plain_identifiers_are_refused():
    with pytest.raises(ValueError):
        Names(agent_role='agent"; DROP ROLE x; --')
    with pytest.raises(ValueError):
        Names(views="Guard")
