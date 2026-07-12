"""gitbook — match + acquire/normalize over a synthetic GitBook (no network).

Reproduces GitBook's double-encoded ``~gitbook/image`` proxy so the
``/files/<id>`` → real-URL resolution is exercised for real.
"""

import urllib.parse

from pagespring import http
from pagespring.patterns.gitbook import GitBookPattern

# A Firebase-style asset URL (its path is %2F-encoded), as it appears decoded.
_RAW_IMG = "https://files.example.com/o/assets%2Fspc%2Fabc123%2Fpic.png?alt=media"
# In the rendered HTML it's wrapped in the image proxy, URL-encoded again.
_PROXY = "/~gitbook/image?url=" + urllib.parse.quote(_RAW_IMG, safe="") + "&width=768"

_LLMS = "https://docs.x.com/intro.md\nhttps://docs.x.com/setup.md\n"
# Footer in GitBook's CURRENT (2026) format: a standalone "# Agent Instructions"
# heading + "## Querying This Documentation" subsection, preceded by a --- rule.
_INTRO_MD = (
    "> For the complete documentation index, see [llms.txt](https://docs.x.com/llms.txt). "
    "Markdown versions of documentation pages are available by appending `.md` to page "
    "URLs; this page is available as [Markdown](https://docs.x.com/intro.md).\n\n"
    "# Intro\n\n"
    "![diagram](/files/abc123)\n\n"
    "See the [setup guide](/setup) for details.\n\n"
    "---\n\n"
    "# Agent Instructions\n"
    "This documentation is published with GitBook. Learn more at gitbook.com.\n\n"
    "## Querying This Documentation\n"
    "Use ?ask= to query this docs site.\n\n---\n"
)
_INTRO_HTML = f'<html><body><img src="{_PROXY}"></body></html>'
_SETUP_MD = "# Setup\n\nInstall it.\n"
_SETUP_HTML = "<html><body>no images here</body></html>"


def _fake_fetch_text(url, **kwargs):
    table = {
        "https://docs.x.com/llms.txt": _LLMS,
        "https://docs.x.com/intro.md": _INTRO_MD,
        "https://docs.x.com/intro": _INTRO_HTML,
        "https://docs.x.com/setup.md": _SETUP_MD,
        "https://docs.x.com/setup": _SETUP_HTML,
    }
    return url, table[url]


def test_match():
    p = GitBookPattern()
    assert p.match("https://acme.gitbook.io/handbook")
    # docs.* custom domains now route via docs_probe's llms.txt sniff instead.
    assert not p.match("https://docs.tableplus.com")
    assert not p.match("https://github.com/x/y")


def test_strip_banner_both_variants():
    from pagespring.patterns._gitbook import strip_banner

    new = (
        "> For the complete documentation index, see [llms.txt](https://d.x.com/llms.txt). "
        "Markdown versions of documentation pages are available by appending `.md` "
        "to page URLs; this page is available as [Markdown](https://d.x.com/master.md).\n"
        "\n# Overview\n\nBody.\n"
    )
    old = (
        "> ## Documentation Index\n"
        "> Fetch the complete documentation index at: https://d.x.com/llms.txt\n"
        "> Use this file to discover all available pages before exploring further.\n"
        "\n# Overview\n\nBody.\n"
    )
    for md in (new, old):
        out = strip_banner(md)
        assert "llms.txt" not in out
        assert out.lstrip().startswith("# Overview")
    # A doc's own blockquote that merely mentions llms.txt is NOT chrome.
    keep = "# Intro\n\n> Note: publish an llms.txt file for AI agents.\n"
    assert strip_banner(keep) == keep


def test_strip_banner_keeps_adjacent_content_blockquote():
    """A legit content blockquote directly abutting the banner (no blank line
    between) must survive — the single-paragraph banner is one line, and its
    strip must not swallow the following blockquote."""
    from pagespring.patterns._gitbook import strip_banner

    md = (
        "> For the complete documentation index, see [llms.txt](https://d.x.com/llms.txt). "
        "Markdown versions of documentation pages are available by appending `.md` "
        "to page URLs; this page is available as [Markdown](https://d.x.com/m.md).\n"
        "> **Note:** back up your config before upgrading.\n"
        "\n# Overview\n\nBody.\n"
    )
    out = strip_banner(md)
    assert "For the complete documentation index" not in out
    assert "back up your config before upgrading" in out
    assert out.lstrip().startswith("> **Note:**")


def test_strip_footer_legacy_single_heading_format():
    """The pre-2026 footer (one combined heading) must still strip."""
    from pagespring.patterns._gitbook import strip_footer

    md = (
        "# Intro\n\nBody.\n\n---\n\n"
        "## Agent Instructions: Querying This Documentation\n\nUse ?ask=.\n"
    )
    out = strip_footer(md)
    assert "Agent Instructions" not in out
    assert out.endswith("Body.")


def test_acquire_resolves_images_strips_footer_absolutizes(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = GitBookPattern()

    acq = p.acquire("https://docs.x.com", tmp_path)
    assert acq.kind == "markdown"
    assert acq.slug == "x"
    assert acq.pages == 2

    text = p.normalize(acq, tmp_path).read_text(encoding="utf-8")
    # /files/<id> resolved to the real downloadable URL pulled from the HTML proxy.
    assert "/files/abc123" not in text
    assert _RAW_IMG in text
    # GitBook's agent-instructions footer stripped — including the trailing ---
    # rule and the "published with GitBook" blurb.
    assert "Agent Instructions" not in text
    assert "Querying This Documentation" not in text
    assert "gitbook.com" not in text
    # The leading "documentation index" banner is stripped from every page.
    assert "For the complete documentation index" not in text
    # Root-relative link absolutized to the origin.
    assert "(https://docs.x.com/setup)" in text
    # Both pages present, in llms.txt order.
    assert text.index("# Intro") < text.index("# Setup")
