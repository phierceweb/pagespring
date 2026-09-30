"""apple_help — acquire (mocked fetch) + normalize (fixture)."""

import json
from pathlib import Path

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns.apple_help import CRAWL_FAILURES, AppleHelpPattern, _parse_apple_url

FIXTURE = Path(__file__).parent / "fixtures" / "apple_help" / "numbers"


@pytest.mark.parametrize(
    "url, slug, platform",
    [
        ("https://support.apple.com/guide/numbers/welcome/mac", "numbers", "mac"),
        ("https://support.apple.com/guide/imovie/welcome/macos", "imovie", "macos"),
        ("https://support.apple.com/guide/iphone/welcome/ios", "iphone", "ios"),
        (
            "https://support.apple.com/guide/logicpro-ipad/welcome/ipados",
            "logicpro-ipad",
            "ipados",
        ),
        ("https://support.apple.com/guide/watch/welcome/watchos", "watch", "watchos"),
        (
            "https://support.apple.com/guide/numbers/intro-tables-num456/13.0/mac/14.0",
            "numbers",
            "mac",
        ),
        ("https://support.apple.com/guide/logicpro/", "logicpro", None),
        ("https://support.apple.com/en-gb/guide/watch/welcome", "watch", None),
    ],
)
def test_parse_apple_url(url, slug, platform):
    assert _parse_apple_url(url) == (slug, platform)


@pytest.mark.parametrize(
    "seed, first_fetch, final",
    [
        ("/guide/iphone/", "/guide/iphone/", "/guide/iphone/welcome/ios"),
        ("/guide/iphone/welcome", "/guide/iphone/welcome", "/guide/iphone/welcome/ios"),
        ("/guide/iphone/set-up-iph3a1b2c3d4", "/guide/iphone/welcome", "/guide/iphone/welcome/ios"),
        ("/en-gb/guide/watch/", "/en-gb/guide/watch/", "/en-gb/guide/watch/welcome/watchos"),
    ],
)
def test_a_seed_naming_no_platform_crawls_the_platform_it_redirects_to(
    tmp_path, monkeypatch, seed, first_fetch, final
):
    host = "https://support.apple.com"
    prefix = final.partition("/guide/")[0]
    guide, platform = final.split("/")[-3], final.rsplit("/", 1)[1]
    topic = f"{prefix}/guide/{guide}/set-up-iph3a1b2c3d4/{platform}"
    welcome = f'<html><body><a href="{topic}">Set up</a></body></html>'
    fetched: list[str] = []

    def fetch(url, **kwargs):
        fetched.append(url)
        if "set-up" in url:
            return f"{host}{topic}", "<html><body>topic</body></html>"
        return f"{host}{final}", welcome

    monkeypatch.setattr(http, "fetch_text", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = AppleHelpPattern().acquire(f"{host}{seed}", tmp_path)

    assert fetched[0] == f"{host}{first_fetch}"
    names = sorted(p.name for p in acq.raw_dir.glob("*.html"))
    assert names == ["set-up-iph3a1b2c3d4.html", "welcome.html"]


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
    """A capped crawl passes every content check, so the cap must travel with the
    result, not just a log line."""
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
    resolve to the same page, so deduping on the raw path segment queues it twice —
    a re-fetch that writes nothing, which a watchdog counting saved files reads as a
    stall."""
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


def test_crawl_follows_version_less_topic_links(tmp_path, monkeypatch):
    """Guide TOCs link topics as /<topic>/<platform>. Versioned links on a welcome
    page name older releases' welcome pages, which are not topics."""
    welcome = (
        "<html><body>"
        '<a href="/guide/logicpro/aaf-files-lgcp6f2262ba/mac">topic</a>'
        '<a href="/guide/logicpro/welcome/10.5/mac/10.14.6">Logic Pro 10.5</a>'
        '<a href="/guide/logicpro/other-platform-lgcp00000001/macos">other platform</a>'
        "</body></html>"
    )
    fetched: list[str] = []

    def fetch(url, **kwargs):
        fetched.append(url)
        return url, welcome if "/welcome/" in url else "<html><body>topic</body></html>"

    monkeypatch.setattr(http, "fetch_text", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = AppleHelpPattern().acquire(
        "https://support.apple.com/guide/logicpro/welcome/mac", tmp_path
    )

    assert fetched == [
        "https://support.apple.com/guide/logicpro/welcome/mac",
        "https://support.apple.com/guide/logicpro/aaf-files-lgcp6f2262ba/mac",
    ]
    assert acq.pages == 2


def test_an_ipados_guide_crawls_its_own_platform(tmp_path, monkeypatch):
    welcome = (
        '<html><body><a href="/guide/logicpro-ipad/adaptive-limiter-lpip6fcabd78/ipados">'
        "Adaptive Limiter</a></body></html>"
    )

    def fetch(url, **kwargs):
        return url, welcome if "/welcome/" in url else "<html><body>topic</body></html>"

    monkeypatch.setattr(http, "fetch_text", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = AppleHelpPattern().acquire(
        "https://support.apple.com/guide/logicpro-ipad/welcome/ipados", tmp_path
    )

    names = sorted(p.name for p in acq.raw_dir.glob("*.html"))
    assert names == ["adaptive-limiter-lpip6fcabd78.html", "welcome.html"]


def test_a_topic_seeded_crawl_fetches_the_welcome_page_first(tmp_path, monkeypatch):
    """The merge takes its outline and title from welcome.html, which no topic links to.
    The seed topic, linked again from welcome, is fetched once."""
    seed = "https://support.apple.com/guide/numbers/intro-tables-num456/mac"
    welcome = '<html><body><a href="/guide/numbers/intro-tables-num456/mac">t</a></body></html>'
    fetched: list[str] = []

    def fetch(url, **kwargs):
        fetched.append(url)
        return url, welcome if "/welcome/" in url else "<html><body>topic</body></html>"

    monkeypatch.setattr(http, "fetch_text", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = AppleHelpPattern().acquire(seed, tmp_path)

    assert fetched == ["https://support.apple.com/guide/numbers/welcome/mac", seed]
    names = sorted(p.name for p in acq.raw_dir.glob("*.html"))
    assert names == ["intro-tables-num456.html", "welcome.html"]


def test_a_topic_whose_fetch_raises_counts_as_lost(tmp_path, monkeypatch):
    """A discovered topic that never staged is reported as lost, and the crawl continues."""
    welcome = (
        "<html><body>"
        '<a href="/guide/numbers/topic-ok/14.0/mac/14.0">ok</a>'
        '<a href="/guide/numbers/topic-dead/14.0/mac/14.0">dead</a>'
        "</body></html>"
    )

    def fetch(url, **kwargs):
        if "topic-dead" in url:
            raise OSError("503 from Apple")
        if "topic-ok" in url:
            return url, "<html><body>topic</body></html>"
        return url, welcome

    monkeypatch.setattr(http, "fetch_text", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = AppleHelpPattern().acquire(
        "https://support.apple.com/guide/numbers/welcome/mac", tmp_path
    )

    assert acq.lost == 1
    assert acq.pages == 2  # welcome + the topic that did stage
    names = [p.name for p in acq.raw_dir.glob("*.html")]
    assert "topic-ok.html" in names
    assert "topic-dead.html" not in names


def test_stalled_crawl_stops_and_reports_truncated(tmp_path, monkeypatch):
    """A crawl that keeps fetching but stops producing pages must bail, not spin.

    Every request is healthy, so no timeout applies. Bailing with work still queued
    makes it a truncated result, which audit fails.
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
    """Apple bolts a "Download the guides" PDF-link block onto every topic."""
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


def test_a_topic_with_no_extractable_body_is_reported_as_lost(tmp_path):
    """Some Apple topic pages carry an article section with no <h1>, so the merge skips
    them — unreported, `pages` counts topics the deliverable does not contain."""
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "welcome.html").write_text(
        '<html><body><h1>Numbers</h1><nav><a href="good.html">Good</a>'
        '<a href="hollow.html">Hollow</a></nav></body></html>',
        encoding="utf-8",
    )
    (raw / "good.html").write_text(
        '<html><body><div id="article-section"><h1>Good</h1><p>real body</p></div></body></html>',
        encoding="utf-8",
    )
    # an article section with no <h1> — extract_body declines it
    (raw / "hollow.html").write_text(
        '<html><body><div id="article-section"><p>gallery only</p></div></body></html>',
        encoding="utf-8",
    )
    acq = AcquireResult(raw_dir=raw, kind="html", slug="numbers", pages=3, lost=0)

    merged = AppleHelpPattern().normalize(acq, tmp_path).read_text(encoding="utf-8")

    assert "real body" in merged
    assert "gallery only" not in merged, "fixture assumption: the hollow topic is skipped"
    assert acq.lost == 1, f"a dropped topic was not reported as lost (lost={acq.lost})"


def test_a_crawl_with_no_topic_refuses_to_normalize(tmp_path):
    """The titled wrapper alone is non-empty, so staging would accept a welcome-only
    crawl and clear the guide it replaces."""
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "welcome.html").write_text(
        (FIXTURE / "welcome.html").read_text(encoding="utf-8"), encoding="utf-8"
    )
    acq = AcquireResult(raw_dir=raw, kind="html", slug="numbers", pages=1)

    with pytest.raises(InvalidInputError, match="no topic page"):
        AppleHelpPattern().normalize(acq, tmp_path)


def test_a_topic_token_with_an_underscore_is_crawled(tmp_path, monkeypatch):
    welcome = (
        '<html><body><a href="/guide/logicpro-ipad/beat-breaker-tips-lpip_bbtips01/ipados">'
        "Beat Breaker tips</a></body></html>"
    )

    def fetch(url, **kwargs):
        return url, welcome if "/welcome/" in url else "<html><body>topic</body></html>"

    monkeypatch.setattr(http, "fetch_text", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = AppleHelpPattern().acquire(
        "https://support.apple.com/guide/logicpro-ipad/welcome/ipados", tmp_path
    )

    assert "beat-breaker-tips-lpip_bbtips01.html" in [p.name for p in acq.raw_dir.glob("*.html")]


def test_a_locale_seed_keeps_its_locale_and_saves_welcome(tmp_path, monkeypatch):
    seed = "https://support.apple.com/en-gb/guide/numbers/intro-tables-num456/mac"
    welcome = (
        "<html><body>"
        '<a href="/en-gb/guide/numbers/intro-tables-num456/mac">t</a>'
        '<a href="/en-gb/guide/numbers/whats-new-num123/mac">n</a>'
        "</body></html>"
    )
    fetched: list[str] = []

    def fetch(url, **kwargs):
        fetched.append(url)
        return url, welcome if "/welcome/" in url else "<html><body>topic</body></html>"

    monkeypatch.setattr(http, "fetch_text", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = AppleHelpPattern().acquire(seed, tmp_path)

    assert fetched == [
        "https://support.apple.com/en-gb/guide/numbers/welcome/mac",
        seed,
        "https://support.apple.com/en-gb/guide/numbers/whats-new-num123/mac",
    ]
    names = sorted(p.name for p in acq.raw_dir.glob("*.html"))
    assert names == ["intro-tables-num456.html", "welcome.html", "whats-new-num123.html"]


def test_a_versioned_topic_seed_fetches_that_releases_welcome_page(tmp_path, monkeypatch):
    fetched: list[str] = []

    def fetch(url, **kwargs):
        fetched.append(url)
        return url, "<html><body>no links</body></html>"

    monkeypatch.setattr(http, "fetch_text", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    AppleHelpPattern().acquire(
        "https://support.apple.com/guide/numbers/intro-tables-num456/13.0/mac/14.0", tmp_path
    )

    assert fetched[0] == "https://support.apple.com/guide/numbers/welcome/13.0/mac/14.0"


@pytest.mark.parametrize("crawl_lost", [0, 1], ids=["never-queued", "fetch-failed"])
def test_a_toc_topic_the_crawl_never_saved_counts_as_lost_once(tmp_path, crawl_lost):
    """A link shape the crawl cannot follow leaves TOC entries unfetched; the merge skips
    them silently, so they are reported against the TOC — without counting a topic
    whose failed fetch the crawl already reported."""
    raw = tmp_path / "raw"
    raw.mkdir()
    for name in ("welcome.html", "whats-new-num123.html"):
        (raw / name).write_text((FIXTURE / name).read_text(encoding="utf-8"), encoding="utf-8")
    acq = AcquireResult(raw_dir=raw, kind="html", slug="numbers", pages=2, lost=crawl_lost)

    AppleHelpPattern().normalize(acq, tmp_path)

    assert acq.lost == 1  # intro-tables-num456 is in the TOC but was never saved


def test_normalizing_again_with_the_reported_lost_reports_the_same_lost(tmp_path):
    """A replay seeds `lost` from the manifest, which already holds this normalize's count."""
    raw = tmp_path / "raw"
    raw.mkdir()
    for name in ("welcome.html", "whats-new-num123.html"):
        (raw / name).write_text((FIXTURE / name).read_text(encoding="utf-8"), encoding="utf-8")
    (raw / "hollow.html").write_text(
        '<html><body><div id="article-section"><p>gallery only</p></div></body></html>',
        encoding="utf-8",
    )
    first = AcquireResult(raw_dir=raw, kind="html", slug="numbers", pages=3)
    AppleHelpPattern().normalize(first, tmp_path)
    replay = AcquireResult(raw_dir=raw, kind="html", slug="numbers", pages=3, lost=first.lost)

    AppleHelpPattern().normalize(replay, tmp_path)

    assert first.lost == 2  # intro-tables-num456 never saved + hollow has no body
    assert replay.lost == first.lost


def test_a_seed_redirected_off_the_guide_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(
        http, "fetch_text", lambda url, **k: ("https://support.apple.com/", "<html></html>")
    )
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = AppleHelpPattern().acquire("https://support.apple.com/guide/aperture/", tmp_path)

    with pytest.raises(InvalidInputError, match="no topic page"):
        AppleHelpPattern().normalize(acq, tmp_path)


def _raw_from_fixture(tmp_path, *names, failed=None, welcome=None):
    """A raw/ holding fixture pages; ``failed`` writes the crawl's failure record."""
    raw = tmp_path / "raw"
    raw.mkdir()
    for name in names:
        (raw / name).write_text((FIXTURE / name).read_text(encoding="utf-8"), encoding="utf-8")
    if welcome is not None:
        (raw / "welcome.html").write_text(welcome, encoding="utf-8")
    if failed is not None:
        (raw / CRAWL_FAILURES).write_text(json.dumps({"failed": failed}), encoding="utf-8")
    return raw


_HOLLOW = '<html><body><div id="article-section"><p>gallery only</p></div></body></html>'


def test_the_crawl_records_the_urls_it_failed_on_in_raw(tmp_path, monkeypatch):
    """A replay has only raw/, so the crawl's losses must be there for it to count."""
    dead = "https://support.apple.com/guide/numbers/topic-dead/14.0/mac/14.0"
    welcome = f'<html><body><a href="{dead}">dead</a></body></html>'

    def fetch(url, **kwargs):
        if url == dead:
            raise OSError("503 from Apple")
        return url, welcome

    monkeypatch.setattr(http, "fetch_text", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = AppleHelpPattern().acquire(
        "https://support.apple.com/guide/numbers/welcome/mac", tmp_path
    )

    record = json.loads((acq.raw_dir / CRAWL_FAILURES).read_text(encoding="utf-8"))
    assert record == {"failed": [dead]}


def test_a_clean_crawl_records_that_nothing_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _two_page_fetch())
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = AppleHelpPattern().acquire(
        "https://support.apple.com/guide/numbers/welcome/mac", tmp_path
    )

    record = json.loads((acq.raw_dir / CRAWL_FAILURES).read_text(encoding="utf-8"))
    assert record == {"failed": []}


def test_a_fetch_error_outside_the_toc_does_not_hide_a_merge_drop(tmp_path):
    raw = _raw_from_fixture(
        tmp_path,
        "welcome.html",
        "whats-new-num123.html",
        "intro-tables-num456.html",
        failed=["https://support.apple.com/guide/numbers/elsewhere-num999/mac"],
    )
    (raw / "hollow-num777.html").write_text(_HOLLOW, encoding="utf-8")
    acq = AcquireResult(raw_dir=raw, kind="html", slug="numbers", pages=4, lost=1)

    AppleHelpPattern().normalize(acq, tmp_path)

    assert acq.lost == 2  # the failed fetch + the hollow page the merge dropped


def test_a_failed_toc_topic_counts_once_under_either_link_form(tmp_path):
    """Apple links a topic as `<words>-<token>` and as bare `<token>`: one topic."""
    welcome = (FIXTURE / "welcome.html").read_text(encoding="utf-8")
    welcome = welcome.replace("intro-tables-num456", "intro-tables-tan1a2b3c4d")
    raw = _raw_from_fixture(
        tmp_path,
        "whats-new-num123.html",
        welcome=welcome,
        failed=["https://support.apple.com/guide/numbers/tan1a2b3c4d/mac"],
    )
    acq = AcquireResult(raw_dir=raw, kind="html", slug="numbers", pages=2, lost=1)

    AppleHelpPattern().normalize(acq, tmp_path)

    assert acq.lost == 1


def test_a_replay_recomputes_lost_from_raw(tmp_path):
    """A replay seeds `lost` from the manifest; a normalize that now recovers a topic
    must be able to report fewer."""
    raw = _raw_from_fixture(
        tmp_path, "welcome.html", "whats-new-num123.html", "intro-tables-num456.html", failed=[]
    )
    acq = AcquireResult(raw_dir=raw, kind="html", slug="numbers", pages=3, lost=3)

    AppleHelpPattern().normalize(acq, tmp_path)

    assert acq.lost == 0


def test_a_raw_dir_without_the_record_keeps_the_count_it_was_given(tmp_path):
    raw = _raw_from_fixture(
        tmp_path, "welcome.html", "whats-new-num123.html", "intro-tables-num456.html"
    )
    acq = AcquireResult(raw_dir=raw, kind="html", slug="numbers", pages=3, lost=2)

    AppleHelpPattern().normalize(acq, tmp_path)

    assert acq.lost == 2


def test_an_unreadable_record_keeps_the_count_it_was_given(tmp_path):
    raw = _raw_from_fixture(
        tmp_path, "welcome.html", "whats-new-num123.html", "intro-tables-num456.html"
    )
    (raw / CRAWL_FAILURES).write_text('{"failed": [', encoding="utf-8")
    acq = AcquireResult(raw_dir=raw, kind="html", slug="numbers", pages=3, lost=2)

    AppleHelpPattern().normalize(acq, tmp_path)

    assert acq.lost == 2


def test_an_overridden_slug_still_checks_the_guides_own_toc(tmp_path):
    """`--slug` renames the deliverable, not the guide its TOC links."""
    welcome = (FIXTURE / "welcome.html").read_text(encoding="utf-8")
    welcome = welcome.replace(
        "<head>",
        '<head><link rel="canonical" href="https://support.apple.com/guide/numbers/welcome/mac" />',
    )
    raw = _raw_from_fixture(tmp_path, "whats-new-num123.html", welcome=welcome, failed=[])
    acq = AcquireResult(raw_dir=raw, kind="html", slug="numbers-user-guide", pages=2)

    AppleHelpPattern().normalize(acq, tmp_path)

    assert acq.lost == 1  # intro-tables-num456 is in the TOC but was never saved


def test_renormalize_lowers_lost_once_the_replay_recovers_a_topic(tmp_path, monkeypatch):
    from pagespring import manifest, orchestrate, renormalize

    monkeypatch.setattr(orchestrate.cfg, "INCOMING_DIR", str(tmp_path / "incoming"))
    slug_dir = tmp_path / "incoming" / "numbers"
    slug_dir.mkdir(parents=True)
    raw = _raw_from_fixture(
        slug_dir, "welcome.html", "whats-new-num123.html", "intro-tables-num456.html", failed=[]
    )
    first = AcquireResult(raw_dir=raw, kind="html", slug="numbers", pages=3)
    clean = AppleHelpPattern().normalize(first, tmp_path)
    (slug_dir / "numbers.html").write_bytes(clean.read_bytes())
    manifest.write_manifest(
        slug_dir,
        manifest.build_manifest(
            source_url="https://support.apple.com/guide/numbers/welcome/mac",
            pattern="apple_help",
            slug="numbers",
            kind="html",
            deliverable="numbers.html",
            pages=3,
            size_bytes=clean.stat().st_size,
            sha256=manifest.sha256_file(clean),
            images=0,
            ingested_at="2026-09-22T00:00:00Z",
            kept_raw=True,
            lost=1,  # an earlier normalize dropped a topic this one recovers
        ),
    )

    renormalize.run_renormalize("numbers")

    assert manifest.read_manifest(slug_dir)["lost"] == 0
