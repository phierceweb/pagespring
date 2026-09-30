"""_jsx — where JSX tags and expressions end inside MDX source."""

import pytest

from pagespring.patterns._jsx import clean_html_tag, prop_content, skip_expression, skip_tag


@pytest.mark.parametrize(
    "expr",
    [
        '{call("}")}',
        "{`a ${ {b: 1}.b } c`}",
        "{<>Don't {x} it</>}",
        "{<>See https://example.com/a</>}",
        "{/* a } in a comment */}",
        "{items.map(i => i) // trailing }\n}",
    ],
)
def test_an_expression_ends_at_its_balancing_brace(expr):
    src = expr + " after"

    assert skip_expression(src, 0) == len(expr)


@pytest.mark.parametrize("src", ["{ never closed", "{`open template}", "{/* open comment }"])
def test_an_expression_that_never_closes_has_no_end(src):
    assert skip_expression(src, 0) is None


def test_a_tag_ends_past_brackets_inside_strings_and_expressions():
    tag = '<Image alt="a > b" rows={[["<n>", "x"]]} />'

    assert skip_tag(tag + " text", len("<Image")) == len(tag)


def test_a_second_tag_opening_means_the_first_was_not_one():
    assert skip_tag("<Foo and <b>bold</b>", len("<Foo")) is None


def test_spread_attributes_are_dropped_from_html():
    assert clean_html_tag('<img {...props} src="/a.png" />') == '<img src="/a.png" />'


def test_a_template_with_holes_is_not_a_code_sample():
    assert prop_content("<CodeBlock example={html`<p>${name}</p>`} />") == ""


@pytest.mark.parametrize(
    "prop", ["{[]}", "{[1, 2]}", '{[["a", 1]]}', '{["a", "b"]}', "{[[x, y]]}", '{{"a": "b"}}']
)
def test_only_rows_of_strings_make_a_table(prop):
    assert prop_content(f"<ApiTable rows={prop} />") == ""
