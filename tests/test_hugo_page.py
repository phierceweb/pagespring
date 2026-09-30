"""_hugo_page — one Hugo page's content fragment, sidebar and kind."""

import pytest
from bs4 import BeautifulSoup

from pagespring.patterns import _hugo_page


def _docsy_page(main):
    return (
        '<html><body class="td-section"><header><nav class="td-navbar"></nav></header>'
        '<div class="container-fluid td-outer"><div class="td-main">'
        '<aside class="td-sidebar"><nav class="td-sidebar-nav"></nav></aside>'
        f"<main>{main}</main></div></div></body></html>"
    )


def test_extract_drops_the_sidebar_nav_drawer():
    """Hugo doc themes render the whole chapter list into every page. It is not
    a <nav>, so the generic chrome selector misses it."""
    html = """<html><body><main>
      <div class="drawer"><div class="scrollable"><div class="wrapper"><div class="toc">
        <a href="/a/">Introduction</a><a href="/b/">Equalizer</a><a href="/c/">Dither</a>
      </div></div></div></div>
      <h1>Equalizer</h1><p>Real documentation.</p>
    </main></body></html>"""
    frag = _hugo_page.extract(BeautifulSoup(html, "html.parser"), "https://d.ex.com/ozone/eq/")
    assert frag is not None
    assert "Real documentation." in frag
    assert "drawer" not in frag
    assert "Introduction" not in frag and "Dither" not in frag


def _extracted(html, url="https://docs.example.com/docs/a/"):
    frag = _hugo_page.extract(BeautifulSoup(html, "html.parser"), url)
    assert frag is not None
    return frag


def test_docsy_chrome_is_stripped_and_authored_examples_stay():
    content = """<div class="td-content">
      <h1>Adding content</h1>
      <div class="lead">Write your pages.</div>
      <h2 id="s">Setup<a class="td-heading-self-link" href="#s" aria-label="Heading self-link"></a></h2>
      <p>Real documentation.</p>
      <blockquote><div class="td-page-meta__lastmod">Example lastmod markup</div></blockquote>
      <div class="section-index"><div class="entry"><h5><a href="/docs/a/b/">Child</a></h5></div></div>
      <div class="d-print-none"><h2 class="feedback--title">Feedback</h2>
        <p class="feedback--question">Was this page helpful?</p></div>
      <div class="td-page-meta__lastmod">Last modified July 1: commit message</div>
      <div class="c-global-meta-links"><div class="td-page-meta">
        <a href="https://github.com/o/r/edit/x.md">Edit this page</a></div></div>
    </div>"""
    frag = _extracted(_docsy_page(main='<nav><ol class="breadcrumb"></ol></nav>' + content))

    assert "Real documentation." in frag and "Write your pages." in frag
    assert "Example lastmod markup" in frag
    assert "td-heading-self-link" not in frag
    assert "section-index" not in frag and "Child" not in frag
    assert "Was this page helpful?" not in frag
    assert "commit message" not in frag
    assert "Edit this page" not in frag


def test_docsy_content_is_read_from_td_content_not_the_main_around_it():
    """A site layout can repeat the title and add a TOC and edit links around td-content."""
    main = """<h1 class="mb-4">Quick start</h1><p class="lead pb-2">Duplicate lead.</p>
      <div class="td-toc td-toc--inline"><a href="#td-content__toc">Contents</a></div>
      <div class="td-content">
        <h1>Quick start</h1><div class="lead">Get started.</div><p>Real documentation.</p>
        <div class="text-muted mt-5 pt-3 border-top">Last modified November 9: commit message</div>
      </div>
      <div class="c-global-meta-links"><div class="td-page-meta">
        <a href="https://github.com/o/r/edit/x.md">Edit this page</a></div></div>"""
    frag = _extracted(_docsy_page(main=main))

    assert frag.startswith('<div class="td-content">')
    assert frag.count("Quick start") == 1
    assert "Real documentation." in frag and "Get started." in frag
    assert "Duplicate lead." not in frag and "Contents" not in frag
    assert "commit message" not in frag
    assert "Edit this page" not in frag


def test_hugo_book_chrome_is_stripped():
    html = """<html><body class="book-kind-page"><main class="container flex">
      <aside class="book-menu"><div class="book-menu-content"><nav><a href="/docs/a/">A</a></nav></div></aside>
      <header class="book-header">menu toggle</header>
      <div class="book-page"><article class="markdown book-article">
        <h1 id="intro">Introduction<a class="anchor" href="#intro"></a></h1>
        <p>Real documentation.</p>
      </article><label class="hidden book-menu-overlay" for="menu-control"></label></div>
      <aside class="book-toc"><div class="book-toc-content">
        <div class="flex book-toc-section"><img class="book-icon" src="/icons/incoming.svg"></div>
      </div></aside>
    </main></body></html>"""
    frag = _extracted(html)

    assert "Real documentation." in frag and "Introduction" in frag
    assert "book-toc" not in frag and "incoming.svg" not in frag
    assert 'class="anchor"' not in frag
    assert "book-menu" not in frag


def test_geekdoc_chrome_is_stripped():
    html = """<html><body><main class="container flex flex-even">
      <aside class="gdoc-nav"><nav><a href="/usage/">Usage</a></nav></aside>
      <div class="gdoc-page">
        <div class="gdoc-page__header">
          <ol class="breadcrumb"><li><a href="/">Welcome to the documentation</a></li></ol>
          <span class="editpage"><a href="https://github.com/o/r/edit/x.md">Edit page</a></span>
        </div>
        <article class="gdoc-markdown" id="main-content">
          <div class="gdoc-page__anchorwrap"><h2 id="s">Setup</h2>
            <a class="gdoc-page__anchor clip" href="#s" title="Anchor to: Setup"></a></div>
          <p>Real documentation.</p>
        </article>
        <div class="gdoc-page__footer"><span class="gdoc-page__nav">
          <a class="gdoc-page__nav--next" href="/b/">Next topic</a></span></div>
      </div>
    </main></body></html>"""
    frag = _extracted(html)

    assert "Real documentation." in frag and "Setup" in frag
    assert "Welcome to the documentation" not in frag
    assert "Edit page" not in frag
    assert "Next topic" not in frag
    assert "Anchor to" not in frag
    assert "gdoc-nav" not in frag


def test_a_geekdoc_post_keeps_its_title_and_drops_its_meta_line():
    html = """<html><body><main class="container flex flex-even"><div class="gdoc-page">
      <article class="gdoc-post" id="main-content">
        <header class="gdoc-post__header"><h1 class="gdoc-post__title">Hello Geekdoc</h1>
          <div class="gdoc-post__meta"><span class="gdoc-post__tag">One minute to read</span></div>
        </header>
        <section class="gdoc-markdown"><p>Real documentation.</p></section>
      </article>
    </div></main></body></html>"""
    frag = _extracted(html)

    assert "Hello Geekdoc" in frag and "Real documentation." in frag
    assert "One minute to read" not in frag


@pytest.mark.parametrize(
    ("fragment", "expected"),
    [
        (
            '<main><div class="book-page"><article class="book-article">\n</article></div></main>',
            True,
        ),
        ("<main><article><h1>Usage</h1></article></main>", False),
        ('<main><p><img src="https://d.ex.com/diagram.png"></p></main>', False),
        ("<main><svg><path d='M0 0'></path></svg></main>", False),
    ],
    ids=["empty-article", "title-only", "image-only", "svg-only"],
)
def test_is_empty_means_no_text_and_no_media(fragment, expected):
    assert _hugo_page.is_empty(fragment) is expected


_URL = "https://d.ex.com/posts/"


def _main(inner):
    return f'<main><div id="main-content">{inner}</div></main>'


def _entry(href, title="Hello", heading="h1"):
    return (
        f'<article class="gdoc-post"><header><{heading}><a href="{href}">{title}</a></{heading}>'
        f"</header><section><p>An excerpt.</p></section></article>"
    )


def test_listed_pages_reads_the_entries_of_a_geekdoc_list():
    fragment = _main(_entry("/posts/a/") + _entry("/posts/b/"))
    assert _hugo_page.listed_pages(fragment, _URL) == {
        "https://d.ex.com/posts/a",
        "https://d.ex.com/posts/b",
    }


def test_listed_pages_reads_a_hugo_book_post_list():
    fragment = (
        '<main><div class="book-page"><article class="book-post markdown">'
        '<h2><a href="https://d.ex.com/posts/example-post/">Example</a></h2>'
        '<div class="book-post-container"><img src="https://d.ex.com/thumb.svg">'
        "<p>An excerpt.</p></div></article></div></main>"
    )
    assert _hugo_page.listed_pages(fragment, _URL) == {"https://d.ex.com/posts/example-post"}


@pytest.mark.parametrize(
    "fragment",
    [
        _main('<article class="gdoc-post"><header><h1>Hello</h1></header><p>Body.</p></article>'),
        _main("<h1>Posts</h1>" + _entry("/posts/a/")),
        _main(_entry("/posts/a/") + "<article><h2>Setup</h2><p>Real documentation.</p></article>"),
        _main(_entry("#hello")),
        _main('<article><h1><a href="/posts/a/">Hello</a> and more</h1><p>Body.</p></article>'),
        _main('<article><p>Intro.</p><h2><a href="/posts/a/">Hello</a></h2></article>'),
        _main("<p>No articles.</p>"),
    ],
    ids=[
        "single-post",
        "own-title",
        "content-article",
        "self-anchor",
        "heading-beyond-link",
        "text-before-heading",
        "no-articles",
    ],
)
def test_listed_pages_is_empty_for_a_page_with_content_of_its_own(fragment):
    assert _hugo_page.listed_pages(fragment, _URL) == set()
