"""_soup — attribute values read as the HTML spec reads them, on every Python."""

import pytest

from pagespring.patterns._soup import hold_attribute_refs, parse_html


@pytest.mark.parametrize(
    "written,read",
    [
        ("?w=1&reg=us", "?w=1&reg=us"),
        ("?w=1&section=2", "?w=1&section=2"),
        ("?a&copy2;=x", "?a&copy2;=x"),
        ("?a&notit;", "?a&notit;"),
        ("?w=1&amp;h=2", "?w=1&h=2"),
        ("&reg;", "®"),
        ("&reg x", "® x"),
        ("&reg;=x", "®=x"),
        ("&#38;&#x26;&#38", "&&&"),
        ("&", "&"),
    ],
)
def test_an_attribute_value_decodes_only_what_the_spec_decodes(written, read):
    for quoted in (f'"{written}"', f"'{written}'"):
        assert parse_html(f"<img src={quoted}>").img["src"] == read


def test_an_unquoted_value_and_a_self_closing_tag():
    assert parse_html("<img src=a.png?w=1&reg=us>").img["src"] == "a.png?w=1&reg=us"
    assert parse_html("<IMG SRC='a?x&reg=1'/>").img["src"] == "a?x&reg=1"


def test_text_and_attribute_names_are_left_alone():
    assert parse_html("<p>&copy 2020 &reg=x</p>").p.get_text() == "© 2020 ®=x"
    assert hold_attribute_refs("<a b&reg=c>x</a>") == "<a b&reg=c>x</a>"


@pytest.mark.parametrize(
    "markup",
    [
        "<script>var u = \"<img src='a?x&reg=1'>\";</script>",
        "<STYLE>a::after { content: \"<b title='&reg='>\" }</style >",
        "<title><img src='a?x&reg=1'></title>",
        "<textarea><img src='a?x&reg=1'></textarea>",
        "<plaintext><img src='a?x&reg=1'>",
        "<!-- <img src='a?x&reg=1'> -->",
        "<![CDATA[<img src='a?x&reg=1'>]]>",
        "<?pi <img src='a?x&reg=1'?>",
        "<p title='x' <img src='a?x&reg=1'",
    ],
)
def test_raw_text_comments_declarations_and_an_unclosed_tag_are_left_alone(markup):
    assert hold_attribute_refs(markup) == markup


@pytest.mark.parametrize(
    "before",
    ["<script src='s.js?a&reg=1'></script>", "<script/>", "<!-->", "<!--x--!>", "</a title='>'>"],
)
def test_a_tag_after_other_markup_still_holds_its_refs(before):
    assert parse_html(f"{before}<img src='b?x&reg=2'>").img["src"] == "b?x&reg=2"


def test_a_value_holding_a_closing_bracket_or_quote_stays_one_value():
    soup = parse_html("<a title='1 > 0' href=\"q?a='&reg=1'\">x</a><img src='c?&reg=3'>")
    assert soup.a["title"] == "1 > 0"
    assert soup.a["href"] == "q?a='&reg=1'"
    assert soup.img["src"] == "c?&reg=3"


def test_unclosed_markup_is_read_in_linear_time():
    """A rescan from each ``<`` is quadratic; at this size it would run for minutes."""
    for markup in ("<a&" * 200_000, "<!--&" * 200_000, "<a b='&reg=' " * 50_000):
        assert hold_attribute_refs(markup) == markup
