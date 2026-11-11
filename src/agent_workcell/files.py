"""Files the server builds from query results: CSV and Excel (.xlsx).

The model never types the data into a file. It sends a query; the server runs it through the person's guarded
connection and writes the rows. Every value from the database is untrusted, so:

- Excel: text is always written as a text cell (an inline string). The writer never writes a formula element, so no
  value can run as a formula. Numbers are number cells and dates are date cells.
- CSV has no cell types. A spreadsheet program reads a cell that starts with = + - @ (or a tab or carriage return)
  as a formula, so such text gets a leading apostrophe. Numbers from the database are written as they are.

Only the standard library is used. An .xlsx file is a zip of a few XML files, and this writer needs only a small part
of the format.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import math
import re
import secrets
import sqlite3
import threading
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

FORMATS = ("csv", "xlsx")
MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
# Characters that make a spreadsheet program read a CSV cell as a formula. The full-width forms are included because
# some programs fold them into the ASCII ones.
FORMULA_START = ("=", "+", "-", "@", "\t", "\r", "\n", "＝", "＋", "－", "＠")
EXCEL_MAX_CELL = 32_767
EXCEL_MAX_DIGITS = 15  # Excel keeps 15 significant digits; longer numbers are written as text so they stay exact
# XML 1.0 cannot hold most control characters, even escaped.
XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
EXCEL_EPOCH = dt.date(1899, 12, 30)


def is_number(value: object) -> bool:
    return isinstance(value, int | float | Decimal) and not isinstance(value, bool)


def as_text(value: object) -> str:
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    return str(value)


# CSV

def csv_cell(value: object) -> str:
    if value is None:
        return ""
    if is_number(value):
        return str(value)
    text = as_text(value)
    return "'" + text if text.startswith(FORMULA_START) else text


def write_csv(columns: list[str], rows: list[tuple]) -> bytes:
    """UTF-8 with a byte order mark, so spreadsheet programs read the characters correctly."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow([csv_cell(name) for name in columns])
    writer.writerows([csv_cell(value) for value in row] for row in rows)
    return ("﻿" + buffer.getvalue()).encode("utf-8")


# Excel

def column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def xml_text(value: str) -> str:
    return escape(XML_ILLEGAL.sub("�", value[:EXCEL_MAX_CELL]))


def xlsx_cell(ref: str, value: object, header: bool = False) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return f'<c r="{ref}" t="b"><v>{int(value)}</v></c>'
    if is_number(value):
        finite = value.is_finite() if isinstance(value, Decimal) else not isinstance(value, float) or \
            math.isfinite(value)
        exact = isinstance(value, int | Decimal)  # ids and money must not be rounded; a float already is
        digits = len(re.sub(r"\D", "", format(value, "f")).lstrip("0")) if exact and finite else 0
        if finite and digits <= EXCEL_MAX_DIGITS:
            return f'<c r="{ref}"><v>{value}</v></c>'
    elif isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return f'<c r="{ref}" s="2"><v>{(value - EXCEL_EPOCH).days}</v></c>'
    style = ' s="1"' if header else ""
    return (f'<c r="{ref}" t="inlineStr"{style}><is><t xml:space="preserve">{xml_text(as_text(value))}</t></is>'
            "</c>")


SHEET = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
         '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
         '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft"'
         ' state="frozen"/></sheetView></sheetViews><sheetData>{rows}</sheetData></worksheet>')
STATIC_PARTS = {
    "[Content_Types].xml":
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml"'
        ' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml"'
        ' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml"'
        ' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>',
    "_rels/.rels":
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="xl/workbook.xml"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"/>'
        '</Relationships>',
    "xl/workbook.xml":
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets></workbook>',
    "xl/_rels/workbook.xml.rels":
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="worksheets/sheet1.xml"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
        '<Relationship Id="rId2" Target="styles.xml"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"/>'
        '</Relationships>',
    # Styles: 0 normal, 1 bold (the header row), 2 a date as yyyy-mm-dd.
    "xl/styles.xml":
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy-mm-dd"/></numFmts>'
        '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
        '<fills count="2"><fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill></fills>'
        '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="3"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
        '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '</styleSheet>',
}


def write_xlsx(columns: list[str], rows: list[tuple]) -> bytes:
    lines = []
    for number, values in enumerate([tuple(columns), *rows], start=1):
        cells = "".join(xlsx_cell(f"{column_letter(index)}{number}", value, header=number == 1)
                        for index, value in enumerate(values))
        lines.append(f'<row r="{number}">{cells}</row>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in STATIC_PARTS.items():
            archive.writestr(name, content)
        archive.writestr("xl/worksheets/sheet1.xml", SHEET.format(rows="".join(lines)))
    return buffer.getvalue()


def write(file_format: str, columns: list[str], rows: list[tuple]) -> bytes:
    if file_format == "csv":
        return write_csv(columns, rows)
    if file_format == "xlsx":
        return write_xlsx(columns, rows)
    raise ValueError(f"unknown file format: {file_format}")


def file_name(title: str, file_format: str) -> str:
    """A safe file name from the model's title: letters, digits, spaces, dots, dashes and underscores only."""
    stem = re.sub(r"[^A-Za-z0-9 ._-]", "", title).strip(" .")[:60].strip(" .") or "data"
    return f"{stem}.{file_format}"


# Storage

STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    file_id TEXT PRIMARY KEY,
    person TEXT NOT NULL,
    run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    format TEXT NOT NULL,
    rows INTEGER NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class StoredFile:
    file_id: str
    name: str
    format: str
    rows: int
    size: int
    path: Path

    @property
    def media_type(self) -> str:
        return MEDIA_TYPES[self.format]


class FileStore:
    """Built files on disk, each owned by one person (memory.person_key) and kept for a limited time.

    The file id is random and is not the file's only protection: a file is handed out only to the person who owns it.
    """

    def __init__(self, folder: Path, keep_hours: float):
        folder.mkdir(parents=True, exist_ok=True)
        self.folder = folder
        self.keep = dt.timedelta(hours=keep_hours)
        self._conn = sqlite3.connect(folder / "index.sqlite3", check_same_thread=False)
        self._conn.executescript(STORE_SCHEMA)
        self._lock = threading.Lock()

    def _path(self, file_id: str, file_format: str) -> Path:
        return self.folder / f"{file_id}.{file_format}"

    def save(self, person: str, run_id: str, name: str, file_format: str, rows: int, content: bytes) -> StoredFile:
        self.remove_expired()
        file_id = secrets.token_urlsafe(18)
        path = self._path(file_id, file_format)
        path.write_bytes(content)
        created = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               [file_id, person, run_id, name, file_format, rows, len(content), created])
        return StoredFile(file_id, name, file_format, rows, len(content), path)

    def get(self, person: str, file_id: str) -> StoredFile | None:
        """The file, if it exists, has not expired and belongs to this person. Otherwise None."""
        oldest = (dt.datetime.now(dt.UTC) - self.keep).isoformat(timespec="seconds")
        with self._lock:
            row = self._conn.execute(
                "SELECT file_id, name, format, rows, size FROM files WHERE file_id = ? AND person = ?"
                " AND created_at >= ?", [file_id, person, oldest]).fetchone()
        if row is None:
            return None
        stored = StoredFile(*row, path=self._path(row[0], row[2]))
        return stored if stored.path.is_file() else None

    def remove_expired(self) -> int:
        oldest = (dt.datetime.now(dt.UTC) - self.keep).isoformat(timespec="seconds")
        with self._lock, self._conn:
            expired = self._conn.execute("SELECT file_id, format FROM files WHERE created_at < ?",
                                         [oldest]).fetchall()
            self._conn.execute("DELETE FROM files WHERE created_at < ?", [oldest])
        for file_id, file_format in expired:
            self._path(file_id, file_format).unlink(missing_ok=True)
        return len(expired)

    def close(self) -> None:
        self._conn.close()
