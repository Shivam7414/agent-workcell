"""Files built from query results: values from the database can never run as formulas. No database needed."""

import csv
import datetime as dt
import io
import re
import time
import zipfile
from decimal import Decimal
from xml.etree import ElementTree

import pytest

from agent_workcell import files
from agent_workcell.files import FileStore
from reference_erp.seed import HOSTILE_NOTES

NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
FORMULA_TEXT = ["=1+1", "+1", "-1+A1", "@SUM(A1)", "\t=1", "\r=1", "＝1+1", *HOSTILE_NOTES]


def read_csv(content: bytes) -> list[list[str]]:
    assert content.startswith("\ufeff".encode())
    return list(csv.reader(io.StringIO(content.decode("utf-8-sig"), newline="")))


def sheet(content: bytes) -> ElementTree.Element:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for name in archive.namelist():  # every part must be well-formed XML
            if name.endswith((".xml", ".rels")):
                ElementTree.fromstring(archive.read(name))
        return ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))


def cells(content: bytes) -> dict[str, ElementTree.Element]:
    return {cell.get("r"): cell for cell in sheet(content).iterfind(".//x:c", NS)}


def cell_text(cell: ElementTree.Element) -> str:
    return "".join(part.text or "" for part in cell.iterfind(".//x:t", NS))


# CSV

@pytest.mark.parametrize("text", FORMULA_TEXT)
def test_csv_text_that_looks_like_a_formula_is_written_as_text(text):
    rows = read_csv(files.write_csv(["notes"], [(text,)]))
    starts_a_formula = text[0] in "=+-@\t\r＝＋－＠"
    assert rows[1][0] == ("'" + text if starts_a_formula else text)


def test_the_hostile_seed_notes_include_formulas_so_the_csv_test_checks_something():
    assert sum(text[0] in "=+-@" for text in HOSTILE_NOTES) >= 2


def test_csv_writes_numbers_dates_and_empty_values_as_they_are():
    rows = read_csv(files.write_csv(["a", "b", "c", "d", "e"],
                                    [(-5, Decimal("-12.50"), dt.date(2026, 6, 30), None, "plain text")]))
    assert rows[1] == ["-5", "-12.50", "2026-06-30", "", "plain text"]


def test_csv_column_names_are_cleaned_too():
    assert read_csv(files.write_csv(["=cmd"], []))[0] == ["'=cmd"]


def test_csv_keeps_line_breaks_and_commas_inside_one_cell():
    rows = read_csv(files.write_csv(["a", "b"], [("line one\nline two, with a comma", 1)]))
    assert rows[1] == ["line one\nline two, with a comma", "1"]


# Excel

def test_excel_never_writes_a_formula_and_keeps_hostile_text_as_text():
    content = files.write_xlsx(["notes"], [(text,) for text in FORMULA_TEXT])
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        for name in archive.namelist():
            assert b"<f>" not in archive.read(name) and b"<f " not in archive.read(name), name
    found = cells(content)
    for number, text in enumerate(FORMULA_TEXT, start=2):
        cell = found[f"A{number}"]
        assert cell.get("t") == "inlineStr"
        assert cell_text(cell) == text.replace("\r", "\n") or cell_text(cell) == text


def test_excel_writes_numbers_as_numbers_and_dates_as_dates():
    found = cells(files.write_xlsx(["a", "b", "c", "d", "e"],
                                   [(42, Decimal("1234.56"), 2.5, dt.date(2026, 6, 30), True)]))
    assert found["A2"].get("t") is None and found["A2"].find("x:v", NS).text == "42"
    assert found["B2"].find("x:v", NS).text == "1234.56"
    assert found["C2"].find("x:v", NS).text == "2.5"
    assert found["D2"].get("s") == "2" and found["D2"].find("x:v", NS).text == str(
        (dt.date(2026, 6, 30) - dt.date(1899, 12, 30)).days)
    assert found["E2"].get("t") == "b"


def test_excel_keeps_long_numbers_exact_by_writing_them_as_text():
    found = cells(files.write_xlsx(["a", "b"], [(1234567890123456789, Decimal("NaN"))]))
    assert found["A2"].get("t") == "inlineStr" and cell_text(found["A2"]) == "1234567890123456789"
    assert found["B2"].get("t") == "inlineStr"


def test_excel_writes_times_with_their_offset_as_text_and_skips_empty_values():
    moment = dt.datetime(2026, 6, 30, 9, 15, tzinfo=dt.UTC)
    found = cells(files.write_xlsx(["a", "b"], [(moment, None)]))
    assert cell_text(found["A2"]) == "2026-06-30T09:15:00+00:00"
    assert "B2" not in found


def test_excel_replaces_characters_that_xml_cannot_hold():
    found = cells(files.write_xlsx(["a"], [("bell\x07 and null\x00 & <tag>",)]))
    assert cell_text(found["A2"]) == "bell\ufffd and null\ufffd & <tag>"


def test_excel_columns_past_z_are_named_correctly():
    assert [files.column_letter(index) for index in (0, 25, 26, 51, 52, 701, 702)] == \
        ["A", "Z", "AA", "AZ", "BA", "ZZ", "AAA"]


# Names and storage

@pytest.mark.parametrize(("title", "expected"), [
    ("Open orders", "Open orders.xlsx"),
    ("../../etc/passwd", "etcpasswd.xlsx"),
    ("C:\\Windows\\x", "CWindowsx.xlsx"),
    ("..", "data.xlsx"),
    ("", "data.xlsx"),
    ('a"; filename=evil.exe', "a filenameevil.exe.xlsx"),
])
def test_file_names_from_the_model_cannot_name_a_path(title, expected):
    assert files.file_name(title, "xlsx") == expected


def test_a_file_is_handed_out_only_to_its_owner_and_only_until_it_expires(tmp_path):
    store = FileStore(tmp_path, keep_hours=1)
    stored = store.save("person-a", "run-1", "data.csv", "csv", 1, b"x")
    assert store.get("person-a", stored.file_id).path.read_bytes() == b"x"
    assert store.get("person-b", stored.file_id) is None
    assert store.get("person-a", "made-up-id") is None
    assert re.fullmatch(r"[A-Za-z0-9_-]{24}", stored.file_id)

    store.keep = files.dt.timedelta(seconds=0)
    time.sleep(1.1)
    assert store.get("person-a", stored.file_id) is None
    assert store.remove_expired() == 1 and not stored.path.exists()
    store.close()
