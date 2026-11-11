"""The answer as safe HTML.

The model's answer is untrusted text: it can repeat text from the data, and that text can be hostile. A page that
shows an image or a link from it can send data out (the address can carry it), so the answer is cleaned on the
server:

- All text is escaped first. Tags are then added only from a short fixed list, and never with an attribute.
- Images are removed and replaced by a short note.
- Links are shown as plain text: the label, then the address in brackets. Nothing is clickable.
- Raw HTML in the answer is shown as text.

A small part of Markdown is supported: paragraphs, headings, bold, italics, inline code, code blocks, lists and
tables. Anything else is shown as written.
"""

from __future__ import annotations

import html
import re

ALLOWED_TAGS = frozenset({"p", "br", "h3", "h4", "strong", "em", "code", "pre", "ul", "ol", "li", "table", "thead",
                          "tbody", "tr", "th", "td"})
IMAGE_NOTE = "(image removed)"

IMAGE = re.compile(r"!\[([^\]\n]*)\]\(([^)\n]*)\)")
LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s\n]+)(?:\s+&quot;[^\n]*?&quot;)?\)")
AUTOLINK = re.compile(r"&lt;((?:https?|mailto|ftp):[^\s&]+)&gt;", re.IGNORECASE)
CODE = re.compile(r"`([^`\n]+)`")
BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*|__(?=\S)(.+?)(?<=\S)__")
ITALIC = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
NUMBERED = re.compile(r"^\s*\d{1,9}[.)]\s+(.*)$")
TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def inline(text: str) -> str:
    """Escape one line, then add inline formatting. Code spans are kept apart so nothing inside them changes."""
    text = html.escape(text, quote=True)
    spans: list[str] = []

    def keep(match: re.Match) -> str:
        spans.append(f"<code>{match.group(1)}</code>")
        return f"\x00{len(spans) - 1}\x00"

    text = CODE.sub(keep, text)
    text = IMAGE.sub(IMAGE_NOTE, text)
    text = LINK.sub(lambda m: m.group(1) if m.group(1) == m.group(2) else f"{m.group(1)} ({m.group(2)})", text)
    text = AUTOLINK.sub(r"\1", text)
    text = BOLD.sub(lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", text)
    text = ITALIC.sub(r"<em>\1</em>", text)
    return re.sub("\x00(\\d+)\x00", lambda m: spans[int(m.group(1))], text)


def table_cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", line)]


def is_table_row(line: str) -> bool:
    return "|" in line.strip()[1:-1] or line.strip().startswith("|")


def answer_html(markdown: str) -> str:
    lines = markdown.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").split("\n")
    out: list[str] = []
    paragraph: list[str] = []
    index = 0

    def flush() -> None:
        if paragraph:
            out.append("<p>" + "<br>".join(inline(line) for line in paragraph) + "</p>")
            paragraph.clear()

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if stripped.startswith("```"):
            flush()
            index += 1
            code = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code.append(lines[index])
                index += 1
            out.append("<pre><code>" + html.escape("\n".join(code), quote=True) + "</code></pre>")
            index += 1
            continue

        if not stripped:
            flush()
            index += 1
            continue

        heading = HEADING.match(stripped)
        if heading:
            flush()
            tag = "h3" if len(heading.group(1)) <= 2 else "h4"
            out.append(f"<{tag}>{inline(heading.group(2))}</{tag}>")
            index += 1
            continue

        if is_table_row(line) and index + 1 < len(lines) and TABLE_RULE.match(lines[index + 1]):
            flush()
            header = table_cells(line)
            rows = []
            index += 2
            while index < len(lines) and lines[index].strip() and is_table_row(lines[index]):
                rows.append(table_cells(lines[index]))
                index += 1
            head = "".join(f"<th>{inline(cell)}</th>" for cell in header)
            body = "".join("<tr>" + "".join(f"<td>{inline(cell)}</td>" for cell in row) + "</tr>" for row in rows)
            out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
            continue

        for pattern, tag in ((BULLET, "ul"), (NUMBERED, "ol")):
            if pattern.match(line):
                flush()
                items = []
                while index < len(lines) and (item := pattern.match(lines[index])):
                    items.append(f"<li>{inline(item.group(1))}</li>")
                    index += 1
                out.append(f"<{tag}>{''.join(items)}</{tag}>")
                break
        else:
            paragraph.append(stripped)
            index += 1

    flush()
    return "\n".join(out)
