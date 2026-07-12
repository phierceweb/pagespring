"""zendesk_help — match + paginated API acquire/normalize (mocked fetch)."""

import json

from pagespring import http
from pagespring.patterns.zendesk_help import ZendeskHelpPattern

_PAGE1 = json.dumps(
    {
        "articles": [
            {
                "title": "Study Timer",
                "body": "<p>Use a timer.</p>",
                "html_url": "https://support.gingerlabs.com/hc/en-us/articles/1",
            },
        ],
        "next_page": "https://support.gingerlabs.com/api/v2/help_center/en-us/articles.json?page=2&per_page=100",
    }
)
_PAGE2 = json.dumps(
    {
        "articles": [
            {
                "title": "Handwriting",
                "body": "<p>Write with a pencil.</p>",
                "html_url": "https://support.gingerlabs.com/hc/en-us/articles/2",
            },
        ],
        "next_page": None,
    }
)


def _fake_fetch_text(url, **kwargs):
    if "page=2" in url:
        return url, _PAGE2
    return url, _PAGE1


def test_match():
    p = ZendeskHelpPattern()
    assert p.match("https://support.gingerlabs.com/hc/en-us")
    assert p.match("https://company.zendesk.com/hc/en-us/articles/123")
    assert not p.match("https://example.com/docs")


class _LogSpy:
    def __init__(self):
        self.warnings = []

    def warning(self, event, **kw):
        self.warnings.append((event, kw))

    def info(self, *a, **kw):
        pass


def test_api_page_cap_warns(tmp_path, monkeypatch):
    """Stopping at _MAX_PAGES with a next_page still pending is loud, not silent."""
    from pagespring.patterns import zendesk_help as mod

    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_MAX_PAGES", 1)
    spy = _LogSpy()
    monkeypatch.setattr(mod, "log", spy)

    acq = ZendeskHelpPattern().acquire("https://support.gingerlabs.com/hc/en-us", tmp_path)

    assert acq.pages == 1  # only page 1's article fetched
    assert any(event == "zendesk_help.capped" for event, _ in spy.warnings)


def test_acquire_paginates_and_merges(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = ZendeskHelpPattern()

    acq = p.acquire("https://support.gingerlabs.com/hc/en-us", tmp_path)
    assert acq.kind == "html"
    assert acq.slug == "gingerlabs"
    assert acq.pages == 2  # one article per API page
    assert len(list(acq.raw_dir.glob("*.html"))) == 2  # both pages' articles

    html = p.normalize(acq, tmp_path).read_text(encoding="utf-8")
    assert "<h1>Gingerlabs Help</h1>" in html
    assert "<h2>Study Timer</h2>" in html  # page 1
    assert "<h2>Handwriting</h2>" in html  # page 2 (pagination followed)
    assert "Write with a pencil." in html
    assert "source: https://support.gingerlabs.com/hc/en-us/articles/2" in html
