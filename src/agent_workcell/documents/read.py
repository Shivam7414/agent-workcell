"""Read a file's text as blocks: a paragraph, or a group of table rows, with the section and page it came from.

Each reader goes through the file line by line and never holds the whole file, so a large file needs little memory.
A line is read at most MAX_LINE_CHARS at a time, so even a file with no line breaks is read in parts. The file's
content is untrusted: it only ever becomes data in a block.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePath

MAX_LINE_CHARS = 64_000
MAX_BLOCK_CHARS = 8_000  # a paragraph longer than this is passed on in parts
CSV_BLOCK_CHARS = 1_500  # rows are grouped into blocks of about this size
SECTION_CHARS = 300

MEDIA_TYPES = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".csv": "text/csv",
}
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")


class ReadError(ValueError):
    """The file cannot be read. The message is shown to people and to the model, so it never names a path."""


@dataclass(frozen=True)
class Block:
    text: str
    section: str | None = None
    page: int | None = None


def media_type_for(file_name: str) -> str | None:
    """The media type of a file that can be read, from its name, or None. The name is only looked at, never opened."""
    return MEDIA_TYPES.get(PurePath(file_name).suffix.lower())


def read_blocks(path: Path, media_type: str) -> Iterator[Block]:
    readers = {"text/plain": _plain, "text/markdown": _markdown, "text/csv": _csv}
    if media_type not in readers:
        raise ReadError("This type of file is not supported.")
    with open(path, encoding="utf-8-sig", newline="") as file:
        try:
            yield from readers[media_type](_lines(file))
        except UnicodeDecodeError:
            raise ReadError("The file is not UTF-8 text.") from None


def _lines(file: io.TextIOBase) -> Iterator[str]:
    while line := file.readline(MAX_LINE_CHARS):
        if "\x00" in line:
            raise ReadError("The file holds binary data, not text.")
        yield line


def _paragraphs(lines: Iterator[tuple[str | None, str]]) -> Iterator[Block]:
    """Text between blank lines, joined into one line, with its section. A new section also ends a paragraph, and a
    very long paragraph comes out in parts."""
    parts: list[str] = []
    size = 0
    current: str | None = None
    for section, line in lines:
        # A line read in part is MAX_LINE_CHARS long, more than MAX_BLOCK_CHARS, so the block always ends after it:
        # a word cut by a partial read is cut only where one block ends and the next begins.
        stripped = line.strip()
        if parts and (not stripped or section != current or size >= MAX_BLOCK_CHARS):
            yield Block(" ".join(parts), current)
            parts, size = [], 0
        if stripped:
            current = section
            parts.append(stripped)
            size += len(stripped) + 1
    if parts:
        yield Block(" ".join(parts), current)


def _plain(lines: Iterator[str]) -> Iterator[Block]:
    return _paragraphs((None, line) for line in lines)


def _markdown(lines: Iterator[str]) -> Iterator[Block]:
    """Headings set the section, as the path of heading titles. A '#' inside a fenced code block is not a heading."""

    def with_sections() -> Iterator[tuple[str | None, str]]:
        headings: list[tuple[int, str]] = []
        in_fence = False
        for line in lines:
            if FENCE.match(line):
                in_fence = not in_fence
            heading = None if in_fence else HEADING.match(line)
            if heading:
                level = len(heading.group(1))
                headings = [(lvl, title) for lvl, title in headings if lvl < level] + [(level, heading.group(2))]
                yield None, ""  # a heading ends the paragraph before it
                continue
            yield " > ".join(title for _, title in headings)[:SECTION_CHARS] or None, line

    return _paragraphs(with_sections())


def _csv(lines: Iterator[str]) -> Iterator[Block]:
    """Rows written as "column: value" pairs, grouped into blocks. The section names the rows, counting the header
    as row 1, so an answer can cite them."""

    def whole_lines() -> Iterator[str]:
        for line in lines:
            if len(line) == MAX_LINE_CHARS and not line.endswith(("\n", "\r")):
                raise ReadError(f"A line of the CSV file is longer than {MAX_LINE_CHARS:,} characters.")
            yield line

    rows = csv.reader(whole_lines())
    try:
        header = [name.strip() for name in next(rows)]
    except StopIteration:
        return
    except csv.Error:
        raise ReadError("The file is not valid CSV.") from None
    parts: list[str] = []
    first = last = size = 0
    row_no = 1
    try:
        for row in rows:
            row_no += 1
            if not any(cell.strip() for cell in row):
                continue
            names = header + [f"column {i + 1}" for i in range(len(header), len(row))]
            text = "; ".join(f"{name}: {cell.strip()}" for name, cell in zip(names, row, strict=False) if cell.strip())
            if parts and size + len(text) > CSV_BLOCK_CHARS:
                yield Block("\n".join(parts), _rows(first, last))
                parts, size = [], 0
            if not parts:
                first = row_no
            parts.append(text)
            size += len(text) + 1
            last = row_no
    except csv.Error:
        raise ReadError("The file is not valid CSV.") from None
    if parts:
        yield Block("\n".join(parts), _rows(first, last))


def _rows(first: int, last: int) -> str:
    return f"Row {first}" if first == last else f"Rows {first} to {last}"
