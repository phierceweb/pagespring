"""_mkdocs — search-index acquisition with a synthetic index (no network)."""

import json

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.patterns import _mkdocs

_INDEX = {
    "config": {"lang": ["en"]},
    "docs": [
        {"location": "", "title": "Home", "text": "Welcome to the docs."},
        {"location": "#install", "title": "Install", "text": "pip install it."},
        {"location": "guide/", "title": "User Guide", "text": "How to use it."},
        {"location": "guide/#advanced", "title": "Advanced", "text": "Power features."},
    ],
}


def _fake_fetch_text(url, **kwargs):
    assert url == "https://www.mkdocs.org/search/search_index.json"
    return url, json.dumps(_INDEX)


def test_acquire_groups_sections_under_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    acq = _mkdocs.acquire("https://www.mkdocs.org", tmp_path, slug="mkdocs", title="MkDocs")
    assert acq.kind == "markdown"
    assert acq.slug == "mkdocs"
    assert acq.title == "MkDocs"
    assert acq.pages == 2  # two distinct pages (root + guide/)

    files = sorted(acq.raw_dir.glob("*.md"))
    assert len(files) == 2
    root = files[0].read_text(encoding="utf-8")
    assert "# Home" in root and "## Install" in root
    assert "Welcome to the docs." in root and "pip install it." in root
    guide = files[1].read_text(encoding="utf-8")
    assert "# User Guide" in guide and "## Advanced" in guide
    assert "source: https://www.mkdocs.org/guide/" in guide


def test_acquire_rejects_non_index_json(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", lambda url, **k: (url, "<html>404 page</html>"))
    with pytest.raises(InvalidInputError):
        _mkdocs.acquire("https://ex.com", tmp_path, slug="ex", title=None)


def test_acquire_rejects_non_dict_record(tmp_path, monkeypatch):
    """A docs[] array holding a non-object entry must raise InvalidInputError,
    not crash with AttributeError inside the grouping loop."""
    bad = {"docs": [{"location": "", "title": "Home", "text": "ok"}, "not-an-object"]}
    monkeypatch.setattr(http, "fetch_text", lambda url, **k: (url, json.dumps(bad)))
    with pytest.raises(InvalidInputError):
        _mkdocs.acquire("https://ex.com", tmp_path, slug="ex", title=None)


def test_acquire_uses_post_redirect_base_for_source_comments(tmp_path, monkeypatch):
    """When the index fetch redirects, source comments must use the final URL's
    base, not the pre-redirect one."""

    def _redirecting_fetch(url, **kwargs):
        assert url == "https://x.example.com/search/search_index.json"
        final = "https://x.example.com/en/latest/search/search_index.json"
        return final, json.dumps(_INDEX)

    monkeypatch.setattr(http, "fetch_text", _redirecting_fetch)
    acq = _mkdocs.acquire("https://x.example.com", tmp_path, slug="x", title=None)
    guide = sorted(acq.raw_dir.glob("*.md"))[1].read_text(encoding="utf-8")
    assert "source: https://x.example.com/en/latest/guide/" in guide
    assert "source: https://x.example.com/guide/" not in guide


def test_page_record_text_is_not_repeated_under_its_sections(tmp_path, monkeypatch):
    """MkDocs' index carries each page TWICE: one page-level record holding the
    whole page's text, then one record per section holding the same text again.
    Emitting both made half of the mkdocs deliverable a verbatim duplicate."""
    index = json.dumps(
        {
            "docs": [
                {
                    "location": "guide/",
                    "title": "Guide",
                    "text": "Intro blurb. Install step. Config step.",
                },
                {"location": "guide/#install", "title": "Install", "text": "Install step."},
                {"location": "guide/#config", "title": "Config", "text": "Config step."},
            ]
        }
    )
    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: (url, index))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = _mkdocs.acquire("https://ex.org", tmp_path, slug="ex", title=None)
    merged = "".join(p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*")))

    assert "# Guide" in merged  # the page title still heads its section
    assert "## Install" in merged and "## Config" in merged
    assert merged.count("Install step.") == 1, "page blob duplicated its own sections"
    assert merged.count("Config step.") == 1
    assert "Intro blurb." in merged, "page-level prose before the first section is content"
