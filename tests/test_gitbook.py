"""gitbook — match + acquire/normalize over a synthetic GitBook (no network).

Reproduces GitBook's double-encoded ``~gitbook/image`` proxy so the
``/files/<id>`` → real-URL resolution is exercised for real.
"""

import urllib.parse

import pytest

from pagespring import http
from pagespring.patterns import _gitbook
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


def test_anchor_links_ending_in_md_are_not_pages(tmp_path, monkeypatch):
    """Stripe's llms.txt carries anchors like
    `/api/payment_intents/create#create_payment_intent-payment_method_types.md`.
    The `.md` is in the FRAGMENT, not the path: it is a link into a page already
    listed, and urlparse().path then yields a stem with no .md — so acquire
    counted the file and normalize's *.md glob dropped it (473 staged, 471 shipped)."""
    llms = (
        "- [A](https://ex.com/a.md)\n"
        "- [Anchor](https://ex.com/a#section-one.md)\n"
        "- [B](https://ex.com/b.md)\n"
    )

    def fake_fetch(url, **kw):
        if url.endswith("/llms.txt"):
            return url, llms
        return url, f"# Page\n\nBody of {url}\n"

    monkeypatch.setattr(http, "fetch_text", fake_fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    p = GitBookPattern()
    acq = p.acquire("https://ex.com", tmp_path)
    out = p.normalize(acq, tmp_path).read_text(encoding="utf-8")

    assert "#section-one" not in out, "an in-page anchor was fetched as its own page"
    assert acq.pages == 2, f"expected 2 real pages, got {acq.pages}"
    assert out.count("<!-- source:") == acq.pages, (
        f"manifest will claim {acq.pages} pages but {out.count('<!-- source:')} are in the file"
    )


class _LogSpy:
    def __init__(self):
        self.warnings = []

    def warning(self, event, **kw):
        self.warnings.append((event, kw))

    def info(self, *a, **kw):
        pass


def test_pages_lost_to_md_fetch_errors_are_counted(tmp_path, monkeypatch):
    """Throttling drops pages one at a time; uncounted, the manifest reports a
    complete crawl and audit sees nothing missing."""

    def fake(url, **kwargs):
        if url == "https://docs.x.com/setup.md":
            raise OSError("503 throttled")
        return _fake_fetch_text(url, **kwargs)

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = GitBookPattern().acquire("https://docs.x.com", tmp_path)

    assert acq.pages == 1
    assert acq.lost == 1


def test_rendered_page_failure_loses_images_not_the_page(tmp_path, monkeypatch):
    """The .md carries the text, so a failed rendered-page fetch costs only the
    image resolution — the page still ships, and the loss is logged not swallowed."""
    from pagespring.patterns import gitbook as mod

    def fake(url, **kwargs):
        if url == "https://docs.x.com/intro":
            raise OSError("500")
        return _fake_fetch_text(url, **kwargs)

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    spy = _LogSpy()
    monkeypatch.setattr(mod, "log", spy)

    acq = GitBookPattern().acquire("https://docs.x.com", tmp_path)

    assert acq.pages == 2
    assert acq.lost == 0
    assert any(event == "gitbook.render_fetch_error" for event, _ in spy.warnings)


def test_acquire_without_rendered_pages_fetches_markdown_only(tmp_path, monkeypatch):
    fetched = []

    def fake(url, **kwargs):
        fetched.append(url)
        return url, _LLMS if url.endswith("/llms.txt") else "# Page\n\nbody\n"

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = GitBookPattern().acquire("https://docs.x.com", tmp_path, rendered=False)

    assert fetched == [
        "https://docs.x.com/llms.txt",
        "https://docs.x.com/intro.md",
        "https://docs.x.com/setup.md",
    ]
    assert acq.pages == 2


def test_an_index_over_the_page_cap_is_truncated(tmp_path, monkeypatch):
    from pagespring.patterns import gitbook as mod

    monkeypatch.setattr(mod, "_MAX_PAGES", 1)
    monkeypatch.setattr(
        http, "fetch_text", lambda url, **k: (url, _LLMS if url.endswith("/llms.txt") else "# P\n")
    )
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = GitBookPattern().acquire("https://docs.x.com", tmp_path, rendered=False)

    assert (acq.pages, acq.truncated) == (1, True)


_CLEAN_PAGE = "# Welcome\n\nBody text.\n"


@pytest.mark.parametrize(
    "preamble",
    [
        "> For clean Markdown of any page, append .md to the page URL.\n"
        "> For a complete documentation index, see https://docs.vendor.example/llms.txt.\n"
        "> For AI client integration (Claude Code, Cursor, etc.), connect to the MCP server.\n\n",
        "> This is a page from the Vendor documentation. For a complete page index, fetch "
        "https://vendor.example/docs/llms.txt. For the full documentation in a single file, "
        "fetch https://vendor.example/docs/llms-full.txt.\n\n",
        "---\nupdatedAt: 2026-09-09T20:47:36.000Z\n---\n\n"
        "Fetch the complete documentation index at: https://docs.vendor.example/main/llms.txt. "
        "Append .md to any documentation page URL to get its markdown version.\n\n"
        "If a page title in the index already matches your question, fetch that page directly, "
        "or query https://docs.vendor.example/main/llms.txt?query=<search-query>.\n\n",
        "> ## Documentation Index\n"
        "> Fetch the complete documentation index at: https://vendor.example/docs/llms.txt\n"
        "> Use this file to discover all available pages before exploring further.\n\n",
    ],
    ids=["fern", "fern-single-line", "readme-front-matter", "mintlify"],
)
def test_strip_boilerplate_drops_platform_agent_preambles(preamble):
    from pagespring.patterns._gitbook import strip_boilerplate

    assert strip_boilerplate(preamble + _CLEAN_PAGE) == _CLEAN_PAGE


def test_strip_boilerplate_keeps_content_that_is_not_a_preamble():
    from pagespring.patterns._gitbook import strip_boilerplate

    source = "<!-- source: https://vendor.example/docs/a.md -->\n\n"
    banner = "> For a complete page index, fetch https://vendor.example/docs/llms.txt.\n\n"
    after_heading = "# Publish\n\nPublish an llms.txt so AI clients can find your pages.\n"
    lead = "Read this first: the API changed in v2.\n\n"

    assert strip_boilerplate(source + banner + after_heading) == source + after_heading
    assert strip_boilerplate(lead + after_heading) == lead + after_heading
    assert strip_boilerplate(after_heading) == after_heading


def test_strip_boilerplate_removes_mdx_definitions_outside_code_fences():
    from pagespring.patterns._gitbook import strip_boilerplate

    md = (
        "# Introduction\n\n"
        'export const TerminalIcon = props => <svg className="h-6 w-6" {...props}>\n'
        '    <path d="M2 3z" />\n'
        "  </svg>;\n\n"
        "import { Card } from '/snippets/card.mdx'\n"
        "Resend is the email API for developers.\n\n"
        "```js\nexport const client = new Resend('re_123');\n```\n\n"
        "export const Unterminated = () => (\n  <div/>\n\n"
        "Kept after the blank line.\n"
    )

    out = strip_boilerplate(md)

    assert "TerminalIcon" not in out and "<path" not in out and "snippets/card" not in out
    assert "Resend is the email API for developers." in out
    assert "export const client = new Resend('re_123');" in out  # code sample, not MDX
    assert "Unterminated" not in out
    assert "Kept after the blank line." in out


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("../docs/command-a", "https://docs.x.com/docs/command-a"),
        ("./setup", "https://docs.x.com/docs/setup"),
        ("guides/intro", "https://docs.x.com/docs/guides/intro"),
        ("/pricing", "https://docs.x.com/pricing"),
        ("#section", "#section"),
        ("mailto:help@x.com", "mailto:help@x.com"),
        ("https://other.example/a", "https://other.example/a"),
    ],
)
def test_absolutize_resolves_page_relative_targets(target, expected):
    from pagespring.patterns._gitbook import absolutize

    out = absolutize(
        f"[link]({target})", "https://docs.x.com", "https://docs.x.com/docs/welcome.md"
    )

    assert out == f"[link]({expected})"


@pytest.mark.parametrize(
    "code",
    [
        "```js\nhandlers[type](event);\nlinks[i](/root);\n```\n",
        "~~~\nfns[i](x);\n~~~\n",
        '````md\n```\nfns[i](x);\n```\nimg[k](y) and <img src="/logo.png">\n````\n',
        "  ```py\n  calls[0](arg)\n  ```\n",
        "Call `fns[i](x)` or ``a`b[c](d)`` then `obj[k](/path)`.\n",
    ],
    ids=["backtick-fence", "tilde-fence", "longer-fence", "indented-fence", "inline-code"],
)
def test_absolutize_leaves_code_untouched(code):
    from pagespring.patterns._gitbook import absolutize

    md = f"[before](guide)\n\n{code}\n[after](/pricing)\n"

    out = absolutize(md, "https://docs.x.com", "https://docs.x.com/docs/welcome.md")

    assert code in out
    assert "[before](https://docs.x.com/docs/guide)" in out
    assert "[after](https://docs.x.com/pricing)" in out


@pytest.mark.parametrize(
    "front_matter",
    [
        "---\ndescription: >-\n  Learn how the CLI\n  talks to the API.\n---\n\n",
        "---\ntitle: CLI\ntags:\n  - cli\n  - setup\n---\n\n",
        '---\n"og:title": CLI reference\n---\n\n',
    ],
    ids=["folded-value", "list", "quoted-key"],
)
def test_strip_boilerplate_drops_multiline_yaml_front_matter(front_matter):
    from pagespring.patterns._gitbook import strip_boilerplate

    assert strip_boilerplate(front_matter + _CLEAN_PAGE) == _CLEAN_PAGE


def test_strip_boilerplate_keeps_a_leading_rule_block_that_is_not_a_mapping():
    from pagespring.patterns._gitbook import strip_boilerplate

    md = "---\n\nSome prose, then a rule.\n\n---\n\n" + _CLEAN_PAGE

    assert strip_boilerplate(md) == md


def test_strip_mdx_definitions_keeps_imports_inside_a_longer_fence():
    from pagespring.patterns._gitbook import strip_mdx_definitions

    md = (
        "# Embedding\n\n"
        "````md\n```jsx\nimport Card from '/snippets/card.mdx'\n```\n"
        "import Tabs from '/snippets/tabs.mdx'\n````\n\n"
        "import Hidden from '/snippets/hidden.mdx'\n"
    )

    out = strip_mdx_definitions(md)

    assert "import Card from '/snippets/card.mdx'" in out
    assert "import Tabs from '/snippets/tabs.mdx'" in out
    assert "Hidden" not in out


def test_strip_mdx_definitions_export_skip_ends_at_an_unindented_element():
    from pagespring.patterns._gitbook import strip_mdx_definitions

    md = 'export const Card = ({title}) => (\n  <div>{title}</div>\n)\n<Card title="Hello" />\n'

    assert strip_mdx_definitions(md) == '<Card title="Hello" />\n'


def test_strip_boilerplate_keeps_leading_prose_that_mentions_llms_txt():
    from pagespring.patterns._gitbook import strip_boilerplate

    md = (
        "Mintlify generates an llms.txt file for every site automatically.\n\n"
        "It lists every page.\n"
    )

    assert strip_boilerplate(md) == md


def test_acquire_resolves_page_relative_links_and_strips_platform_preambles(tmp_path, monkeypatch):
    llms = "https://docs.x.com/docs/welcome.md\n"
    page = (
        "> For a complete documentation index, see https://docs.x.com/llms.txt.\n\n"
        "# Welcome\n\nSee [Command](../docs/command-a).\n"
    )
    monkeypatch.setattr(
        http, "fetch_text", lambda url, **k: (url, llms if url.endswith("/llms.txt") else page)
    )
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = GitBookPattern()

    acq = p.acquire("https://docs.x.com", tmp_path, rendered=False)
    text = p.normalize(acq, tmp_path).read_text(encoding="utf-8")

    assert "llms.txt" not in text
    assert "[Command](https://docs.x.com/docs/command-a)" in text


@pytest.mark.parametrize("marker", ["- ", "* ", "1. ", "2) "])
def test_absolutize_tracks_a_fence_opened_on_a_list_item(marker):
    md = (
        f"{marker}```bash\n  npm i\n  ```\n\nNext: [configure](configure).\n\n"
        "```\nhandlers[type](event);\n```\n\nSee [pricing](/pricing).\n"
    )

    out = _gitbook.absolutize(md, "https://docs.x.com", "https://docs.x.com/guide/start.md")

    assert "[configure](https://docs.x.com/guide/configure)" in out
    assert "[pricing](https://docs.x.com/pricing)" in out
    assert "handlers[type](event);" in out
