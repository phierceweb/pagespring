"""microsoft_support — match + sitemap/hub acquire + normalize (mocked fetch)."""

import urllib.error

from pagespring import http
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


def test_sustained_403_breaker_stops_cooldowns(tmp_path, monkeypatch):
    """When cooldown-retries keep failing (sustained quota block, not a burst),
    stop paying the cooldown after 3 consecutive failures — skip fast instead
    of stretching the crawl by 30-60s per article."""
    sitemap = (
        "<urlset>"
        + "".join(
            f"<url><loc>https://support.microsoft.com/en-us/excel/a{i}</loc></url>"
            for i in range(6)
        )
        + "</urlset>"
    )
    sleeps: list[float] = []

    def fake_fetch(url, **kwargs):
        if url.endswith("_sitemaps/excel_en-us_1.xml"):
            return url, sitemap
        if "_sitemaps/" in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        raise urllib.error.HTTPError(url, 403, "Forbidden", None, None)

    monkeypatch.setattr(http, "fetch_text", fake_fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda s=0.25: sleeps.append(s))
    p = MicrosoftSupportPattern()

    acq = p.acquire("https://support.microsoft.com/en-us/excel", tmp_path)

    assert acq.pages == 0
    cooldowns = [s for s in sleeps if s >= 30]
    assert len(cooldowns) == 3  # breaker opened after 3 failed retries


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
