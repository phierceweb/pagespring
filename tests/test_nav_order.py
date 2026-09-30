"""_nav_order — sidebar order, with omitted pages placed beside their section."""

from pagespring.patterns import _nav_order

_DOCS = "https://docs.example.com/docs"


def test_a_page_the_sidebar_omits_is_placed_with_its_section():
    """A section page the sidebar omits goes before its first listed child; any other
    omitted page goes after the listed pages under its nearest ancestor, nearer
    ancestors first."""
    nav = [_DOCS, f"{_DOCS}/a/one", f"{_DOCS}/a/two", f"{_DOCS}/b/one"]
    sitemap_order = [
        f"{_DOCS}/b/one/",
        f"{_DOCS}/zz/",
        f"{_DOCS}/_a/",
        f"{_DOCS}/b/one/x/",
        f"{_DOCS}/a/one/deep/",
        f"{_DOCS}/a/",
        f"{_DOCS}/a/two/",
        f"{_DOCS}/",
        f"{_DOCS}/a/one/",
        f"{_DOCS}/b/",
    ]

    ordered = _nav_order.reading_order(sitemap_order, nav)

    assert ordered == [
        f"{_DOCS}/",
        f"{_DOCS}/a/",
        f"{_DOCS}/a/one/",
        f"{_DOCS}/a/one/deep/",
        f"{_DOCS}/a/two/",
        f"{_DOCS}/b/",
        f"{_DOCS}/b/one/",
        f"{_DOCS}/b/one/x/",
        f"{_DOCS}/_a/",
        f"{_DOCS}/zz/",
    ]


def test_index_html_and_trailing_slash_spellings_match_the_sidebar():
    nav = [f"{_DOCS}/index.html", f"{_DOCS}/b/index.html", f"{_DOCS}/a/index.html"]
    ordered = _nav_order.reading_order([f"{_DOCS}/a/", f"{_DOCS}/b/", f"{_DOCS}/"], nav)
    assert ordered == [f"{_DOCS}/", f"{_DOCS}/b/", f"{_DOCS}/a/"]
