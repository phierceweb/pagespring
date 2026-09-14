"""llms_txt — match + acquire/normalize with a synthetic index (no network)."""

from pagespring import http
from pagespring.base import AcquireResult
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


_FULL = """# Acme Docs

Everything about Acme, inlined.

## Install
Run the installer.

## Contributing
See https://github.com/acme/acme/blob/main/CONTRIBUTING.md for the process.
"""


def test_llms_full_txt_body_is_the_deliverable(tmp_path, monkeypatch):
    """llms-full.txt IS the documentation, inlined. Link-scraping it threw the
    body away and shipped whatever .md URLs its prose happened to cite."""

    def fake(url, **kw):
        assert url == "https://docs.acme.com/llms-full.txt", f"unexpected fetch: {url}"
        return url, _FULL

    monkeypatch.setattr(http, "fetch_text", fake)
    p = LlmsTxtPattern()
    acq = p.acquire("https://docs.acme.com/llms-full.txt", tmp_path)

    assert acq.pages == 1, f"the full text is one document, got pages={acq.pages}"
    assert acq.single_document is True, "a 1-page deliverable here is correct, not suspect"

    out = p.normalize(acq, tmp_path).read_text(encoding="utf-8")
    assert "Everything about Acme, inlined." in out, "the documentation body was discarded"
    assert "Run the installer." in out


def test_llms_full_txt_slug_comes_from_the_host(tmp_path, monkeypatch):
    """`endswith("llms.txt")` is False for "llms-full.txt", so every host slugged
    to the same `llms-full-txt` — and a second vendor's ingest wiped the first."""
    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: (url, _FULL))
    p = LlmsTxtPattern()

    a = p.acquire("https://docs.acme.com/llms-full.txt", tmp_path / "a")
    b = p.acquire("https://docs.other.com/llms-full.txt", tmp_path / "b")

    assert a.slug == "docs-acme-com", f"host-derived slug expected, got {a.slug!r}"
    assert a.slug != b.slug, "two vendors collided on one slug"


def test_match_survives_query_fragment_and_case(monkeypatch):
    """`endswith` on the raw URL let a query string, fragment, or uppercase
    spelling route llms-full.txt to docs_probe — which crawls the whole site
    instead of taking the inlined body."""
    p = LlmsTxtPattern()
    assert p.match("https://docs.foo.com/llms-full.txt?v=2")
    assert p.match("https://docs.foo.com/llms-full.txt#top")
    assert p.match("https://docs.foo.com/LLMS-FULL.TXT")
    assert p.match("https://docs.foo.com/llms.txt?v=2")
    assert not p.match("https://docs.foo.com/guide?next=llms-full.txt")


def test_full_body_branch_survives_a_query_string(tmp_path, monkeypatch):
    """A suffixed llms-full.txt URL fell into the index branch, which filtered
    every .md link away and raised EmptyOutputError."""
    body = "# Docs\n\nAll the documentation, inlined.\n"
    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: (url, body))

    acq = LlmsTxtPattern().acquire("https://docs.foo.com/llms-full.txt?v=2", tmp_path)

    assert acq.pages == 1
    assert acq.single_document
    staged = "".join(p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*")))
    assert "All the documentation, inlined." in staged


def test_section_prefix_drops_query_and_fragment(monkeypatch, tmp_path):
    """A section base pasted with a tracking query filtered every .md link away
    (nothing starts with the query'd prefix), staging 0 pages."""
    index = "- [A](https://docs.foo.com/guide/a.md)\n- [B](https://docs.foo.com/other/b.md)\n"
    fetched: list = []

    def fake_fetch(url, **kw):
        fetched.append(url)
        return url, index if url.endswith("llms.txt") else "body"

    monkeypatch.setattr(http, "fetch_text", fake_fetch)
    acq = LlmsTxtPattern().acquire("https://docs.foo.com/guide?utm_source=x#top", tmp_path)

    assert acq.pages == 1, "the query'd prefix filtered every link away"
    assert "https://docs.foo.com/guide/a.md" in fetched


def test_section_filter_stops_at_a_path_boundary(tmp_path, monkeypatch):
    """A raw string prefix let a /guide seed absorb every sibling section whose
    path merely starts with it, while the manifest claimed only /guide."""
    index = (
        "- [A](https://docs.foo.com/guide/a.md)\n- [B](https://docs.foo.com/guide-advanced/b.md)\n"
    )
    fetched: list = []

    def fake_fetch(url, **kw):
        fetched.append(url)
        return url, index if url.endswith("llms.txt") else "body"

    monkeypatch.setattr(http, "fetch_text", fake_fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    acq = LlmsTxtPattern().acquire("https://docs.foo.com/guide", tmp_path)

    assert acq.pages == 1, "a sibling section was absorbed"
    assert "https://docs.foo.com/guide-advanced/b.md" not in fetched


def test_section_filter_ignores_host_case(tmp_path, monkeypatch):
    """An uppercase-host seed routes here but matched no lowercase .md link,
    staging 0 pages and failing later as 'the source may have changed shape'."""
    index = "- [A](https://docs.claude.com/en/docs/claude-code/a.md)\n"

    def fake_fetch(url, **kw):
        return url, index if url.lower().endswith("llms.txt") else "body"

    monkeypatch.setattr(http, "fetch_text", fake_fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    acq = LlmsTxtPattern().acquire("https://Docs.Claude.com/en/docs/claude-code", tmp_path)

    assert acq.pages == 1, "the host's case filtered every link away"
    assert acq.slug == "claude-code"


def test_deep_path_llms_full_slug_folds_in_the_host(tmp_path, monkeypatch):
    """/docs/llms-full.txt is a common layout, so the last path segment alone
    put two vendors on the slug 'docs'."""
    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: (url, _FULL))
    p = LlmsTxtPattern()

    a = p.acquire("https://acme.com/docs/llms-full.txt", tmp_path / "a")
    b = p.acquire("https://other.com/docs/llms-full.txt", tmp_path / "b")

    assert a.slug == "acme-com-docs", f"host-qualified slug expected, got {a.slug!r}"
    assert b.slug == "other-com-docs"
    assert a.pages == 1 and a.single_document is True


def test_uppercase_llms_full_takes_the_body_branch(tmp_path, monkeypatch):
    """Case reaches past match(): the acquire branch decides whether the body IS
    the deliverable or gets link-scraped away."""
    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: (url, _FULL))

    acq = LlmsTxtPattern().acquire("https://docs.acme.com/EN/LLMS-FULL.TXT", tmp_path)

    assert acq.pages == 1
    assert acq.single_document is True
    assert acq.slug.lower() == "docs-acme-com-en", f"unexpected slug {acq.slug!r}"
    staged = "".join(f.read_text(encoding="utf-8") for f in sorted(acq.raw_dir.glob("*")))
    assert "Everything about Acme, inlined." in staged


def test_match_requires_the_whole_llms_basename(monkeypatch):
    """Suffix matching claimed any file ending in the name, and acquire then
    read that vendor file as if it were the llms.txt index."""
    p = LlmsTxtPattern()
    assert p.match("https://x.com/llms.txt")
    assert p.match("https://x.com/en/llms-full.txt")
    assert not p.match("https://x.com/vendor-llms.txt")
    assert not p.match("https://x.com/allms.txt")
    assert not p.match("https://x.com/my-llms-full.txt")


def test_normalize_strips_platform_preambles_and_absolutizes_links(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "0000-intro.md").write_text(
        "<!-- source: https://docs.vendor.example/docs/intro.md -->\n\n"
        "> This is a page from the Vendor documentation. For a complete page index, fetch "
        "https://docs.vendor.example/docs/llms.txt.\n\n"
        "# Intro\n\nSee [Setup](./setup) and [Pricing](/pricing).\n",
        encoding="utf-8",
    )
    acq = AcquireResult(raw_dir=raw, kind="markdown", slug="vendor", pages=1)

    text = LlmsTxtPattern().normalize(acq, tmp_path).read_text(encoding="utf-8")

    assert "For a complete page index" not in text
    assert "[Setup](https://docs.vendor.example/docs/setup)" in text
    assert "[Pricing](https://docs.vendor.example/pricing)" in text


def test_llms_full_leaves_page_relative_links_unresolved(tmp_path):
    """The file's URL is not any section's page URL, so resolving against it
    would point a section's relative link at the wrong page."""
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "0000-llms-full.md").write_text(
        "<!-- source: https://docs.vendor.example/llms-full.txt -->\n\n"
        "# Install\n\nNext: [setup](setup). See [Pricing](/pricing).\n",
        encoding="utf-8",
    )
    acq = AcquireResult(raw_dir=raw, kind="markdown", slug="vendor", pages=1, single_document=True)

    text = LlmsTxtPattern().normalize(acq, tmp_path).read_text(encoding="utf-8")

    assert "[setup](setup)" in text
    assert "[Pricing](https://docs.vendor.example/pricing)" in text
