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
