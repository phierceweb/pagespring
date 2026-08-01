"""_paligo — Paligo HTML5 publications (mocked fetch).

Two entry points, and only one of them is self-identifying:

- a **topic** page carries ``<meta name="generator" content="Paligo">``
- the **portal** shell (the URL a human lands on) carries no generator meta at
  all, no ``<main>``, and none of the content — just links into ``<locale>/``.

The page index is the search corpus, ``<base>/js/fuzzydata.js``: an
``indexDict`` whose entries carry a ``url`` each. Many entries share a page
(one per anchor), so they dedupe down to the real page set.
"""

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.patterns import _paligo

ROOT = "https://docs.vendor.example/widget5"

_PORTAL = """<html><head><title>Widget 5</title>
<script src="js/html5.fuse.search.js"></script></head>
<body><div class="content-wrapper"><div class="portal-single-publication">
<a href="en/getting-started.html">Getting Started</a>
<a href="en/equalizer.html">Equalizer</a>
</div></div></body></html>"""

_FUZZY = """
indexDict['en'] = [
 {"title":"Getting Started","url":"getting-started.html","body":"..."},
 {"title":"Setup","url":"getting-started.html#setup","body":"..."},
 {"title":"Equalizer","url":"equalizer.html","body":"..."}
];
"""

_TOPIC = """<html><head><meta name="generator" content="Paligo"><title>T</title></head>
<body>
<nav class="site-nav">chrome nav</nav>
<main>
  <article id="search-result-wrapper" class="search-results"></article>
  <article class="topic content-container" id="content-wrapper">
    <h1>Getting Started</h1>
    <p>Insert Widget on a track.</p>
    <img src="../images/ui.png">
  </article>
</main>
<a id="header-navigation-next" href="equalizer.html">Next</a>
</body></html>"""


def _fetch(seen=None, *, fuzzy=_FUZZY, fuzzy_ok=True):
    def fetch(url, **kwargs):
        if seen is not None:
            seen.append(url)
        if url.endswith("fuzzydata.js"):
            if not fuzzy_ok:
                raise OSError("404")
            return url, fuzzy
        if url.endswith("/index.html"):
            return url, _PORTAL
        return url, _TOPIC

    return fetch


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)


def test_portal_shell_is_recognised_even_without_a_generator_meta():
    assert _paligo.is_paligo(_PORTAL) is True
    assert _paligo.is_paligo(_TOPIC) is True  # via the generator meta
    assert _paligo.is_paligo("<html><body>plain</body></html>") is False


def test_publication_base_resolves_from_the_portal_link_locale():
    """The locale dir is whatever the portal links into — not hardcoded 'en'."""
    assert _paligo.publication_base(f"{ROOT}/index.html", _PORTAL) == f"{ROOT}/en"


def test_publication_base_from_a_topic_url_is_its_own_directory():
    assert _paligo.publication_base(f"{ROOT}/de/einrichtung.html", _TOPIC) == f"{ROOT}/de"


def test_dedupes_anchor_entries_to_real_pages(tmp_path, monkeypatch):
    """fuzzydata carries one entry per anchor; #setup is the same page as its parent."""
    seen: list[str] = []
    monkeypatch.setattr(http, "fetch_text", _fetch(seen))

    acq = _paligo.acquire(f"{ROOT}/index.html", tmp_path, slug="widget5", title=None)

    assert acq.pages == 2, "3 index entries collapse to 2 pages"
    assert f"{ROOT}/en/getting-started.html" in seen
    assert f"{ROOT}/en/equalizer.html" in seen
    assert seen[1].endswith("/en/js/fuzzydata.js")


def test_takes_the_content_article_not_the_empty_search_shell(tmp_path, monkeypatch):
    """article#search-result-wrapper sits beside the real one and extracts to ~30
    chars on every page — selecting it silently yields an empty corpus."""
    monkeypatch.setattr(http, "fetch_text", _fetch())

    acq = _paligo.acquire(f"{ROOT}/index.html", tmp_path, slug="widget5", title=None)
    joined = "\n".join(p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html")))

    assert "Insert Widget on a track." in joined
    assert "search-result-wrapper" not in joined
    assert "chrome nav" not in joined


def test_absolutizes_asset_refs(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fetch())

    acq = _paligo.acquire(f"{ROOT}/index.html", tmp_path, slug="widget5", title=None)
    joined = "\n".join(p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html")))

    assert f"{ROOT}/images/ui.png" in joined
    assert "../images" not in joined


def test_missing_index_is_an_input_error(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fetch(fuzzy_ok=False))
    with pytest.raises(InvalidInputError, match="fuzzydata"):
        _paligo.acquire(f"{ROOT}/index.html", tmp_path, slug="widget5", title=None)


def test_index_without_urls_is_an_input_error(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fetch(fuzzy="indexDict['en'] = [];"))
    with pytest.raises(InvalidInputError, match="no pages"):
        _paligo.acquire(f"{ROOT}/index.html", tmp_path, slug="widget5", title=None)
