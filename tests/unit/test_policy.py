"""The policy loader stops on every mistake it can see, and accepts a correct policy."""

from pathlib import Path

import pytest

from agent_workcell.policy import PolicyError, load_policy, parse_policy
from reference_erp import seed

REFERENCE_POLICY = Path(__file__).parents[2] / "reference_erp" / "policy.toml"

VALID = """
version = 1
schema = "public"
roles = ["admin", "buyer"]

[principal]
user_id = "bigint"
supplier_id = "bigint"

[tables.orders]
visible = ["id", "buyer_id"]
hidden = ["secret"]
rows.admin = "true"
rows.buyer = "buyer_id = {user_id}"
"""


def problems_of(text: str) -> list[str]:
    with pytest.raises(PolicyError) as error:
        parse_policy(text)
    return error.value.problems


def test_a_correct_policy_loads():
    policy = parse_policy(VALID)
    assert policy.roles == ("admin", "buyer")
    assert list(policy.attributes) == ["user_id", "supplier_id"]
    assert policy.tables["orders"].visible == ("id", "buyer_id")
    assert policy.tables["orders"].rows == {"admin": "true", "buyer": "buyer_id = {user_id}"}


@pytest.mark.parametrize(("change", "expected"), [
    (("version = 1", "version = 2"), "'version' must be 1"),
    (('schema = "public"', 'schema = "Public; DROP"'), "'schema' must be"),
    (('roles = ["admin", "buyer"]', "roles = []"), "'roles' must be a list"),
    (('roles = ["admin", "buyer"]', 'roles = ["admin", "admin", "buyer"]'), "lists a role twice"),
    (('roles = ["admin", "buyer"]', 'roles = ["admin", "Buyer X", "buyer"]'), "is not a valid name"),
    (('user_id = "bigint"', 'owner_id = "bigint"'), "must include user_id"),
    (('supplier_id = "bigint"', 'supplier_id = "json"'), "has type 'json'"),
    (("version = 1", "version = 1\nallow_all = true"), "unknown setting 'allow_all'"),
    (('hidden = ["secret"]', 'hiden = ["secret"]'), "unknown setting 'hiden'"),
    (('hidden = ["secret"]', 'hidden = ["buyer_id"]'), "is both visible and hidden"),
    (('visible = ["id", "buyer_id"]', "visible = []"), "no visible columns"),
    (('visible = ["id", "buyer_id"]', 'visible = ["id", "buyer_id", "id"]'), "lists a column twice"),
    (('visible = ["id", "buyer_id"]', 'visible = ["id", "buyer\\"_id"]'), "is not a valid column name"),
    (('visible = ["id", "buyer_id"]', 'visible = ["id", "principal_role"]'), "clashes with the guard"),
    (('rows.admin = "true"\nrows.buyer = "buyer_id = {user_id}"', ""), "no role gets any rows"),
    (('rows.admin = "true"', 'rows.auditor = "true"'), "rows.auditor is for a role that is not in 'roles'"),
    (('rows.admin = "true"', 'rows.admin = ""'), "must be a non-empty SQL condition"),
    (('rows.admin = "true"', 'rows.admin = "true; DROP TABLE orders"'), "must not contain ';'"),
    (('rows.admin = "true"', 'rows.admin = "true -- all"'), "must not contain SQL comments"),
    (('rows.admin = "true"', 'rows.admin = "buyer_id = {tenant_id}"'), "uses {tenant_id}"),
    (('rows.admin = "true"', "rows.admin = \"buyer_id = ANY('{1,2}')\""), "has a brace"),
    (("version = 1", "version = 1\n[[broken"), "not valid TOML"),
])
def test_a_broken_policy_is_refused_with_a_clear_problem(change, expected):
    old, new = change
    assert old in VALID
    problems = problems_of(VALID.replace(old, new))
    assert any(expected in problem for problem in problems), problems


def test_every_problem_is_reported_not_only_the_first():
    text = VALID.replace("version = 1", "version = 3").replace('rows.admin = "true"', 'rows.admin = "x;"')
    assert len(problems_of(text)) == 2


def test_the_digest_changes_when_the_policy_changes():
    assert parse_policy(VALID).digest != parse_policy(VALID + "\n# a comment\n").digest
    assert parse_policy(VALID).digest == parse_policy(VALID).digest


def test_the_reference_policy_marks_every_column_of_every_table():
    policy = load_policy(REFERENCE_POLICY)
    assert set(policy.tables) == set(seed.COLUMNS)
    for name, columns in seed.COLUMNS.items():
        table = policy.tables[name]
        columns = columns + seed.GENERATED_COLUMNS.get(name, ())
        assert sorted(table.visible + table.hidden) == sorted(columns), name
        assert list(table.visible) == [column for column in columns if column in table.visible], \
            f"{name}: keep visible columns in table order"


def test_the_reference_policy_hides_the_sensitive_columns():
    policy = load_policy(REFERENCE_POLICY)
    assert "password_hash" in policy.tables["users"].hidden
    assert {"tax_id", "bank_account"} <= set(policy.tables["suppliers"].hidden)


def test_the_reference_policy_gives_buyers_no_payments_and_only_admins_the_audit_log():
    policy = load_policy(REFERENCE_POLICY)
    assert "buyer" not in policy.tables["payments"].rows
    assert "buyer" not in policy.tables["payment_allocations"].rows
    assert set(policy.tables["audit_log"].rows) == {"admin"}
