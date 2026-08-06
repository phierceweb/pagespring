"""llms_txt — match + acquire/normalize with a synthetic index (no network)."""

from pagespring import http
from pagespring.patterns.llms_txt import LlmsTxtPattern

_LLMS = """# Docs index
- [Overview](https://ex.com/docs/en/claude-code/overview.md)
- [Setup](https://ex.com/docs/en/claude-code/setup.md)
- [Unrelated](https://ex.com/docs/en/other/thing.md)
"""

# The per-page banner GitBook-hosted llms.txt sites prepend (seen live on
# code.claude.com): a blockquote pointing agents back at the index.
_BANNER = (
    "> ## Documentation Index\n"
    "> Fetch the complete documentation index at: https://ex.com/llms.txt\n"
    "> Use this file to discover all available pages before exploring further.\n\n"
)
_PAGES = {
    "https://ex.com/docs/en/claude-code/overview.md": _BANNER + "# Overview\nWelcome.",
    "https://ex.com/docs/en/claude-code/setup.md": _BANNER + "# Setup\nInstall it.",
    "https://ex.com/docs/en/other/thing.md": "# Other\nUnrelated content.",
}


def _fake_fetch_text(url, **kwargs):
    if url.endswith("llms.txt"):
        return url, _LLMS
    return url, _PAGES[url]


def test_match():
    p = LlmsTxtPattern()
    assert p.match("https://platform.claude.com/docs/en/docs/claude-code")
    assert p.match("https://docs.foo.com/llms.txt")
    assert not p.match("https://example.com/whatever")


def test_acquire_filters_to_section_and_concats(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = LlmsTxtPattern()

    # Section base URL -> llms.txt at host root, keep only that section's pages.
    acq = p.acquire("https://ex.com/docs/en/claude-code", tmp_path)
    assert acq.kind == "markdown"
    assert acq.slug == "claude-code"
    assert acq.pages == 2
    assert len(list(acq.raw_dir.glob("*.md"))) == 2  # "other" filtered out

    clean = p.normalize(acq, tmp_path)
    text = clean.read_text(encoding="utf-8")
    assert "# Overview" in text and "# Setup" in text
    assert "Unrelated content" not in text
    # Order preserved (Overview before Setup) and provenance recorded.
    assert text.index("# Overview") < text.index("# Setup")
    assert "source: https://ex.com/docs/en/claude-code/overview.md" in text
    # The platform's per-page "Documentation Index" banner is stripped.
    assert "Documentation Index" not in text
    assert "discover all available pages" not in text


def test_anchor_links_ending_in_md_are_not_pages(tmp_path, monkeypatch):
    """Same defect as gitbook: a `.md` in the fragment is an in-page anchor, not
    a page, and its path-derived stem has no .md for normalize's glob to find."""
    index = (
        "- [A](https://ex.com/a.md)\n"
        "- [Anchor](https://ex.com/a#section-one.md)\n"
        "- [B](https://ex.com/b.md)\n"
    )

    def fake_fetch(url, **kw):
        if url.endswith("llms.txt"):
            return url, index
        return url, f"# Page\n\nBody of {url}\n"

    monkeypatch.setattr(http, "fetch_text", fake_fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    p = LlmsTxtPattern()
    acq = p.acquire("https://ex.com/llms.txt", tmp_path)
    out = p.normalize(acq, tmp_path).read_text(encoding="utf-8")

    assert acq.pages == 2, f"expected 2 real pages, got {acq.pages}"
    assert out.count("<!-- source:") == acq.pages


def test_pages_lost_to_fetch_errors_are_counted(tmp_path, monkeypatch):
    """Throttling drops pages one at a time; uncounted, the manifest reports a
    complete crawl and audit sees nothing missing."""

    def fake(url, **kwargs):
        if url.endswith("/setup.md"):
            raise OSError("503 throttled")
        return _fake_fetch_text(url, **kwargs)

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = LlmsTxtPattern().acquire("https://ex.com/docs/en/claude-code", tmp_path)

    assert acq.pages == 1
    assert acq.lost == 1
