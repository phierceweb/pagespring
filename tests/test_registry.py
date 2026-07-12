"""Registry routing (pure, no network)."""

import pytest

from pagespring.base import Pattern
from pagespring.registry import PATTERNS, classify


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://support.apple.com/guide/numbers/welcome/mac", "apple_help"),
        ("https://support.apple.com/guide/imovie/playback-clips-abc/mac", "apple_help"),
        ("https://platform.claude.com/docs/en/docs/claude-code", "llms_txt"),
        ("https://code.claude.com/llms.txt", "llms_txt"),
        ("https://docs.foo.com/llms.txt", "llms_txt"),
        ("https://github.com/laravel/docs", "github_markdown"),
        ("https://github.com/laravel/docs/tree/13.x", "github_markdown"),
        ("https://support.gingerlabs.com/hc/en-us", "zendesk_help"),
        ("https://company.zendesk.com/hc/en-us/articles/9", "zendesk_help"),
        ("https://support.microsoft.com/en-us/excel", "microsoft_support"),
        ("https://docs.python.org/3/archives/python-3.14-docs-text.zip", "archive_download"),
        ("https://x.com/manuals/Kemper_Main_14.pdf", "pdf_url"),
        ("https://picard-docs.musicbrainz.org/_/downloads/en/latest/pdf/", "pdf_url"),
        ("https://requests.readthedocs.io/en/latest/", "readthedocs"),
        ("https://requests.readthedocs.io/_/downloads/en/latest/pdf/", "pdf_url"),
        # extension beats the broad docs.* gitbook heuristic:
        ("https://docs.vendor.com/guide/manual.pdf", "pdf_url"),
        # docs.* custom domains no longer match gitbook directly — docs_probe's
        # llms.txt sniff routes them back to the gitbook machinery at acquire time.
        ("https://docs.tableplus.com", "docs_probe"),
        ("https://mycompany.gitbook.io/handbook", "gitbook"),
        # docs_probe is the last-resort catch-all for any unmatched http(s) URL.
        ("https://en.wikipedia.org/wiki/Spreadsheet", "docs_probe"),
        ("https://example.com/some/page.html", "docs_probe"),
    ],
)
def test_classify_routes(url, expected):
    pattern = classify(url)
    assert (pattern.name if pattern else None) == expected


def test_all_patterns_satisfy_protocol():
    assert PATTERNS, "registry should not be empty"
    for pattern in PATTERNS:
        assert isinstance(pattern, Pattern)
        assert pattern.name
        assert isinstance(pattern.convert_recipe, list)


def test_docs_probe_is_last_and_claims_unmatched():
    assert PATTERNS[-1].name == "docs_probe"
    p = classify("https://plain.example.com/anything")
    assert p is not None and p.name == "docs_probe"


def test_gitbook_narrowed_to_gitbook_io():
    p = classify("https://acme.gitbook.io/handbook")
    assert p is not None and p.name == "gitbook"
    q = classify("https://docs.tableplus.com")
    assert q is not None and q.name == "docs_probe"
