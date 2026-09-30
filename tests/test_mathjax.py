"""MathJax SVG output is rebuilt as MathML."""

from bs4 import BeautifulSoup

from pagespring.patterns import _mathjax, _readable


def _glyph(code: str) -> str:
    return f'<path data-c="{code}" d="M0 0"></path>'


def _node(kind: str, inner: str) -> str:
    return f'<g data-mml-node="{kind}" transform="translate(0,0)">{inner}</g>'


# x^2 + y^2, glyphs in Mathematical Italic as MathJax draws them.
_INLINE = (
    '<mjx-container class="MathJax" jax="SVG"><svg role="img"><defs></defs>'
    '<g stroke="currentColor"><g data-mml-node="math">'
    + _node("msup", _node("mi", _glyph("1D465")) + _node("mn", _glyph("32")))
    + _node("mo", _glyph("2B"))
    + _node("msup", _node("mi", _glyph("1D466")) + _node("mn", _glyph("32")))
    + "</g></g></svg></mjx-container>"
)

# sqrt(a)/2 labelled (1), laid out as MathJax draws it: the surd is an <mo> after the
# radicand, and the label follows its row in the table.
_DISPLAY = (
    '<mjx-container class="MathJax" jax="SVG" display="true"><svg role="img"><g>'
    + _node(
        "math",
        _node(
            "mtable",
            _node(
                "mlabeledtr",
                _node(
                    "mtd",
                    _node(
                        "mfrac",
                        _node("msqrt", _node("mi", _glyph("1D44E")) + _node("mo", _glyph("221A")))
                        + _node("mn", _glyph("32"))
                        + '<rect width="10" height="1"></rect>',
                    ),
                ),
            )
            + "<g>"
            + _node("mtd", _node("mtext", '<text data-variant="normal">(1)</text>'))
            + "</g>",
        ),
    )
    + "</g></svg></mjx-container>"
)


def _rebuilt(html: str) -> str:
    soup = BeautifulSoup(f"<div>{html}</div>", "html.parser")
    _mathjax.rebuild_math(soup)
    return str(soup.div)


def test_an_svg_formula_becomes_mathml_text():
    assert _rebuilt(_INLINE) == (
        "<div><math><msup><mi>x</mi><mn>2</mn></msup><mo>+</mo>"
        "<msup><mi>y</mi><mn>2</mn></msup></math></div>"
    )


def test_the_surd_goes_and_the_label_leads_its_row():
    assert _rebuilt(_DISPLAY) == (
        '<div><math display="block"><mtable><mlabeledtr><mtd><mtext>(1)</mtext></mtd>'
        "<mtd><mfrac><msqrt><mi>a</mi></msqrt><mn>2</mn></mfrac></mtd>"
        "</mlabeledtr></mtable></math></div>"
    )


def test_assistive_mathml_is_kept_as_published():
    html = (
        '<mjx-container jax="SVG" display="true"><svg aria-hidden="true"><g>'
        + _node("math", _node("mi", _glyph("1D465")))
        + '</g></svg><mjx-assistive-mml><math display="block"><mi>z</mi></math>'
        "</mjx-assistive-mml></mjx-container>"
    )
    assert _rebuilt(html) == '<div><math display="block"><mi>z</mi></math></div>'


def test_a_container_without_mathml_markup_is_left_alone():
    html = '<mjx-container jax="SVG"><svg><path d="M0 0"></path></svg></mjx-container>'
    assert _rebuilt(html) == f"<div>{html}</div>"


def test_readable_extraction_keeps_formulas():
    soup = BeautifulSoup(f"<article><p>Pythagoras: {_INLINE}</p></article>", "html.parser")
    _readable.clean(soup.article, "https://x.com/page")
    assert "<math><msup><mi>x</mi>" in str(soup.article)
    assert "<svg" not in str(soup.article)


def test_only_mathml_element_names_are_rebuilt():
    """A group's name is page data: anything but a MathML element becomes an mrow."""
    html = (
        '<mjx-container jax="SVG"><svg><g data-mml-node="math">'
        + _node("img src=x onerror=alert(1)", _node("mi", _glyph("61")))
        + _node("script", _node("mn", _glyph("32")))
        + _node("TeXAtom", _node("mo", _glyph("2B")))
        + "</g></svg></mjx-container>"
    )
    assert _rebuilt(html) == (
        "<div><math><mrow><mi>a</mi></mrow><mrow><mn>2</mn></mrow>"
        "<mrow><mo>+</mo></mrow></math></div>"
    )
