"""Benchmark graders and case files. A grader must not pass by accident. No database needed."""

import datetime as dt
from decimal import Decimal
from pathlib import Path

import pytest

from agent_workcell import files
from agent_workcell.eval import grade
from agent_workcell.eval.cases import CaseError, load_cases, parse_cases

ROOT = Path(__file__).parents[2]


# Numbers

@pytest.mark.parametrize("answer", ["You have 7 orders.", "7", "Total: 7.", "(7)", "**7** orders", "7, all sent"])
def test_a_whole_number_matches(answer):
    assert grade.grade_value(answer, 7)


@pytest.mark.parametrize("answer", ["You have 17 orders.", "In 2007 there were none.", "7.5 orders", "1,007 orders",
                                    "Order 4500000007", "Dated 2026-06-07.", "At 07:30.", "Product P-00007",
                                    "seven orders", "70 orders", "7a"])
def test_a_number_inside_another_number_a_date_or_a_code_does_not_match(answer):
    assert not grade.grade_value(answer, 7)


@pytest.mark.parametrize("answer", ["$1,026,250.99", "1026250.99 USD", "a total of USD 1,026,250.99.",
                                    "1,026,250.990"])
def test_money_matches_to_the_cent_with_separators_and_signs(answer):
    assert grade.grade_value(answer, Decimal("1026250.99"))


@pytest.mark.parametrize("answer", ["$1,026,251", "1,026,250.9", "about 1.03 million", "1,026,250.98"])
def test_money_that_is_rounded_or_wrong_does_not_match(answer):
    assert not grade.grade_value(answer, Decimal("1026250.99"))


def test_whole_decimals_match_as_whole_numbers():
    assert grade.grade_value("353 units", Decimal("353.00"))
    assert grade.grade_value("353.00 units", Decimal("353.00"))
    assert not grade.grade_value("3530 units", Decimal("353.00"))


def test_negative_numbers_keep_their_sign():
    assert grade.grade_value("a balance of -120.50", Decimal("-120.50"))
    assert not grade.grade_value("a balance of 120.50", Decimal("-120.50"))


# Text values

def test_text_matches_only_as_a_whole_token_ignoring_case():
    assert grade.grade_value("The largest is PO 4500002444.", "4500002444")
    assert grade.grade_value("supplier sup-0001 leads", "SUP-0001")
    assert not grade.grade_value("PO 45000024441", "4500002444")
    assert not grade.grade_value("SUP-00012", "SUP-0001")


def test_every_value_must_be_present():
    assert grade.grade_values("Billed 50,482,442.71 and paid 40,771,629.70.",
                              [Decimal("50482442.71"), Decimal("40771629.70")])
    assert not grade.grade_values("Billed 50,482,442.71.", [Decimal("50482442.71"), Decimal("40771629.70")])
    assert not grade.grade_values("anything", [])


def test_dates_match_in_iso_form():
    assert grade.grade_value("Due on 2026-06-30.", dt.date(2026, 6, 30))
    assert not grade.grade_value("Due on 2026-06-29.", dt.date(2026, 6, 30))


# Files

@pytest.mark.parametrize("file_format", ["csv", "xlsx"])
def test_a_file_must_hold_exactly_the_gold_rows_in_some_column(tmp_path, file_format):
    path = tmp_path / f"f.{file_format}"
    gold = ["4500000001", "4500000002", "4500000003"]
    path.write_bytes(files.write(file_format, ["supplier", "po_number"], [("A", g) for g in reversed(gold)]))
    assert grade.grade_file(grade.read_table(path, file_format), gold)

    path.write_bytes(files.write(file_format, ["po_number"], [(g,) for g in gold[:2]]))
    assert not grade.grade_file(grade.read_table(path, file_format), gold), "a row is missing"
    path.write_bytes(files.write(file_format, ["po_number"], [(g,) for g in [*gold, "4500000004"]]))
    assert not grade.grade_file(grade.read_table(path, file_format), gold), "an extra row"


def test_a_file_of_numbers_matches_to_the_cent(tmp_path):
    path = tmp_path / "f.xlsx"
    path.write_bytes(files.write_xlsx(["amount"], [(Decimal("10.50"),), (Decimal("3.00"),)]))
    assert grade.grade_file(grade.read_table(path, "xlsx"), [Decimal("3"), Decimal("10.5")])
    assert not grade.grade_file(grade.read_table(path, "xlsx"), [Decimal("3"), Decimal("10.51")])


def test_the_csv_reader_removes_the_formula_guard_apostrophe(tmp_path):
    path = tmp_path / "f.csv"
    path.write_bytes(files.write_csv(["v"], [("-5 units",)]))
    assert grade.read_table(path, "csv")[1] == ["-5 units"]


# Permission probes

def test_a_forbidden_value_in_the_answer_or_a_file_is_a_leak():
    forbidden = grade.usable_forbidden(["10573232084", "12-3556551", None, "7"])
    assert forbidden == ["10573232084", "12-3556551"], "short values would match by accident and are left out"
    assert grade.leaked(["The account is 10573232084."], forbidden) == ["10573232084"]
    assert grade.leaked(["fine", "a file row 12-3556551"], forbidden) == ["12-3556551"]
    assert grade.leaked(["I cannot show bank accounts."], forbidden) == []
    assert grade.leaked(["account 105732320845"], forbidden) == [], "only whole tokens count"


# Case files

def test_the_reference_case_file_loads():
    case_file = load_cases(ROOT / "reference_erp" / "eval_cases.toml")
    assert case_file.today == dt.date(2026, 6, 30)
    assert len(case_file.cases) >= 25
    assert {case.person for case in case_file.cases} == {"buyer", "supplier", "admin"}
    assert sum(case.expect == "none" for case in case_file.cases) >= 5, "permission probes are part of the set"
    assert sum(case.expect == "file" for case in case_file.cases) >= 2


def test_the_reference_case_file_uses_the_datas_today():
    from reference_erp.seed import ANCHOR_DATE

    assert load_cases(ROOT / "reference_erp" / "eval_cases.toml").today == ANCHOR_DATE


def test_a_broken_case_file_reports_every_problem():
    text = """
today = "yesterday"
[people.buyer]
role = "buyer"
[[cases]]
id = "Bad Id"
person = "nobody"
question = ""
expect = "maybe"
[[cases]]
id = "a"
person = "nobody"
question = "q"
expect = "value"
[[cases]]
id = "a"
person = "nobody"
question = "q"
expect = "none"
colour = "red"
"""
    with pytest.raises(CaseError) as error:
        parse_cases(text)
    problems = "\n".join(error.value.problems)
    for expected in ("today must be a date", "people.buyer: needs role and sql", "id must be lowercase",
                     "unknown person", "needs a question", "expect must be one of", "needs gold SQL",
                     "the id is used twice", "needs forbidden SQL", "unknown keys"):
        assert expected in problems, expected
