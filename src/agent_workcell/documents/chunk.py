"""Blocks into passages ("chunks") for the document_chunks table.

Consecutive blocks with the same section and page are joined until a passage would pass MAX_CHARS. A block longer
than that is split at the last sentence end, or else the last space, before the limit. A passage never spans two
sections or two pages, so its citation is exact. Plain code, no model call: the same file always gives the same
passages.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from agent_workcell.documents.read import Block

MAX_CHARS = 1_500


@dataclass(frozen=True)
class Chunk:
    content: str
    section: str | None
    page: int | None


def chunks(blocks: Iterable[Block], max_chars: int = MAX_CHARS) -> Iterator[Chunk]:
    parts: list[str] = []
    size = 0
    where: tuple[str | None, int | None] = (None, None)
    for block in blocks:
        here = (block.section, block.page)
        for piece in _split(block.text, max_chars):
            if parts and (here != where or size + 1 + len(piece) > max_chars):
                yield Chunk("\n".join(parts), *where)
                parts, size = [], 0
            if not parts:
                where = here
                size = len(piece)
            else:
                size += 1 + len(piece)
            parts.append(piece)
    if parts:
        yield Chunk("\n".join(parts), *where)


def _split(text: str, max_chars: int) -> Iterator[str]:
    text = text.strip()
    while len(text) > max_chars:
        window = text[:max_chars]
        cut = max(window.rfind(". "), window.rfind("? "), window.rfind("! "))
        if cut >= max_chars // 2:
            cut += 1  # keep the full stop with its sentence
        else:
            cut = window.rfind(" ")
            if cut < max_chars // 2:
                cut = max_chars  # no good place: cut at the limit
        yield text[:cut].strip()
        text = text[cut:].strip()
    if text:
        yield text
