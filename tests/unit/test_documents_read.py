"""Reading files into blocks, and blocks into passages. Plain code, no database, no model."""

from pathlib import Path

import pytest

from agent_workcell.documents import read
from agent_workcell.documents.chunk import Chunk, chunks
from agent_workcell.documents.read import Block, ReadError, media_type_for, read_blocks
from reference_erp import seed


def write(tmp_path: Path, name: str, content: str | bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(content.encode("utf-8") if isinstance(content, str) else content)
    return path


def test_the_media_type_comes_from_the_file_name_and_unknown_types_are_refused():
    assert media_type_for("Terms.MD") == "text/markdown"
    assert media_type_for("rows.csv") == "text/csv"
    assert media_type_for("notes.txt") == "text/plain"
    assert media_type_for("report.pdf") is None
    assert media_type_for("../../etc/passwd") is None
    assert media_type_for("no-extension") is None


def test_plain_text_is_split_into_paragraphs_at_blank_lines(tmp_path):
    path = write(tmp_path, "a.txt", "First line\nstill first.\n\n\nSecond paragraph.\n")
    assert list(read_blocks(path, "text/plain")) == [Block("First line still first."), Block("Second paragraph.")]


def test_markdown_headings_become_the_section_path_and_end_a_paragraph(tmp_path):
    text = ("Intro text.\n# Terms\nTop of terms.\n## Payment\nInvoices are due in 30 days.\n"
            "## Delivery\nWithin 10 days.\n# Ending\nNinety days' notice.\n")
    blocks = list(read_blocks(write(tmp_path, "a.md", text), "text/markdown"))
    assert blocks == [
        Block("Intro text."),
        Block("Top of terms.", "Terms"),
        Block("Invoices are due in 30 days.", "Terms > Payment"),
        Block("Within 10 days.", "Terms > Delivery"),
        Block("Ninety days' notice.", "Ending"),
    ]


def test_a_hash_inside_a_code_block_is_not_a_heading(tmp_path):
    text = "# Setup\n```\n# not a heading\n```\n"
    blocks = list(read_blocks(write(tmp_path, "a.md", text), "text/markdown"))
    assert {block.section for block in blocks} == {"Setup"}
    assert "# not a heading" in blocks[0].text


def test_csv_rows_carry_their_column_names_and_row_numbers(tmp_path):
    text = "sku,name,price\nP-1,Bolt,0.20\n\nP-2,\"Nut, small\",0.10\n"
    blocks = list(read_blocks(write(tmp_path, "a.csv", text), "text/csv"))
    assert blocks == [Block("sku: P-1; name: Bolt; price: 0.20\nsku: P-2; name: Nut, small; price: 0.10",
                            "Rows 2 to 4")]


def test_many_csv_rows_are_grouped_into_several_blocks_with_exact_row_ranges(tmp_path):
    text = "n,text\n" + "".join(f"{i},{'x' * 100}\n" for i in range(2, 102))
    blocks = list(read_blocks(write(tmp_path, "a.csv", text), "text/csv"))
    assert len(blocks) > 1
    assert blocks[0].section.startswith("Rows 2 to ")
    assert blocks[-1].section.endswith(" to 101")
    assert sum(block.text.count("\n") + 1 for block in blocks) == 100


def test_a_byte_order_mark_is_not_part_of_the_text(tmp_path):
    path = write(tmp_path, "a.txt", "﻿Hello.")
    assert list(read_blocks(path, "text/plain")) == [Block("Hello.")]


@pytest.mark.parametrize(("name", "content", "media_type", "message"), [
    ("a.txt", b"caf\xe9 au lait", "text/plain", "not UTF-8"),
    ("a.txt", b"text\x00with a zero byte", "text/plain", "binary data"),
    ("a.pdf", b"%PDF-1.7", "application/pdf", "not supported"),
    ("a.csv", ("a,b\n" + "x" * (read.MAX_LINE_CHARS + 10) + "\n").encode(), "text/csv", "longer than"),
])
def test_a_file_that_cannot_be_read_gives_a_message_without_a_path(tmp_path, name, content, media_type, message):
    path = write(tmp_path, name, content)
    with pytest.raises(ReadError) as refused:
        list(read_blocks(path, media_type))
    assert message in str(refused.value)
    assert str(tmp_path) not in str(refused.value)


def test_a_file_with_no_line_breaks_is_read_in_parts_without_breaking_words_inside_a_block(tmp_path):
    # 7 characters per word, so the 64,000-character reads end in the middle of a word.
    path = write(tmp_path, "a.txt", "abcdef " * 50_000)
    blocks = list(read_blocks(path, "text/plain"))
    assert len(blocks) > 1
    assert max(len(block.text) for block in blocks) <= read.MAX_BLOCK_CHARS + read.MAX_LINE_CHARS
    for block in blocks:
        # A word may be cut only where one block ends and the next begins.
        assert set(block.text.split()[1:-1]) == {"abcdef"}
    assert "".join(block.text.replace(" ", "") for block in blocks) == "abcdef" * 50_000


def test_hostile_text_in_a_file_stays_plain_text(tmp_path):
    path = write(tmp_path, "a.md", "# Remarks\n\n" + "\n\n".join(seed.HOSTILE_NOTES))
    blocks = list(read_blocks(path, "text/markdown"))
    assert [block.text for block in blocks] == seed.HOSTILE_NOTES


def test_passages_join_paragraphs_of_one_section_up_to_the_limit():
    blocks = [Block("a" * 40, "S1"), Block("b" * 40, "S1"), Block("c" * 40, "S1"), Block("d" * 10, "S2")]
    assert list(chunks(blocks, max_chars=90)) == [
        Chunk("a" * 40 + "\n" + "b" * 40, "S1", None),
        Chunk("c" * 40, "S1", None),
        Chunk("d" * 10, "S2", None),
    ]


def test_a_passage_never_spans_two_pages():
    blocks = [Block("one", "S", 1), Block("two", "S", 2)]
    assert [chunk.page for chunk in chunks(blocks)] == [1, 2]


def test_a_long_paragraph_is_split_at_a_sentence_end_and_loses_no_text():
    sentence = "The supplier delivers within ten working days. "
    text = sentence * 100
    passages = list(chunks([Block(text)], max_chars=500))
    assert all(len(passage.content) <= 500 for passage in passages)
    assert all(passage.content.endswith(".") for passage in passages)
    assert " ".join(passage.content for passage in passages).split() == text.split()


def test_text_with_no_spaces_is_cut_at_the_limit():
    passages = list(chunks([Block("x" * 1_250)], max_chars=500))
    assert [len(passage.content) for passage in passages] == [500, 500, 250]


def test_the_same_blocks_always_give_the_same_passages():
    blocks = [Block(f"Paragraph {i}. " * 30, f"S{i % 3}") for i in range(20)]
    assert list(chunks(blocks)) == list(chunks(blocks))
