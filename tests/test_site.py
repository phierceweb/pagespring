"""_site — shared docs-site helpers (pure functions, no network)."""

from bs4 import BeautifulSoup

from pagespring.patterns._site import (
    absolutize_refs,
    generator_meta,
    page_title,
    slug_from_host,
)


def test_slug_from_host():
    assert slug_from_host("docs.pytest.org") == "pytest"
    assert slug_from_host("www.mkdocs.org") == "mkdocs"
    assert slug_from_host("docusaurus.io") == "docusaurus"
    assert slug_from_host("manual.audacityteam.org") == "audacityteam"
    assert slug_from_host("squidfunk.github.io") == "squidfunk"


def test_page_title_and_generator_meta():
    html = (
        "<html><head><title> MkDocs </title>"
        '<meta name="generator" content="mkdocs-1.6.1, mkdocs-material-9.5.0">'
        "</head><body></body></html>"
    )
    assert page_title(html) == "MkDocs"
    assert generator_meta(html) == "mkdocs-1.6.1, mkdocs-material-9.5.0"
    assert generator_meta("<html><head></head></html>") == ""


def test_absolutize_refs():
    soup = BeautifulSoup(
        '<article><a href="/guide/x">x</a><img src="img/pic.png">'
        '<a href="https://ex.com/abs">abs</a><a href="#frag">frag</a></article>',
        "html.parser",
    )
    article = soup.find("article")
    absolutize_refs(article, "https://docs.ex.com/section/page/")
    html = str(soup)
    assert 'href="https://docs.ex.com/guide/x"' in html
    assert 'src="https://docs.ex.com/section/page/img/pic.png"' in html
    assert 'href="https://ex.com/abs"' in html  # untouched
    assert 'href="#frag"' in html  # untouched
