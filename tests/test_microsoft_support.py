"""microsoft_support — match + sitemap/hub acquire + normalize (mocked fetch)."""

import urllib.error

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns.microsoft_support import MicrosoftSupportPattern

_GUID = "11111111-1111-1111-1111-111111111111"
_GUID2 = "22222222-2222-2222-2222-222222222222"
_HUB = f"""
<html><body><h1 class="header__title">Excel help &amp; learning</h1>
<a class="ocpArticleLink" href="/en-us/office/create-a-pivottable-{_GUID}">PivotTable</a>
<a class="ocpArticleLink" href="/en-us/office/enter-and-format-data-{_GUID2}">Enter data</a>
</body></html>
"""
_ART1 = """
<html><body>
<nav>site chrome</nav>
<h1>Create a PivotTable</h1>
<div class="learnArticleContent">
  <h2>Build it</h2><p>Insert a PivotTable.</p>
  <img src="https://support.content.office.net/img/pivot.png">
  <div class="feedbackHeader articleExperience">Was this helpful?</div>
</div>
<footer>more chrome</footer>
</body></html>
"""
_ART2 = """
<html><body><h1>Enter and format data</h1>
<div class="learnArticleContent"><h2>Type values</h2><p>Click a cell and type.</p></div>
</body></html>
"""


def _fake_fetch_text(url, **kwargs):
    """Hub-scrape fixture: the per-product sitemap 404s, forcing fallback."""
    if "_sitemaps/" in url:
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
    if url.endswith("/en-us/excel"):
        return url, _HUB
    if "create-a-pivottable" in url:
        return url, _ART1
    return url, _ART2


# Sitemap-mode fixtures: product sitemap lists two real articles and one
# chrome-shell page (no h1, near-empty body) that must be skipped.
_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset><url><loc>https://support.microsoft.com/en-us/excel/create-a-pivottable</loc></url>
<url><loc>https://support.microsoft.com/en-us/excel/enter-and-format-data</loc></url>
<url><loc>https://support.microsoft.com/en-us/excel/4414eaaf-chrome-shell</loc></url></urlset>
"""
_SHELL = """
<html><body>
<div class="learnArticleContent"><div class="row ocpArticleSizingWrapper"></div></div>
</body></html>
"""


def _fake_fetch_sitemap_mode(url, **kwargs):
    if url.endswith("_sitemaps/excel_en-us_1.xml"):
        return url, _SITEMAP
    if "_sitemaps/" in url:  # _2.xml and beyond
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
    if "create-a-pivottable" in url:
        return url, _ART1
    if "chrome-shell" in url:
        return url, _SHELL
    return url, _ART2


def test_403_cools_down_and_retries_article(tmp_path, monkeypatch):
    """support.microsoft.com throttles with 403 (not 429): the first 403 on an
    article triggers a cooldown sleep and ONE retry instead of dropping it."""
    calls: dict[str, int] = {}
    sleeps: list[float] = []

    def fake_fetch(url, **kwargs):
        if url.endswith("_sitemaps/excel_en-us_1.xml"):
            return url, _SITEMAP
        if "_sitemaps/" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        calls[url] = calls.get(url, 0) + 1
        if "create-a-pivottable" in url and calls[url] == 1:
            raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)
        if "create-a-pivottable" in url:
            return url, _ART1
        if "chrome-shell" in url:
            return url, _SHELL
        return url, _ART2

    monkeypatch.setattr(http, "fetch_text", fake_fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda s=0.25: sleeps.append(s))
    p = MicrosoftSupportPattern()

    acq = p.acquire("https://support.microsoft.com/en-us/excel", tmp_path)

    assert acq.pages == 2  # the 403'd article recovered on retry
    assert any(s >= 30 for s in sleeps)  # cooldown actually taken
    html = p.normalize(acq, tmp_path).read_text(encoding="utf-8")
    assert "<h2>Create a PivotTable</h2>" in html


def test_acquire_uses_product_sitemap(tmp_path, monkeypatch):
    """With a per-product sitemap available, articles come from it — and
    chrome-shell pages (no title, trivial body) are skipped."""
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_sitemap_mode)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = MicrosoftSupportPattern()

    acq = p.acquire("https://support.microsoft.com/en-us/excel", tmp_path)
    assert acq.slug == "excel"
    assert acq.pages == 2  # shell skipped

    html = p.normalize(acq, tmp_path).read_text(encoding="utf-8")
    assert "<h2>Create a PivotTable</h2>" in html
    assert "<h2>Enter and format data</h2>" in html
    assert "ocpArticleSizingWrapper" not in html  # the shell never staged


def test_match():
    p = MicrosoftSupportPattern()
    assert p.match("https://support.microsoft.com/en-us/excel")
    assert not p.match("https://learn.microsoft.com/en-us/office")
    assert not p.match("https://example.com/x")


class _LogSpy:
    def __init__(self):
        self.warnings = []

    def warning(self, event, **kw):
        self.warnings.append((event, kw))

    def info(self, *a, **kw):
        pass


def test_article_cap_warns(tmp_path, monkeypatch):
    """Truncating the hub's article list at _MAX is loud, not silent."""
    from pagespring.patterns import microsoft_support as mod

    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_MAX", 1)
    spy = _LogSpy()
    monkeypatch.setattr(mod, "log", spy)

    acq = MicrosoftSupportPattern().acquire("https://support.microsoft.com/en-us/excel", tmp_path)

    assert acq.pages == 1  # second hub article dropped by the cap
    assert any(event == "microsoft_support.capped" for event, _ in spy.warnings)


def test_sitemap_non_404_error_warns(tmp_path, monkeypatch):
    """A non-404 on a mid-pagination sitemap page (e.g. a 403 throttle) truncates
    the catalog — warn loudly instead of breaking silently like the fetch loop does."""
    from pagespring.patterns import microsoft_support as mod

    def fake_fetch(url, **kwargs):
        if url.endswith("_sitemaps/excel_en-us_1.xml"):
            return url, _SITEMAP
        if "_sitemaps/" in url:  # _2.xml throttled — NOT a genuine end-of-pages 404
            raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)
        if "create-a-pivottable" in url:
            return url, _ART1
        if "chrome-shell" in url:
            return url, _SHELL
        return url, _ART2

    monkeypatch.setattr(http, "fetch_text", fake_fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    spy = _LogSpy()
    monkeypatch.setattr(mod, "log", spy)

    acq = MicrosoftSupportPattern().acquire("https://support.microsoft.com/en-us/excel", tmp_path)

    assert acq.pages == 2  # articles from _1.xml still acquired despite the early break
    assert any(event == "microsoft_support.sitemap_error" for event, _ in spy.warnings)


def test_sitemap_404_end_is_silent(tmp_path, monkeypatch):
    """Running off the end of the sitemap pages (the terminal 404) is the normal
    stop — it must NOT warn, or the noise would fire on every sitemap crawl."""
    from pagespring.patterns import microsoft_support as mod

    monkeypatch.setattr(http, "fetch_text", _fake_fetch_sitemap_mode)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    spy = _LogSpy()
    monkeypatch.setattr(mod, "log", spy)

    MicrosoftSupportPattern().acquire("https://support.microsoft.com/en-us/excel", tmp_path)

    assert not any(event == "microsoft_support.sitemap_error" for event, _ in spy.warnings)


def test_a_throttled_sitemap_page_truncates_the_result(tmp_path, monkeypatch):
    """A 403 mid-pagination cuts the catalog: the articles on the pages never
    reached cannot be counted one by one, so truncated has to carry it."""

    def fake_fetch(url, **kwargs):
        if url.endswith("_sitemaps/excel_en-us_1.xml"):
            return url, _SITEMAP
        if "_sitemaps/" in url:  # _2.xml throttled — NOT a genuine end-of-pages 404
            raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)
        if "create-a-pivottable" in url:
            return url, _ART1
        if "chrome-shell" in url:
            return url, _SHELL
        return url, _ART2

    monkeypatch.setattr(http, "fetch_text", fake_fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = MicrosoftSupportPattern().acquire("https://support.microsoft.com/en-us/excel", tmp_path)

    assert acq.truncated is True
    assert acq.pages == 2  # articles from _1.xml still acquired


def test_the_terminal_sitemap_404_does_not_truncate(tmp_path, monkeypatch):
    """Running off the end of the sitemap pages is the normal stop — flagging it
    would mark every healthy sitemap crawl truncated."""
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_sitemap_mode)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = MicrosoftSupportPattern().acquire("https://support.microsoft.com/en-us/excel", tmp_path)

    assert acq.truncated is False


def test_article_without_a_content_div_counts_as_lost(tmp_path, monkeypatch):
    """An article the extractor cannot open was dropped silently — a client-rendered
    or retemplated article is a real loss, not a chrome shell."""
    no_content = (
        "<html><body><h1>Create a PivotTable</h1><p>Rendered client-side.</p></body></html>"
    )

    def fake_fetch(url, **kwargs):
        if url.endswith("_sitemaps/excel_en-us_1.xml"):
            return url, _SITEMAP
        if "_sitemaps/" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        if "create-a-pivottable" in url:
            return url, no_content
        if "chrome-shell" in url:
            return url, _SHELL
        return url, _ART2

    monkeypatch.setattr(http, "fetch_text", fake_fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = MicrosoftSupportPattern().acquire("https://support.microsoft.com/en-us/excel", tmp_path)

    assert acq.pages == 1
    assert acq.lost == 1


def test_title_less_chrome_shell_is_not_counted_as_lost(tmp_path, monkeypatch):
    """The shell HAS a content div, just nothing in it — it is not an article, so
    counting it fires pages_lost on every healthy crawl."""
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_sitemap_mode)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = MicrosoftSupportPattern().acquire("https://support.microsoft.com/en-us/excel", tmp_path)

    assert acq.pages == 2  # the shell was skipped
    assert acq.lost == 0


def test_relative_image_src_is_absolutized(tmp_path, monkeypatch):
    """Articles served with relative media/ image paths (Sway, Publisher, …) must be
    absolutized against the article URL — the deliverable promises absolute assets.
    Already-absolute srcs are left untouched."""
    sitemap = (
        "<urlset><url>"
        "<loc>https://support.microsoft.com/en-us/sway/create-in-sway</loc>"
        "</url></urlset>"
    )
    article = (
        "<html><body><h1>Create in Sway</h1>"
        '<div class="learnArticleContent">'
        "<p>Create a new Sway from scratch or from a document.</p>"
        '<img src="media/welcome.png" data-linktype="relative-path">'
        '<img src="https://support.content.office.net/img/keep.png">'
        "</div></body></html>"
    )

    def fake_fetch(url, **kwargs):
        if url.endswith("_sitemaps/sway_en-us_1.xml"):
            return url, sitemap
        if "_sitemaps/" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return url, article

    monkeypatch.setattr(http, "fetch_text", fake_fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = MicrosoftSupportPattern()

    acq = p.acquire("https://support.microsoft.com/en-us/sway", tmp_path)
    html = p.normalize(acq, tmp_path).read_text(encoding="utf-8")

    assert 'src="https://support.microsoft.com/en-us/sway/media/welcome.png"' in html
    assert 'src="media/welcome.png"' not in html  # no relative ref survives
    assert 'src="https://support.content.office.net/img/keep.png"' in html  # absolute untouched


def test_acquire_extracts_articles(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = MicrosoftSupportPattern()

    acq = p.acquire("https://support.microsoft.com/en-us/excel", tmp_path)
    assert acq.kind == "html"
    assert acq.slug == "excel"
    assert acq.pages == 2
    assert len(list(acq.raw_dir.glob("*.html"))) == 2

    html = p.normalize(acq, tmp_path).read_text(encoding="utf-8")
    assert "<h1>Excel Help</h1>" in html
    assert "<h2>Create a PivotTable</h2>" in html  # article title from <h1>
    assert "Insert a PivotTable." in html  # body content
    assert "Enter and format data" in html  # second article
    assert "support.content.office.net/img/pivot.png" in html  # image ref kept absolute
    assert "site chrome" not in html and "more chrome" not in html  # page chrome excluded
    assert "Was this helpful" not in html  # feedback chrome stripped


def test_a_zero_article_crawl_refuses_to_normalize(tmp_path):
    """A hub whose shape changed — or a crawl the site quota-blocked outright — acquires
    nothing, and a titled HTML shell wrapping zero articles is non-empty, so staging
    would accept it over the previous good deliverable."""
    raw = tmp_path / "raw"
    raw.mkdir()
    acq = AcquireResult(raw_dir=raw, kind="html", slug="windows-help", pages=0)

    with pytest.raises(InvalidInputError, match="no article"):
        MicrosoftSupportPattern().normalize(acq, tmp_path)


def _urlset(*names):
    locs = "".join(
        f"<url><loc>https://support.microsoft.com/en-us/excel/{n}</loc></url>" for n in names
    )
    return f"<urlset>{locs}</urlset>"


def _recorded_crawl(monkeypatch, tmp_path, sitemap, article):
    """Acquire from a one-page sitemap; returns the result and its fetches and sleeps in order."""
    events: list[tuple[str, object]] = []

    def fetch_text(url, **kw):
        events.append(("fetch", url))
        if url.endswith("_sitemaps/excel_en-us_1.xml"):
            return url, sitemap
        if "_sitemaps/" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return url, article(url)

    monkeypatch.setattr(http, "fetch_text", fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda s=0.25: events.append(("sleep", s)))
    acq = MicrosoftSupportPattern().acquire("https://support.microsoft.com/en-us/excel", tmp_path)
    return acq, events


def _forbidden(url):
    raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)


@pytest.mark.parametrize(
    ("page_two", "truncated"),
    [
        ("<html><body>Checking your browser</body></html>", True),
        (
            '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"/>',
            False,
        ),
    ],
)
def test_a_200_sitemap_page_without_loc_ends_the_walk(monkeypatch, page_two, truncated):
    """The site ends pagination with a 404, and an empty <urlset> is a clean end too.
    A WAF interstitial or CDN error page answering 200 hid the rest of the catalog."""
    from pagespring.patterns import microsoft_support as mod

    calls = []

    def fake_fetch(url, **kw):
        calls.append(url)
        return url, _urlset("a") if url.endswith("_1.xml") else page_two

    monkeypatch.setattr(mod.http, "fetch_text", fake_fetch)
    monkeypatch.setattr(mod.http, "polite_sleep", lambda *a, **k: None)

    links, was_truncated = mod._sitemap_articles("excel", "en-us")

    assert links == ["https://support.microsoft.com/en-us/excel/a"]
    assert was_truncated is truncated
    assert len(calls) == 2, f"walked past the page without <loc>: {len(calls)} requests"


def test_every_request_is_paced_whatever_its_outcome(tmp_path, monkeypatch):
    """Sitemap pages, fetch errors, content-less pages and chrome shells are requests too."""
    answers = {"shell": _SHELL, "empty": "<html><body><h1>No body</h1></body></html>"}

    def article(url):
        name = url.rsplit("/", 1)[-1]
        if name == "broken":
            raise urllib.error.URLError("connection reset")
        if name == "gone":
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return answers.get(name, _ART2)

    sitemap = _urlset("broken", "shell", "empty", "good", "gone")
    acq, events = _recorded_crawl(monkeypatch, tmp_path, sitemap, article)

    assert acq.pages == 1
    unpaced = [b for a, b in zip(events, events[1:], strict=False) if a[0] == b[0] == "fetch"]
    assert unpaced == []


def test_a_sustained_block_stops_the_crawl_and_counts_the_rest_lost(tmp_path, monkeypatch):
    """After 3 consecutive failed cooldown-retries and a refused re-check, further
    requests only feed the block."""
    sitemap = _urlset(*(f"a{i}" for i in range(40)))
    acq, events = _recorded_crawl(monkeypatch, tmp_path, sitemap, _forbidden)

    articles = [e for e in events if e[0] == "fetch" and "_sitemaps/" not in str(e[1])]
    assert len(articles) == 7, "three articles fetched and retried once, then one re-check"
    assert events.count(("sleep", 60.0)) == 3
    assert (acq.pages, acq.lost) == (0, 40)


def test_a_direct_success_resets_the_block_breaker(tmp_path, monkeypatch):
    """Failed cooldowns separated by articles that load are bursts, not a sustained block."""

    def article(url):
        return _forbidden(url) if url.rsplit("/", 1)[-1].startswith("b") else _ART2

    sitemap = _urlset(*(f"{kind}{i}" for i in range(4) for kind in ("b", "ok")))
    acq, events = _recorded_crawl(monkeypatch, tmp_path, sitemap, article)

    assert (acq.pages, acq.lost) == (4, 4)
    assert events.count(("sleep", 60.0)) == 4


def test_the_sitemap_page_cap_stops_the_walk_and_reports_truncated(tmp_path, monkeypatch):
    """A product whose sitemap never 404s — more pages than the cap, or an origin
    that answers every _n.xml — must stop at the cap and say so: the articles on
    the pages never requested cannot be counted one by one."""
    from pagespring.patterns import microsoft_support as mod

    requested: list[str] = []

    def fake_fetch(url, **kwargs):
        if "_sitemaps/" in url:
            requested.append(url)
            n = url.rsplit("_", 1)[-1].removesuffix(".xml")
            return url, (
                '<?xml version="1.0" encoding="UTF-8"?><urlset><url><loc>'
                f"https://support.microsoft.com/en-us/excel/enter-and-format-data-{n}"
                "</loc></url></urlset>"
            )
        return url, _ART2

    monkeypatch.setattr(http, "fetch_text", fake_fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_MAX_SITEMAP_PAGES", 3)
    spy = _LogSpy()
    monkeypatch.setattr(mod, "log", spy)

    acq = MicrosoftSupportPattern().acquire("https://support.microsoft.com/en-us/excel", tmp_path)

    assert len(requested) == 3, f"the walk must stop at the cap, requested {len(requested)}"
    assert acq.truncated is True, "a capped walk left articles undiscovered"
    assert acq.pages == 3
    assert any(event == "microsoft_support.sitemap_capped" for event, _ in spy.warnings)


def test_restricted_articles_in_a_row_do_not_stop_the_crawl(tmp_path, monkeypatch):
    """The site also answers 403 for a retired or restricted article; the block is
    confirmed against a request that should load before the crawl is stopped."""

    def article(url):
        return _forbidden(url) if url.rsplit("/", 1)[-1] in {"a0", "a1", "a2"} else _ART2

    sitemap = _urlset(*(f"a{i}" for i in range(20)))
    acq, _events = _recorded_crawl(monkeypatch, tmp_path, sitemap, article)

    assert (acq.pages, acq.lost) == (17, 3)


def test_a_block_that_spares_the_hub_still_stops_the_crawl(tmp_path, monkeypatch):
    """Before any article has loaded, the upcoming article is the re-check: a hub page
    answering 200 says nothing about whether articles are being refused."""
    seed = "https://support.microsoft.com/en-us/excel"

    def article(url):
        return _ART2 if url == seed else _forbidden(url)

    sitemap = _urlset(*(f"a{i}" for i in range(40)))
    acq, events = _recorded_crawl(monkeypatch, tmp_path, sitemap, article)

    articles = [e for e in events if e[0] == "fetch" and "_sitemaps/" not in str(e[1])]
    assert len(articles) == 7
    assert (acq.pages, acq.lost) == (0, 40)
