"""apple_help — acquire (mocked fetch) + normalize (fixture)."""

from pathlib import Path

import pytest

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns.apple_help import AppleHelpPattern, _parse_apple_url

FIXTURE = Path(__file__).parent / "fixtures" / "apple_help" / "numbers"


@pytest.mark.parametrize(
    "url, slug, platform",
    [
        ("https://support.apple.com/guide/numbers/welcome/mac", "numbers", "mac"),
        ("https://support.apple.com/guide/imovie/welcome/macos", "imovie", "macos"),
    ],
)
def test_parse_apple_url(url, slug, platform):
    assert _parse_apple_url(url) == (slug, platform)


def test_acquire_crawls_and_saves(tmp_path, monkeypatch):
    """acquire BFS-crawls via the mocked fetcher and saves a file per page."""
    welcome_url = "https://support.apple.com/guide/numbers/welcome/mac"
    welcome_html = (
        '<html><body><a href="/guide/numbers/whats-new-xyz/14.0/mac/14.0">x</a></body></html>'
    )

    def fake_fetch_text(url, **kwargs):
        if "whats-new-xyz" in url:
            return url, "<html><body>topic body, no further links</body></html>"
        return url, welcome_html

    monkeypatch.setattr(http, "fetch_text", fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = AppleHelpPattern().acquire(welcome_url, tmp_path)

    assert acq.slug == "numbers"
    assert acq.kind == "html"
    assert acq.pages == 2  # welcome + the one linked topic
    names = sorted(p.name for p in acq.raw_dir.glob("*.html"))
    assert "welcome.html" in names
    assert "whats-new-xyz.html" in names


class _LogSpy:
    def __init__(self):
        self.warnings = []

    def warning(self, event, **kw):
        self.warnings.append((event, kw))

    def info(self, *a, **kw):
        pass


def test_crawl_cap_warns(tmp_path, monkeypatch):
    """Hitting _MAX_PAGES with pages still queued is loud, not silent."""
    from pagespring.patterns import apple_help as mod

    welcome = (
        '<html><body><a href="/guide/numbers/topic-a/14.0/mac/14.0">a</a>'
        '<a href="/guide/numbers/topic-b/14.0/mac/14.0">b</a></body></html>'
    )
    monkeypatch.setattr(http, "fetch_text", lambda u, **k: (u, welcome))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_MAX_PAGES", 1)
    spy = _LogSpy()
    monkeypatch.setattr(mod, "log", spy)

    acq = AppleHelpPattern().acquire(
        "https://support.apple.com/guide/numbers/welcome/mac", tmp_path
    )

    assert acq.pages == 1  # capped
    assert any(event == "apple_help.capped" for event, _ in spy.warnings)


def test_normalize_merges_fixture(tmp_path):
    """normalize strips chrome, sets TOC-depth heading levels, keeps images absolute."""
    acq = AcquireResult(raw_dir=FIXTURE, kind="html", slug="numbers")
    out = AppleHelpPattern().normalize(acq, tmp_path)
    html = out.read_text(encoding="utf-8")

    # App title is H1; group + topics get heading levels from TOC depth.
    assert "<h1>Numbers User Guide</h1>" in html
    assert "<h2>Whats new in Numbers</h2>" in html  # topic h1 -> h2 (depth 0)
    assert "<h2>Create a spreadsheet</h2>" in html  # TOC group at depth 0
    assert "<h3>Intro to tables</h3>" in html  # nested topic h1 -> h3
    assert "<h4>Add a table</h4>" in html  # nested topic's h2 -> h4

    # Chrome stripped; image kept absolute; topic icon dropped; See-also listified.
    assert "Global navigation" not in html
    assert "Was this helpful" not in html
    assert "https://support.apple.com/img/new.png" in html
    assert "ICONALT" not in html
    assert "<ul>" in html
    assert "See also A" in html and "See also B" in html


def _two_page_fetch():
    """welcome + one linked topic — enough for a cap of 1 to bite."""
    welcome = '<html><body><a href="/guide/numbers/whats-new-xyz/14.0/mac/14.0">x</a></body></html>'

    def fetch(url, **kwargs):
        if "whats-new-xyz" in url:
            return url, "<html><body>topic</body></html>"
        return url, welcome

    return fetch


def test_capped_crawl_marks_the_result_truncated(tmp_path, monkeypatch):
    """A cap that silently truncates is the Logic Pro failure: 1500 of 3935 pages
    staged, and it passed every check because the guide had grown since the last
    version. The cap must travel with the result, not just a log line."""
    from pagespring.patterns import apple_help as mod

    monkeypatch.setattr(http, "fetch_text", _two_page_fetch())
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_MAX_PAGES", 1)

    acq = AppleHelpPattern().acquire(
        "https://support.apple.com/guide/numbers/welcome/mac", tmp_path
    )

    assert acq.truncated is True


def test_uncapped_crawl_is_not_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _two_page_fetch())
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = AppleHelpPattern().acquire(
        "https://support.apple.com/guide/numbers/welcome/mac", tmp_path
    )

    assert acq.truncated is False


def test_same_topic_under_short_and_long_url_is_fetched_once(tmp_path, monkeypatch):
    """Apple links each topic BOTH as /<slug>-<token>/ and bare /<token>/. Both
    resolve to the same page, so deduping on the raw path segment queues it twice.

    Measured on the real Logic Pro guide: 3935 discovered ids were 1972 real
    topics plus 1963 short-form duplicates — half of every crawl was re-fetching
    pages already on disk, writing nothing. A watchdog counting saved files reads
    that as a stall.
    """
    welcome = (
        "<html><body>"
        '<a href="/guide/logicpro/aaf-files-lgcp6f2262ba/12.3/mac/15.6">long</a>'
        '<a href="/guide/logicpro/lgcp6f2262ba/12.3/mac/15.6">short</a>'
        "</body></html>"
    )
    fetched: list[str] = []

    def fetch(url, **kwargs):
        fetched.append(url)
        if "welcome" in url:
            return url, welcome
        # Apple 301s the short form to the long one; fetch_text returns the final URL.
        final = "https://support.apple.com/guide/logicpro/aaf-files-lgcp6f2262ba/12.3/mac/15.6"
        return final, "<html><body>topic body</body></html>"

    monkeypatch.setattr(http, "fetch_text", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = AppleHelpPattern().acquire(
        "https://support.apple.com/guide/logicpro/welcome/mac", tmp_path
    )

    topic_hits = [u for u in fetched if "welcome" not in u]
    assert len(topic_hits) == 1, f"topic fetched {len(topic_hits)}x: {topic_hits}"
    assert acq.pages == 2  # welcome + the one topic
    # The descriptive filename is preserved — _apple_merge matches TOC anchors on it.
    assert "aaf-files-lgcp6f2262ba.html" in [p.name for p in acq.raw_dir.glob("*.html")]


def test_stalled_crawl_stops_and_reports_truncated(tmp_path, monkeypatch):
    """A crawl that keeps fetching but stops producing pages must bail, not spin.

    This is the shape of the real failure: duplicate ids meant ~1963 fetches that
    all returned 200 and wrote nothing. Every request was healthy, so no timeout
    applied. Bailing with work still queued makes it a truncated result, which
    audit already fails.
    """
    from pagespring.patterns import apple_help as mod

    # Every topic resolves to the SAME file, so after the first save nothing new
    # is ever written — a fetching-but-not-progressing crawl.
    welcome = "".join(
        f'<a href="/guide/logicpro/dup-lgcp{i:08x}/12.3/mac/15.6">x</a>' for i in range(40)
    )

    def fetch(url, **kwargs):
        if "welcome" in url:
            return url, f"<html><body>{welcome}</body></html>"
        final = "https://support.apple.com/guide/logicpro/same-page-lgcpaaaaaaaa/12.3/mac/15.6"
        return final, "<html><body>topic</body></html>"

    monkeypatch.setattr(http, "fetch_text", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    monkeypatch.setattr(mod.cfg, "CRAWL_STALL_AFTER_S", 30)

    clock = {"t": 0.0}
    monkeypatch.setattr(mod.time, "monotonic", lambda: clock["t"])
    real_fetch = http.fetch_text

    def ticking(url, **kwargs):
        clock["t"] += 5.0  # each fetch costs 5s of wall clock
        return real_fetch(url, **kwargs)

    monkeypatch.setattr(http, "fetch_text", ticking)

    acq = AppleHelpPattern().acquire(
        "https://support.apple.com/guide/logicpro/welcome/mac", tmp_path
    )

    assert acq.truncated is True, "a stalled crawl must report truncated"
    assert acq.pages < 40, "it must stop early, not grind through every duplicate"


def test_extract_body_drops_scripts_and_styles():
    """Apple's help pages carry analytics and inline CSS inside #article-section."""
    from pagespring.patterns._apple_merge import extract_body

    html = """<html><body><div id="article-section">
      <h1>Add a chart</h1><p>Real content.</p>
      <script src="https://www.apple.com/metrics/ac-analytics.js"></script>
      <script>window.AC = {};</script>
      <style>.topic{color:red}</style>
      <noscript>Enable JS</noscript>
    </div></body></html>"""
    frag = extract_body(html, 2)
    assert frag is not None
    assert "Real content." in frag
    assert "<script" not in frag and "<style" not in frag and "<noscript" not in frag
    assert "ac-analytics" not in frag


def test_extract_body_drops_the_download_guides_widget():
    """Apple bolts a "Download the guides" PDF-link block onto every topic —
    1,969 copies of one string in the Logic Pro guide, 5% of its text."""
    from pagespring.patterns._apple_merge import extract_body

    html = """<html><body><div id="article-section">
      <h1>Add a fade</h1><p>Real content.</p>
      <div class="LinkDownload multiple"><p><strong>Download the guides:</strong></p>
        <a href="https://help.apple.com/pdf/logicpro.pdf">Logic Pro User Guide: PDF</a></div>
    </div></body></html>"""
    frag = extract_body(html, 2)
    assert frag is not None
    assert "Real content." in frag
    assert "LinkDownload" not in frag
    assert "Download the guides" not in frag
