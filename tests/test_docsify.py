"""_docsify — raw markdown via the sidebar file a Docsify site loads (mocked fetch)."""

from urllib.error import HTTPError

import pytest

from pagespring import http
from pagespring.patterns import _docsify
from pagespring.patterns.docs_probe import DocsProbePattern

_ENTRY = "https://ex.github.io/proj"
_ROOT = "https://ex.github.io/proj/"


def _index(config: str, *, runtime: str = "//cdn.jsdelivr.net/npm/docsify@4") -> str:
    return (
        '<!DOCTYPE html><html><head><title>Ex docs</title></head><body><div id="app"></div>'
        f"<script>\n  window.$docsify = {{\n{config}\n  }}\n</script>"
        f'<script src="{runtime}"></script></body></html>'
    )


def _serve(monkeypatch, files: dict[str, object]) -> list[str]:
    """Serve ``files``: a body, an ``(final_url, body)`` redirect, or an exception."""
    seen: list[str] = []

    def fetch(url, **kwargs):
        seen.append(url)
        if url not in files:
            raise HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
        body = files[url]
        if isinstance(body, Exception):
            raise body
        if isinstance(body, tuple):
            return body
        return url, body

    monkeypatch.setattr(http, "fetch_text", fetch)
    return seen


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)


def _staged(acq) -> list[str]:
    return [p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.md"))]


def _sources(acq) -> list[str]:
    return [t.split(" -->", 1)[0].removeprefix("<!-- source: ") for t in _staged(acq)]


_CONFIG = """\
    name: '<img src="logo.png"> <b>Ex</b>',
    loadSidebar: true,
    // routerMode: 'history',
    /* basePath: '/elsewhere/', */
    alias: {
      '.*?/changelog': 'https://raw.test/ex/main/CHANGELOG.md',
      '/old/(.*)': '/new/$1',
    },"""

_SIDEBAR = """<!-- docs/_sidebar.md -->
- Getting started
  - [Quick start](quickstart.md)
  - [Guide](guide/ "The guide")
  - [Options](#/configuration?id=options)
  - [Deep](usage/deep)
- [Changelog](changelog.md)
- [Old page](/old/page)
- [Quick start again](quickstart.md#install)
- [GitHub](https://github.com/ex/ex)
- <a style="display:inline" href="/extra">Extra</a> <sup>NEW</sup>
"""

_SITE = {
    _ENTRY: (_ROOT, _index(_CONFIG)),
    f"{_ROOT}_sidebar.md": _SIDEBAR,
    f"{_ROOT}README.md": "# Ex\n\nWelcome.\n",
    f"{_ROOT}quickstart.md": "# Quick start\n\nInstall it.\n",
    f"{_ROOT}guide/README.md": "# Guide\n\nGuide body.\n",
    f"{_ROOT}configuration.md": "# Configuration\n\n## Options\n\nSet them.\n",
    f"{_ROOT}usage/deep.md": "# Deep\n\nSee [Sections](usage/sections.md) and ![shot](img/s.png).\n",
    "https://raw.test/ex/main/CHANGELOG.md": "# Changelog\n\n1.0 shipped.\n",
    f"{_ROOT}new/page.md": "Old page body.\n",
    f"{_ROOT}extra.md": "# Extra\n\nExtra body.\n",
}


def _acquire(tmp_path, monkeypatch, files=None, entry=_ENTRY, title="Ex docs"):
    seen = _serve(monkeypatch, _SITE if files is None else files)
    acq = _docsify.acquire(entry, tmp_path, slug="ex", title=title)
    return acq, seen


# --- the tell ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        "//cdn.jsdelivr.net/npm/docsify@4",
        "//unpkg.com/docsify/lib/docsify.min.js",
        "//cdn.jsdelivr.net/npm/docsify@5/dist/docsify.min.js",
        "lib/docsify.js",
    ],
)
def test_the_docsify_runtime_script_is_the_tell(src):
    assert _docsify.is_docsify(_index("loadSidebar: true", runtime=src))


@pytest.mark.parametrize(
    "html",
    [
        '<html><body><script src="//cdn.jsdelivr.net/npm/docsify-plugin-carbon@1"></script>'
        "</body></html>",
        "<html><body><pre><code>window.$docsify = { loadSidebar: true }\n"
        '&lt;script src="//cdn.jsdelivr.net/npm/docsify@4"&gt;&lt;/script&gt;</code></pre>'
        "</body></html>",
        '<html><head><meta name="generator" content="VitePress v1.6.4"></head>'
        "<body><p>Unlike docsify, this site is prerendered.</p></body></html>",
    ],
)
def test_pages_that_only_mention_docsify_are_not_claimed(html):
    assert not _docsify.is_docsify(html)


# --- discovery and order ---------------------------------------------------------------


def test_pages_stage_in_sidebar_order_after_an_unlisted_homepage(tmp_path, monkeypatch):
    acq, _seen = _acquire(tmp_path, monkeypatch)

    assert _sources(acq) == [
        f"{_ROOT}README.md",
        f"{_ROOT}quickstart.md",
        f"{_ROOT}guide/README.md",
        f"{_ROOT}configuration.md",
        f"{_ROOT}usage/deep.md",
        "https://raw.test/ex/main/CHANGELOG.md",
        f"{_ROOT}new/page.md",
        f"{_ROOT}extra.md",
    ]
    assert acq.kind == "markdown"
    assert acq.pages == 8
    assert acq.lost == 0
    assert acq.truncated is False
    assert acq.single_document is False


def test_each_file_is_fetched_once_and_external_links_never(tmp_path, monkeypatch):
    _acquire_result, seen = _acquire(tmp_path, monkeypatch)

    # entry, sidebar, 8 pages, a sidebar probe in each of guide/, usage/ and new/, and
    # the page deep.md links to
    assert len(seen) == len(set(seen)) == 14
    assert f"{_ROOT}usage/sections.md" in seen
    assert not any("github.com" in url for url in seen)


def test_a_listed_homepage_keeps_its_sidebar_position(tmp_path, monkeypatch):
    files = dict(_SITE)
    files[f"{_ROOT}_sidebar.md"] = "- [Quick start](quickstart.md)\n- [Home](/)\n"
    acq, _seen = _acquire(tmp_path, monkeypatch, files)

    assert _sources(acq) == [f"{_ROOT}quickstart.md", f"{_ROOT}README.md"]


def test_the_commented_out_options_are_ignored(tmp_path, monkeypatch):
    """The config comments out history mode and a basePath; honouring either would
    move the root to the origin or /elsewhere/."""
    _acq, seen = _acquire(tmp_path, monkeypatch)

    assert seen[1] == f"{_ROOT}_sidebar.md"


@pytest.mark.parametrize(
    ("entry", "config", "sidebar", "home"),
    [
        (
            "https://ex.test",
            "loadSidebar: true, basePath: '/docs/'",
            "https://ex.test/docs/_sidebar.md",
            "https://ex.test/docs/README.md",
        ),
        (
            "https://ex.test",
            "loadSidebar: true, basePath: 'https://raw.test/ex/main/docs/'",
            "https://raw.test/ex/main/docs/_sidebar.md",
            "https://raw.test/ex/main/docs/README.md",
        ),
        (
            _ENTRY,
            "loadSidebar: true, basePath: 'docs/'",
            f"{_ROOT}docs/_sidebar.md",
            f"{_ROOT}docs/README.md",
        ),
        (
            "https://ex.test/getting-started",
            "loadSidebar: true, routerMode: 'history'",
            "https://ex.test/_sidebar.md",
            "https://ex.test/README.md",
        ),
        (
            "https://ex.test/#/quickstart",
            "loadSidebar: 'nav/sidebar.md', homepage: 'intro.md'",
            "https://ex.test/nav/sidebar.md",
            "https://ex.test/intro.md",
        ),
        (
            "https://ex.test/site/index.html",
            "loadSidebar: true",
            "https://ex.test/site/_sidebar.md",
            "https://ex.test/site/README.md",
        ),
    ],
)
def test_the_config_places_the_sidebar_and_homepage(
    tmp_path, monkeypatch, entry, config, sidebar, home
):
    page = entry.split("#", 1)[0]
    files = {page: _index(config), sidebar: "- [Home](/)\n", home: "# Home\n\nHi.\n"}
    acq, seen = _acquire(tmp_path, monkeypatch, files, entry=entry)

    assert seen == [page, sidebar, home]
    assert _sources(acq) == [home]


def test_no_sidebar_stages_the_homepage_as_the_whole_document(tmp_path, monkeypatch):
    files = {_ENTRY: (_ROOT, _index("name: 'Ex'")), f"{_ROOT}README.md": "# Ex\n\nAll of it.\n"}
    acq, seen = _acquire(tmp_path, monkeypatch, files)

    assert acq.pages == 1
    assert acq.single_document is True
    assert f"{_ROOT}_sidebar.md" in seen


def test_a_sidebar_turned_off_is_not_fetched(tmp_path, monkeypatch):
    files = {
        _ENTRY: (_ROOT, _index("loadSidebar: false")),
        f"{_ROOT}README.md": "# Ex\n\nAll of it.\n",
    }
    acq, seen = _acquire(tmp_path, monkeypatch, files)

    assert seen == [_ENTRY, f"{_ROOT}README.md"]
    assert acq.single_document is True


def test_a_sidebar_the_server_fails_to_serve_is_an_error(tmp_path, monkeypatch):
    files = dict(_SITE)
    files[f"{_ROOT}_sidebar.md"] = HTTPError(f"{_ROOT}_sidebar.md", 503, "Down", {}, None)  # type: ignore[arg-type]
    with pytest.raises(HTTPError):
        _acquire(tmp_path, monkeypatch, files)


def test_a_directory_sidebar_adds_its_pages_after_the_homepage(tmp_path, monkeypatch):
    files = dict(_SITE)
    files[f"{_ROOT}_sidebar.md"] = "- [Guide](guide/)\n"
    files[f"{_ROOT}guide/_sidebar.md"] = "- [Home](/)\n- [Deep](usage/deep)\n"
    acq, _seen = _acquire(tmp_path, monkeypatch, files)

    assert _sources(acq) == [
        f"{_ROOT}README.md",
        f"{_ROOT}guide/README.md",
        f"{_ROOT}usage/deep.md",
    ]


def test_a_sidebar_answered_by_the_app_shell_counts_as_absent(tmp_path, monkeypatch):
    files = dict(_SITE)
    files[f"{_ROOT}_sidebar.md"] = "<!DOCTYPE html><html><body><div id='app'></div></body></html>"
    acq, _seen = _acquire(tmp_path, monkeypatch, files)

    assert _sources(acq) == [f"{_ROOT}README.md"]


# --- page content ---------------------------------------------------------------------


def _page(acq, name: str) -> str:
    return next(t for t in _staged(acq) if f"source: {_ROOT}{name} -->" in t)


def test_links_resolve_to_the_files_they_route_to(tmp_path, monkeypatch):
    acq, _seen = _acquire(tmp_path, monkeypatch)

    assert f"[Sections]({_ROOT}usage/sections.md)" in _page(acq, "usage/deep.md")


def test_images_resolve_against_the_page_route_directory(tmp_path, monkeypatch):
    acq, _seen = _acquire(tmp_path, monkeypatch)

    assert f"![shot]({_ROOT}usage/img/s.png)" in _page(acq, "usage/deep.md")


def test_relative_path_mode_resolves_links_against_the_page(tmp_path, monkeypatch):
    files = dict(_SITE)
    files[_ENTRY] = (_ROOT, _index("loadSidebar: true, relativePath: true"))
    files[f"{_ROOT}_sidebar.md"] = "- [Deep](usage/deep.md)\n"
    files[f"{_ROOT}usage/deep.md"] = "# Deep\n\n[Sections](sections.md)\n"
    acq, _seen = _acquire(tmp_path, monkeypatch, files)

    assert f"[Sections]({_ROOT}usage/sections.md)" in _page(acq, "usage/deep.md")


_LINKS_PAGE = """# Links

[abs](https://other.test/x) [anchor](#local) [route](#/configuration?id=options)
[dir](guide/) [home](/) [file](files/ex.zip) [bare](quickstart) [`fn()`](guide/?id=fn)
![root image](/img/a.png) <img src="img/b.png" width="40"> <a href="quickstart.md">qs</a>

```js
const x = "[not](a-link.md)"
```

Inline `[code](span.md)` stays.
"""


@pytest.mark.parametrize(
    "expected",
    [
        "[abs](https://other.test/x)",
        "[anchor](#local)",
        f"[route]({_ROOT}configuration.md#options)",
        f"[dir]({_ROOT}guide/README.md)",
        f"[home]({_ROOT}README.md)",
        f"[file]({_ROOT}files/ex.zip)",
        f"[bare]({_ROOT}quickstart.md)",
        f"[`fn()`]({_ROOT}guide/README.md#fn)",
        f"![root image]({_ROOT}img/a.png)",
        f'<img src="{_ROOT}img/b.png" width="40">',
        f'<a href="{_ROOT}quickstart.md">qs</a>',
        'const x = "[not](a-link.md)"',
        "`[code](span.md)`",
    ],
)
def test_link_forms_resolve_and_code_is_left_alone(tmp_path, monkeypatch, expected):
    files = dict(_SITE)
    files[f"{_ROOT}_sidebar.md"] = "- [Links](links.md)\n"
    files[f"{_ROOT}links.md"] = _LINKS_PAGE
    acq, _seen = _acquire(tmp_path, monkeypatch, files)

    assert expected in _page(acq, "links.md")


def test_a_page_without_a_title_takes_its_sidebar_label(tmp_path, monkeypatch):
    acq, _seen = _acquire(tmp_path, monkeypatch)

    assert "\n\n# Old page\n\nOld page body." in _page(acq, "new/page.md")
    assert _page(acq, "quickstart.md").count("# Quick start") == 1


def test_a_title_below_a_banner_still_counts(tmp_path, monkeypatch):
    files = dict(_SITE)
    files[f"{_ROOT}quickstart.md"] = "![banner](banner.png)\n\n# Quick start\n\nGo.\n"
    acq, _seen = _acquire(tmp_path, monkeypatch, files)

    assert _page(acq, "quickstart.md").count("# Quick start") == 1


def test_a_heading_inside_code_is_not_a_title(tmp_path, monkeypatch):
    files = dict(_SITE)
    files[f"{_ROOT}quickstart.md"] = "Go.\n\n```md\n# Not a title\n```\n"
    acq, _seen = _acquire(tmp_path, monkeypatch, files)

    assert "\n\n# Quick start\n\nGo." in _page(acq, "quickstart.md")


def test_an_html_heading_counts_as_a_title(tmp_path, monkeypatch):
    files = dict(_SITE)
    files[f"{_ROOT}quickstart.md"] = '<h1>Quick <img src="https://b.test/v.svg"/></h1>\n\nGo.\n'
    acq, _seen = _acquire(tmp_path, monkeypatch, files)

    assert "# Quick start" not in _page(acq, "quickstart.md")


def test_docsify_callouts_become_blockquotes_outside_code(tmp_path, monkeypatch):
    files = dict(_SITE)
    files[f"{_ROOT}quickstart.md"] = (
        "# Quick start\n\n!> Careful here.\n\n?> A tip.\n\n```md\n!> shown as written\n```\n"
    )
    acq, _seen = _acquire(tmp_path, monkeypatch, files)
    page = _page(acq, "quickstart.md")

    assert "\n> Careful here.\n" in page
    assert "\n> A tip.\n" in page
    assert "\n!> shown as written\n" in page


# --- embedded files ------------------------------------------------------------------

_DEMO = "https://raw.test/ex/demo/1-basic.ts"


def _embeds(body: str, extra: dict[str, object] | None = None) -> dict[str, object]:
    files = dict(_SITE)
    files[f"{_ROOT}_sidebar.md"] = "- [Embeds](usage/embeds.md)\n"
    files[f"{_ROOT}usage/embeds.md"] = f"# Embeds\n\n{body}"
    files.update(extra or {})
    return files


def test_a_code_include_becomes_a_fenced_block_in_its_language(tmp_path, monkeypatch):
    files = _embeds(
        f'Before.\n[Example]({_DEMO} ":include")\n\n## After\n', {_DEMO: "const a = `x`;\n"}
    )
    acq, _seen = _acquire(tmp_path, monkeypatch, files)
    page = _page(acq, "usage/embeds.md")

    assert "Before.\n\n```ts\nconst a = `x`;\n```\n\n## After" in page
    assert _DEMO not in page


def test_a_markdown_include_is_inlined_and_resolved_like_its_page(tmp_path, monkeypatch):
    part = "Part text, see [Quick start](quickstart.md).\n\n![p](img/p.png)\n"
    files = _embeds("[part](_media/part.md ':include')\n", {f"{_ROOT}usage/_media/part.md": part})
    acq, _seen = _acquire(tmp_path, monkeypatch, files)
    page = _page(acq, "usage/embeds.md")

    assert f"Part text, see [Quick start]({_ROOT}quickstart.md)." in page
    assert f"![p]({_ROOT}usage/img/p.png)" in page
    assert "_media/part.md" not in page


def test_a_fragment_include_keeps_only_the_marked_lines(tmp_path, monkeypatch):
    source = "import x;\n/// [demo]\n    run();\n      go();\n/// [demo]\nrest();\n"
    files = _embeds(f"[Ex]({_DEMO} ':include :type=code :fragment=demo')\n", {_DEMO: source})
    acq, _seen = _acquire(tmp_path, monkeypatch, files)
    page = _page(acq, "usage/embeds.md")

    assert "```ts\nrun();\n  go();\n```" in page
    assert "import x" not in page


def test_an_included_file_holding_fences_gets_a_longer_fence(tmp_path, monkeypatch):
    part = "# Part\n\n```js\nx();\n```\n"
    files = _embeds("[part](part.md ':include :type=code')\n", {f"{_ROOT}usage/part.md": part})
    acq, _seen = _acquire(tmp_path, monkeypatch, files)

    assert "````md\n# Part\n\n```js\nx();\n```\n````" in _page(acq, "usage/embeds.md")


def test_includes_in_code_and_media_includes_are_not_fetched(tmp_path, monkeypatch):
    body = (
        f"```md\n[Ex]({_DEMO} ':include')\n```\n\n"
        "[Page](https://raw.test/ex/page.html ':include')\n\n[Clip](clip.mp4 ':include')\n"
    )
    acq, seen = _acquire(tmp_path, monkeypatch, _embeds(body, {_DEMO: "x"}))
    page = _page(acq, "usage/embeds.md")

    assert not any(u.startswith("https://raw.test/") or u.endswith("clip.mp4") for u in seen)
    assert f"[Ex]({_DEMO} ':include')\n```" in page
    assert "[Page](https://raw.test/ex/page.html ':include')" in page
    assert f"[Clip]({_ROOT}usage/clip.mp4 ':include')" in page


def test_an_include_that_fails_to_load_stays_a_link(tmp_path, monkeypatch):
    files = _embeds(
        "[Gone](demo/gone.ts ':include')\n\n[Shell](demo/shell.ts ':include')\n",
        {f"{_ROOT}usage/demo/shell.ts": "<!DOCTYPE html><html><div id='app'></div></html>"},
    )
    acq, _seen = _acquire(tmp_path, monkeypatch, files)
    page = _page(acq, "usage/embeds.md")

    assert acq.pages == 2
    assert acq.lost == 0
    assert f"[Gone]({_ROOT}usage/demo/gone.ts ':include')" in page
    assert f"[Shell]({_ROOT}usage/demo/shell.ts ':include')" in page


def test_an_include_used_on_two_pages_is_fetched_once_and_politely(tmp_path, monkeypatch):
    files = _embeds(f"[Ex]({_DEMO} ':include')\n", {_DEMO: "x();\n"})
    files[f"{_ROOT}_sidebar.md"] = "- [Embeds](usage/embeds.md)\n- [Again](again.md)\n"
    files[f"{_ROOT}again.md"] = f"# Again\n\n[Ex]({_DEMO} ':include')\n"
    events: list[str] = []
    seen = _serve(monkeypatch, files)
    served = http.fetch_text
    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: events.append(url) or served(url))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: events.append("sleep"))
    acq = _docsify.acquire(_ENTRY, tmp_path, slug="ex", title=None)

    assert seen.count(_DEMO) == 1
    assert events[events.index(_DEMO) - 1] == "sleep"
    assert all("```ts\nx();\n```" in page for page in _staged(acq)[1:])


# --- losses, duplicates and caps ------------------------------------------------------


def test_missing_and_shell_pages_count_as_lost(tmp_path, monkeypatch):
    files = dict(_SITE)
    del files[f"{_ROOT}quickstart.md"]
    files[f"{_ROOT}extra.md"] = "<!doctype html><html><body><div id='app'></div></body></html>"
    files[f"{_ROOT}guide/README.md"] = OSError("reset")
    acq, _seen = _acquire(tmp_path, monkeypatch, files)

    assert acq.pages == 5
    assert acq.lost == 3


def test_two_routes_to_one_file_stage_it_once(tmp_path, monkeypatch):
    files = dict(_SITE)
    files[f"{_ROOT}_sidebar.md"] = (
        "- [A](quickstart.md)\n- [B](quickstart)\n- [C](/quickstart.md)\n"
    )
    acq, _seen = _acquire(tmp_path, monkeypatch, files)

    assert _sources(acq) == [f"{_ROOT}README.md", f"{_ROOT}quickstart.md"]


def test_a_page_at_a_long_path_stages_under_a_short_file_name(tmp_path, monkeypatch):
    name = "a" * 300  # past a file name's 255-byte limit
    files = dict(_SITE)
    files[f"{_ROOT}_sidebar.md"] = f"- [Long]({name}.md)\n"
    files[f"{_ROOT}{name}.md"] = "# Long\n\nBody.\n"
    acq, _seen = _acquire(tmp_path, monkeypatch, files)

    assert _sources(acq) == [f"{_ROOT}README.md", f"{_ROOT}{name}.md"]
    assert max(len(p.name) for p in acq.raw_dir.iterdir()) <= 100


def test_the_page_cap_truncates(tmp_path, monkeypatch):
    monkeypatch.setattr(_docsify, "_MAX_PAGES", 3)
    acq, _seen = _acquire(tmp_path, monkeypatch)

    assert acq.pages == 3
    assert acq.truncated is True


def test_every_request_after_the_first_waits_the_polite_delay(tmp_path, monkeypatch):
    events: list[str] = []
    seen = _serve(monkeypatch, _SITE)
    served = http.fetch_text

    def fetch(url, **kwargs):
        events.append("fetch")
        return served(url, **kwargs)

    monkeypatch.setattr(http, "fetch_text", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: events.append("sleep"))
    _docsify.acquire(_ENTRY, tmp_path, slug="ex", title=None)

    assert len(seen) == 14
    fetches = [i for i, e in enumerate(events) if e == "fetch"]
    assert all(events[i - 1] == "sleep" for i in fetches[1:])


# --- title and hand-off ---------------------------------------------------------


def test_the_title_is_the_configured_name_as_text(tmp_path, monkeypatch):
    acq, _seen = _acquire(tmp_path, monkeypatch)

    assert acq.title == "Ex"


def test_without_a_name_the_title_is_the_page_title(tmp_path, monkeypatch):
    files = dict(_SITE)
    files[_ENTRY] = (_ROOT, _index("loadSidebar: true"))
    acq, _seen = _acquire(tmp_path, monkeypatch, files, title="Ex docs")

    assert acq.title == "Ex docs"


def test_normalize_joins_the_pages_in_order(tmp_path, monkeypatch):
    acq, _seen = _acquire(tmp_path, monkeypatch)
    out = DocsProbePattern().normalize(acq, tmp_path).read_text(encoding="utf-8")

    order = ["Welcome.", "Install it.", "Guide body.", "Set them.", "1.0 shipped.", "Extra body."]
    positions = [out.index(text) for text in order]
    assert positions == sorted(positions)
    assert out.count("\n\n---\n\n") == 7


# --- pages linked only from content ------------------------------------------------


def _linked_site(**pages: str) -> dict[str, object]:
    site: dict[str, object] = {
        _ENTRY: (_ROOT, _index("    loadSidebar: true,")),
        f"{_ROOT}_sidebar.md": "- [A](a.md)\n- [B](guide/b.md)\n",
        f"{_ROOT}README.md": "# Home\n\nSee [the translation](zh-cn/) and [a PDF](manual.pdf).\n",
        f"{_ROOT}a.md": "# A\n\nRead [Hidden](hidden.md), then [B](guide/b.md).\n",
        f"{_ROOT}guide/b.md": "# B\n\nMore in [Deeper](deeper.md).\n",
        f"{_ROOT}hidden.md": "Hidden body.\n",
        f"{_ROOT}guide/deeper.md": "# Deeper\n\nDeeper body.\n",
        f"{_ROOT}deeper.md": "# Root deeper\n\nRoot deeper body.\n",
        f"{_ROOT}zh-cn/README.md": "# 首页\n",
        f"{_ROOT}manual.pdf": "%PDF-1.7",
    }
    site.update({f"{_ROOT}{name}": body for name, body in pages.items()})
    return site


def test_a_page_linked_only_from_content_stages_after_the_page_linking_it(tmp_path, monkeypatch):
    acq, _seen = _acquire(tmp_path, monkeypatch, _linked_site())

    assert _sources(acq) == [
        f"{_ROOT}README.md",
        f"{_ROOT}a.md",
        f"{_ROOT}hidden.md",
        f"{_ROOT}guide/b.md",
        f"{_ROOT}deeper.md",
    ]
    assert _staged(acq)[2].endswith("# Hidden\n\nHidden body.\n")
    assert acq.pages == 5


def test_content_links_outside_the_sidebars_directories_are_not_followed(tmp_path, monkeypatch):
    """A README's link to its translation would otherwise pull in the whole other language."""
    _acq, seen = _acquire(tmp_path, monkeypatch, _linked_site())

    assert f"{_ROOT}zh-cn/README.md" not in seen
    assert f"{_ROOT}manual.pdf" not in seen


def test_links_in_code_are_not_followed(tmp_path, monkeypatch):
    site = _linked_site(**{"a.md": "# A\n\nWrite `[x](hidden.md)`:\n\n```\n[x](hidden.md)\n```\n"})
    _acq, seen = _acquire(tmp_path, monkeypatch, site)

    assert f"{_ROOT}hidden.md" not in seen


def test_a_dead_content_link_is_not_a_lost_page(tmp_path, monkeypatch):
    site = _linked_site(**{"a.md": "# A\n\nSee [Gone](gone.md) and [Shell](shell.md).\n"})
    site[f"{_ROOT}shell.md"] = _index("")
    acq, seen = _acquire(tmp_path, monkeypatch, site)

    assert f"{_ROOT}gone.md" in seen
    assert acq.lost == 0


def test_an_include_is_embedded_not_staged_as_a_page(tmp_path, monkeypatch):
    site = _linked_site(
        **{"a.md": "# A\n\n[snippet](snippet.md ':include')\n", "snippet.md": "Snippet text.\n"}
    )
    acq, _seen = _acquire(tmp_path, monkeypatch, site)

    assert f"{_ROOT}snippet.md" not in _sources(acq)
    assert "Snippet text." in _page(acq, "a.md")


def test_a_dead_content_link_the_host_refuses_is_not_a_lost_page(tmp_path, monkeypatch):
    """S3 and CloudFront answer a missing key with 403."""
    site = _linked_site(**{"a.md": "# A\n\nSee [Gone](gone.md).\n"})
    site[f"{_ROOT}gone.md"] = HTTPError(f"{_ROOT}gone.md", 403, "Forbidden", {}, None)  # type: ignore[arg-type]
    acq, seen = _acquire(tmp_path, monkeypatch, site)

    assert f"{_ROOT}gone.md" in seen
    assert acq.lost == 0
