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


def test_article_bodies_are_stripped_of_scripts(tmp_path, monkeypatch):
    """Zendesk article bodies are author-supplied HTML — they carry embed and
    tracking <script>, which is page furniture, not manual content."""
    dirty = json.dumps(
        {
            "articles": [
                {
                    "title": "Embeds",
                    "body": (
                        '<p>Watch this.</p><script async="async" src="https://www.tiktok.com/embed.js">'
                        "</script><style>.x{color:red}</style><p>Then read on.</p>"
                    ),
                    "html_url": "https://support.gingerlabs.com/hc/en-us/articles/9",
                },
            ],
            "next_page": None,
        }
    )
    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: (url, dirty))
    monkeypatch.setattr(http, "polite_sleep", lambda: None)

    acq = ZendeskHelpPattern().acquire("https://support.gingerlabs.com/hc/en-us", tmp_path)
    merged = "".join(p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html")))

    assert "tiktok.com/embed.js" not in merged
    assert "<script" not in merged
    assert "<style" not in merged
    assert "Watch this." in merged and "Then read on." in merged  # content survives


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


def _spy_fetch(seen):
    def fetch(url, **kwargs):
        seen.append(url)
        return url, json.dumps(
            {
                "articles": [
                    {
                        "title": "Setting up VocAlign 6",
                        "body": "<p>Insert the plug-in.</p>",
                        "html_url": "https://synchroarts.zendesk.com/hc/en-us/articles/9",
                    }
                ],
                "next_page": None,
            }
        )

    return fetch


def test_section_url_scopes_to_that_section(tmp_path, monkeypatch):
    """A /sections/<id> URL pulls only that section, not the whole help center."""
    seen: list[str] = []
    monkeypatch.setattr(http, "fetch_text", _spy_fetch(seen))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = ZendeskHelpPattern().acquire(
        "https://synchroarts.zendesk.com/hc/en-us/sections/23943152503959-VocAlign-6", tmp_path
    )

    assert "/api/v2/help_center/en-us/sections/23943152503959/articles.json" in seen[0]
    assert acq.pages == 1


def test_category_url_scopes_to_that_category(tmp_path, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(http, "fetch_text", _spy_fetch(seen))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    ZendeskHelpPattern().acquire(
        "https://synchroarts.zendesk.com/hc/en-us/categories/4408150081431-Products", tmp_path
    )

    assert "/api/v2/help_center/en-us/categories/4408150081431/articles.json" in seen[0]


def test_unscoped_url_still_pulls_whole_help_center(tmp_path, monkeypatch):
    """Regression: the bare /hc/<locale> form must keep using the global endpoint."""
    seen: list[str] = []
    monkeypatch.setattr(http, "fetch_text", _spy_fetch(seen))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    ZendeskHelpPattern().acquire("https://support.gingerlabs.com/hc/en-us", tmp_path)

    assert "/api/v2/help_center/en-us/articles.json" in seen[0]
    assert "/sections/" not in seen[0]


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
