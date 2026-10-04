"""zendesk_help — match + paginated API acquire/normalize (mocked fetch)."""

import json

import pytest

from pagespring import http
from pagespring.patterns import zendesk_help
from pagespring.patterns.zendesk_help import ZendeskHelpPattern, _slug

_PAGE1 = json.dumps(
    {
        "articles": [
            {
                "title": "Study Timer",
                "body": "<p>Use a timer.</p>",
                "html_url": "https://support.acme.example/hc/en-us/articles/1",
            },
        ],
        "next_page": "https://support.acme.example/api/v2/help_center/en-us/articles.json?page=2&per_page=100",
    }
)
_PAGE2 = json.dumps(
    {
        "articles": [
            {
                "title": "Handwriting",
                "body": "<p>Write with a pencil.</p>",
                "html_url": "https://support.acme.example/hc/en-us/articles/2",
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
                    "html_url": "https://support.acme.example/hc/en-us/articles/9",
                },
            ],
            "next_page": None,
        }
    )
    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: (url, dirty))
    monkeypatch.setattr(http, "polite_sleep", lambda: None)

    acq = ZendeskHelpPattern().acquire("https://support.acme.example/hc/en-us", tmp_path)
    merged = "".join(p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html")))

    assert "tiktok.com/embed.js" not in merged
    assert "<script" not in merged
    assert "<style" not in merged
    assert "Watch this." in merged and "Then read on." in merged  # content survives


def test_match():
    p = ZendeskHelpPattern()
    assert p.match("https://support.acme.example/hc/en-us")
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

    acq = ZendeskHelpPattern().acquire("https://support.acme.example/hc/en-us", tmp_path)

    assert acq.pages == 1  # only page 1's article fetched
    assert any(event == "zendesk_help.capped" for event, _ in spy.warnings)


def _spy_fetch(seen):
    def fetch(url, **kwargs):
        seen.append(url)
        return url, json.dumps(
            {
                "articles": [
                    {
                        "title": "Setting up Widget 6",
                        "body": "<p>Insert the plug-in.</p>",
                        "html_url": "https://acme.zendesk.com/hc/en-us/articles/9",
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
        "https://acme.zendesk.com/hc/en-us/sections/360000000001-Widget-6", tmp_path
    )

    assert "/api/v2/help_center/en-us/sections/360000000001/articles.json" in seen[0]
    assert acq.pages == 1


def test_category_url_scopes_to_that_category(tmp_path, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(http, "fetch_text", _spy_fetch(seen))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    ZendeskHelpPattern().acquire(
        "https://acme.zendesk.com/hc/en-us/categories/360000000002-Products", tmp_path
    )

    assert "/api/v2/help_center/en-us/categories/360000000002/articles.json" in seen[0]


def test_unscoped_url_still_pulls_whole_help_center(tmp_path, monkeypatch):
    """Regression: the bare /hc/<locale> form must keep using the global endpoint."""
    seen: list[str] = []
    monkeypatch.setattr(http, "fetch_text", _spy_fetch(seen))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    ZendeskHelpPattern().acquire("https://support.acme.example/hc/en-us", tmp_path)

    assert "/api/v2/help_center/en-us/articles.json" in seen[0]
    assert "/sections/" not in seen[0]


def test_acquire_paginates_and_merges(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = ZendeskHelpPattern()

    acq = p.acquire("https://support.acme.example/hc/en-us", tmp_path)
    assert acq.kind == "html"
    assert acq.slug == "acme"
    assert acq.pages == 2  # one article per API page
    assert len(list(acq.raw_dir.glob("*.html"))) == 2  # both pages' articles

    html = p.normalize(acq, tmp_path).read_text(encoding="utf-8")
    assert "<h1>Acme Help</h1>" in html
    assert "<h2>Study Timer</h2>" in html  # page 1
    assert "<h2>Handwriting</h2>" in html  # page 2 (pagination followed)
    assert "Write with a pencil." in html
    assert "source: https://support.acme.example/hc/en-us/articles/2" in html


@pytest.mark.parametrize(
    "body",
    ["<!DOCTYPE html><html><body>Help Center</body></html>", "[]", '"closed"'],
)
def test_an_api_answer_that_is_not_an_articles_object_is_invalid_input(tmp_path, monkeypatch, body):
    """A /hc/ path on a site that is not Zendesk answers the API URL with its own page."""
    from pf_core.exceptions import InvalidInputError

    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: (url, body))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    with pytest.raises(InvalidInputError, match=r"help\.acme\.com/api/v2/help_center/en-us"):
        ZendeskHelpPattern().acquire("https://help.acme.com/hc/en-us", tmp_path)


def test_hc_must_lead_the_path():
    p = ZendeskHelpPattern()
    assert not p.match("https://example.com/docs/hc/intro")
    assert not p.match("https://example.com/manuals/hc/en-us")
    assert p.match("https://example.com/hc/en-us/articles/1")


def test_attachment_url_is_not_claimed():
    """An attachment is a file: claimed, a lone PDF would pull the whole center; declined,
    docs_probe's sniff routes it to pdf_url."""
    url = "https://vendor.zendesk.com/hc/en-us/article_attachments/37651641721111"

    assert not ZendeskHelpPattern().match(url)


def test_help_center_urls_are_still_claimed():
    p = ZendeskHelpPattern()

    assert p.match("https://support.acme.example/hc/en-us")
    assert p.match("https://acme.zendesk.com/hc/en-us/articles/9-Setting-up")


def test_article_url_scopes_to_that_article(tmp_path, monkeypatch):
    """An article URL narrows the endpoint too; matching only sections and
    categories falls through to the unscoped endpoint and pages the whole center."""
    seen: list[str] = []
    monkeypatch.setattr(http, "fetch_text", _spy_fetch(seen))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    ZendeskHelpPattern().acquire(
        "https://vendor.zendesk.com/hc/en-us/articles/360000000003-Model-42-Manual", tmp_path
    )

    assert "/api/v2/help_center/en-us/articles/360000000003" in seen[0]


def test_article_slug_does_not_collide_with_the_whole_center(tmp_path, monkeypatch):
    """A host-only slug would put a single-article ingest and a whole-center ingest
    of the same vendor in one incoming/ dir, each overwriting the other."""
    monkeypatch.setattr(http, "fetch_text", _spy_fetch([]))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = ZendeskHelpPattern()

    article = p.acquire(
        "https://acme.zendesk.com/hc/en-us/articles/9-Setting-up-Widget-6", tmp_path
    )
    center = p.acquire("https://acme.zendesk.com/hc/en-us", tmp_path)

    assert article.slug != center.slug


def test_article_ingest_marks_itself_a_single_document(tmp_path, monkeypatch):
    """One article IS the deliverable. Without this, audit's single_page_crawl
    fires on a healthy slug and `audit --all --strict` exits 1."""
    monkeypatch.setattr(http, "fetch_text", _spy_fetch([]))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = ZendeskHelpPattern().acquire(
        "https://acme.zendesk.com/hc/en-us/articles/9-Setting-up", tmp_path
    )

    assert acq.single_document is True


def test_whole_center_ingest_is_not_a_single_document(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _spy_fetch([]))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = ZendeskHelpPattern().acquire("https://support.acme.example/hc/en-us", tmp_path)

    assert acq.single_document is False


def test_article_slug_keeps_the_vendor_host():
    """A title-only slug collides across vendors, and the second ingest clears the first unflagged."""
    one = _slug("https://support.a.com/hc/en-us/articles/111-Getting-Started")
    two = _slug("https://support.b.com/hc/en-us/articles/222-Getting-Started")

    assert one != two
    assert "getting-started" in one


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/hc/de/sections/12345-Handbuch", "de"),
        ("/hc/fr", "fr"),
        ("/hc/ja/articles/9-x", "ja"),
        ("/hc/es-419/sections/1", "es-419"),
        ("/hc/en-us/articles/1", "en-us"),
        ("/hc/articles/1", "en-us"),
    ],
)
def test_bare_two_letter_and_regional_locales_are_honoured(path, expected):
    """Zendesk publishes /hc/de and /hc/es-419 as well as xx-xx; matching only xx-xx falls
    back to en-us and silently acquires the wrong language."""
    _origin, locale = zendesk_help._api_base_and_locale(f"https://support.v.com{path}")
    assert locale == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://support.vendor.com/hc/en-us", "vendor"),
        ("https://www.vendor.com/hc/en-us", "vendor"),
        ("https://vendor.com/hc/en-us/articles/123-Thing", "vendor-thing"),
    ],
)
def test_slug_is_stable_across_host_spellings(url, expected):
    assert _slug(url) == expected
