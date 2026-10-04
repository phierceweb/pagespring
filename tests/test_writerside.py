"""_writerside — JetBrains Writerside detection and HelpTOC.json-driven acquisition (mocked fetch)."""

import json
import re

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.config import cfg
from pagespring.patterns import _toc_stage, _writerside
from pagespring.patterns.docs_probe import DocsProbePattern

_DIR = "https://docs.example.com/help/prod/"

_HEAD = (
    '<!DOCTYPE html SYSTEM "about:legacy-compat">\n'
    '<html lang="en-US" data-preset="contrast"><head><meta charset="UTF-8">'
    '<meta name="build-number" content="285"><title>{title} | Prod Documentation</title>'
    '<script type="application/json" id="virtual-toc-data">[]</script>'
    '<script type="application/json" id="topic-shortcuts"></script>'
    '<link href="custom-frontend-app/app.css?135" rel="stylesheet"></head>'
)


def _article(title: str, content: str) -> str:
    return (
        _HEAD.format(title=title)
        + f'<body data-id="x" data-main-title="{title}" data-template="article" '
        'data-breadcrumbs="Group///Sub">'
        '<div class="wrapper"><main class="panel _main"><header class="panel__header">'
        '<div class="container"><h3>Prod Help</h3><div class="panel-trigger"></div></div>'
        '</header><section class="panel__content"><div class="container">'
        f'<article class="article"><h1 id="x.topic">{title}</h1>{content}'
        '<div class="last-modified">26 March 2025</div><div data-feedback-placeholder="true"></div>'
        '<div class="navigation-links _bottom"><a class="navigation-links__prev" href="a.html">'
        'Previous page</a><a class="navigation-links__next" href="b.html">Next page</a></div>'
        '</article><div id="disqus_thread"></div></div></section></main></div>'
        '<script src="custom-frontend-app/app.js?135"></script></body></html>'
    )


def _section(title: str, topic: str | None = "starting-page-s.json") -> str:
    topic_attr = f' data-topic="{topic}"' if topic else ""
    return (
        _HEAD.format(title=title)
        + f'<body data-id="s" data-main-title="{title}"{topic_attr} data-template="section-page">'
        '<script src="custom-frontend-app/app.js?135"></script></body></html>'
    )


def _toc(pages: dict[str, dict], top: list[str]) -> str:
    return json.dumps({"entities": {"pages": pages}, "topLevelIds": top})


# Entities are listed out of reading order: only topLevelIds and each node's
# "pages" carry the order.
_TOC = _toc(
    {
        "b": {"id": "b", "title": "B", "url": "b.html", "level": 1, "parentId": "grp"},
        "grp": {"id": "grp", "title": "Guides & more", "level": 0, "pages": ["a", "b"]},
        "welcome": {"id": "welcome", "title": "Welcome", "url": "welcome.html", "level": 0},
        "a": {"id": "a", "title": "A", "url": "a.html", "level": 1, "parentId": "grp"},
        "ext": {"id": "ext", "title": "Elsewhere", "url": "https://other.example.org/x.html"},
        "rootrel": {"id": "rootrel", "title": "Sibling set", "url": "/help/other/x.html"},
    },
    ["welcome", "grp", "ext", "rootrel"],
)

_PAGES = {
    f"{_DIR}HelpTOC.json": _TOC,
    f"{_DIR}welcome.html": _section("Welcome"),
    f"{_DIR}starting-page-s.json": json.dumps(
        {"title": "Prod Documentation", "subtitle": "\n   Prod builds things.\n  ", "tips": []}
    ),
    f"{_DIR}a.html": _article("A", "<p>Alpha body.</p>"),
    f"{_DIR}b.html": _article("B", "<p>Beta body.</p>"),
}


def _serve(monkeypatch, pages, seen=None, finals=None):
    seen = seen if seen is not None else []

    def fetch_text(url, **kwargs):
        seen.append(url)
        if url not in pages:
            raise OSError(f"404 {url}")
        return (finals or {}).get(url, url), pages[url]

    monkeypatch.setattr(http, "fetch_text", fetch_text)
    return seen


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)


def _acquire(tmp_path, base=f"{_DIR}welcome.html", title="Welcome | Prod Documentation"):
    return _writerside.acquire(base.rstrip("/"), tmp_path, slug="example", title=title)


def _staged(acq) -> list[str]:
    return [p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html"))]


def _joined(acq) -> str:
    return "\n".join(_staged(acq))


# --- the tell -----------------------------------------------------------------


def test_recognises_an_article_page():
    assert _writerside.is_writerside(_article("A", "<p>x</p>"))


def test_recognises_a_section_page_shell_with_no_content_and_a_custom_frontend():
    """A starting page is a client-rendered shell, and a site can rebrand the app
    bundle away from the vendor's CDN — the help app's own data hooks remain."""
    assert _writerside.is_writerside(_section("Home"))


def test_rejects_unrelated_html():
    assert not _writerside.is_writerside(
        '<html><head><meta name="generator" content="Docusaurus v3"></head>'
        '<body data-template="article"><article>x</article></body></html>'
    )


def test_rejects_a_meta_refresh_redirect_shell():
    shell = (
        '<!DOCTYPE html><html lang="en-US"><meta charset="utf-8">'
        "<title>You will be redirected shortly</title>"
        '<meta http-equiv="refresh" content="0; url=welcome.html">'
        '<h1>Redirecting&hellip;</h1><a href="welcome.html">Click here</a></html>'
    )
    assert not _writerside.is_writerside(shell)


def test_one_help_app_hook_alone_is_not_enough():
    assert not _writerside.is_writerside(
        '<html><head><script type="application/json" id="virtual-toc-data">[]</script>'
        "</head><body><p>x</p></body></html>"
    )


# --- discovery ----------------------------------------------------------------


def test_toc_is_read_from_the_instance_directory_of_a_topic_url(tmp_path, monkeypatch):
    seen = _serve(monkeypatch, _PAGES)
    _acquire(tmp_path)
    assert seen[0] == f"{_DIR}HelpTOC.json"


def test_toc_is_read_from_a_directory_seed_without_a_trailing_slash(tmp_path, monkeypatch):
    seen = _serve(monkeypatch, _PAGES)
    _acquire(tmp_path, base=_DIR)
    assert seen[0] == f"{_DIR}HelpTOC.json"


def test_pages_are_staged_in_toc_order_not_entity_order(tmp_path, monkeypatch):
    _serve(monkeypatch, _PAGES)
    acq = _acquire(tmp_path)
    staged = _staged(acq)
    assert [re.search(r"<!-- source: (\S+) -->", s).group(1) for s in staged] == [
        f"{_DIR}welcome.html",
        f"{_DIR}a.html",
        f"{_DIR}b.html",
    ]
    assert acq.pages == 3
    assert acq.lost == 0
    assert not acq.truncated


def test_raw_files_are_numbered_html_fragments(tmp_path, monkeypatch):
    _serve(monkeypatch, _PAGES)
    acq = _acquire(tmp_path)
    assert acq.kind == "html"
    assert [p.name for p in sorted(acq.raw_dir.glob("*.html"))] == [
        "0000-welcome.html",
        "0001-a.html",
        "0002-b.html",
    ]


def test_a_topic_at_a_long_path_stages_under_a_short_file_name(tmp_path, monkeypatch):
    topic = "a" * 300 + ".html"  # past a file name's 255-byte limit
    toc = _toc({"long": {"id": "long", "title": "Long", "url": topic, "level": 0}}, ["long"])
    _serve(
        monkeypatch,
        {f"{_DIR}HelpTOC.json": toc, f"{_DIR}{topic}": _article("Long", "<p>Long body.</p>")},
    )
    acq = _acquire(tmp_path)

    assert acq.pages == 1
    assert max(len(p.name) for p in acq.raw_dir.iterdir()) <= 100


def test_entries_outside_the_instance_are_not_fetched(tmp_path, monkeypatch):
    """An absolute TOC link leaves the manual; a root-relative one names another
    instance on the same host, even when it sits under the same directory."""
    pages = dict(_PAGES)
    pages[f"{_DIR}HelpTOC.json"] = _toc(
        {
            "a": {"id": "a", "title": "A", "url": "a.html"},
            "nested": {"id": "nested", "title": "N", "url": "/help/prod/multi/x.html"},
            "up": {"id": "up", "title": "Up", "url": "../other/y.html"},
            "ext": {"id": "ext", "title": "E", "url": "https://docs.example.com/help/prod/z.html"},
        },
        ["a", "nested", "up", "ext"],
    )
    seen = _serve(monkeypatch, pages)
    acq = _acquire(tmp_path)
    assert seen == [f"{_DIR}HelpTOC.json", f"{_DIR}a.html"]
    assert acq.pages == 1
    assert acq.lost == 0


def test_a_topic_listed_twice_is_fetched_once(tmp_path, monkeypatch):
    pages = dict(_PAGES)
    pages[f"{_DIR}HelpTOC.json"] = _toc(
        {
            "a": {"id": "a", "title": "A", "url": "a.html"},
            "a2": {"id": "a2", "title": "A again", "url": "a.html#part"},
        },
        ["a", "a2"],
    )
    seen = _serve(monkeypatch, pages)
    acq = _acquire(tmp_path)
    assert seen.count(f"{_DIR}a.html") == 1
    assert acq.pages == 1


def test_aliases_serving_identical_content_are_staged_once(tmp_path, monkeypatch):
    pages = dict(_PAGES)
    pages[f"{_DIR}HelpTOC.json"] = _toc(
        {
            "a": {"id": "a", "title": "A", "url": "a.html"},
            "alias": {"id": "alias", "title": "A alias", "url": "alias.html"},
        },
        ["a", "alias"],
    )
    pages[f"{_DIR}alias.html"] = pages[f"{_DIR}a.html"]
    _serve(monkeypatch, pages)
    acq = _acquire(tmp_path)
    assert acq.pages == 1
    assert acq.lost == 0
    assert _joined(acq).count("Alpha body.") == 1


def test_missing_toc_is_an_input_error(tmp_path, monkeypatch):
    _serve(monkeypatch, {})
    with pytest.raises(InvalidInputError, match="HelpTOC.json"):
        _acquire(tmp_path)


@pytest.mark.parametrize(
    "body",
    ["<html>soft 404</html>", "[]", '{"entities": {}}', '{"entities": {"pages": {}}}'],
)
def test_a_toc_that_is_not_the_help_app_shape_is_an_input_error(tmp_path, monkeypatch, body):
    _serve(monkeypatch, {f"{_DIR}HelpTOC.json": body})
    with pytest.raises(InvalidInputError, match="HelpTOC.json"):
        _acquire(tmp_path)


def test_a_toc_cycle_does_not_loop(tmp_path, monkeypatch):
    pages = dict(_PAGES)
    pages[f"{_DIR}HelpTOC.json"] = _toc(
        {
            "a": {"id": "a", "title": "A", "url": "a.html", "pages": ["b"]},
            "b": {"id": "b", "title": "B", "url": "b.html", "pages": ["a", "missing"]},
        },
        ["a"],
    )
    _serve(monkeypatch, pages)
    acq = _acquire(tmp_path)
    assert acq.pages == 2


# --- nesting --------------------------------------------------------------------


def test_group_nodes_become_headings_at_their_depth(tmp_path, monkeypatch):
    """A TOC group has no page of its own; its title exists only in the TOC."""
    _serve(monkeypatch, _PAGES)
    staged = _staged(_acquire(tmp_path))
    assert "<h1>Guides &amp; more</h1>" in staged[1]
    assert staged[1].index("Guides &amp; more") < staged[1].index("Alpha body.")
    assert "Guides" not in staged[2]


def test_a_group_holding_only_links_out_of_the_instance_leaves_no_heading(tmp_path, monkeypatch):
    pages = dict(_PAGES)
    pages[f"{_DIR}HelpTOC.json"] = _toc(
        {
            "a": {"id": "a", "title": "A", "url": "a.html"},
            "out": {"id": "out", "title": "Reference", "pages": ["ext", "inner"]},
            "ext": {"id": "ext", "title": "API", "url": "https://api.example.org/"},
            "inner": {"id": "inner", "title": "Inner", "pages": ["rootrel"]},
            "rootrel": {"id": "rootrel", "title": "Other", "url": "/help/other/x.html"},
            "b": {"id": "b", "title": "B", "url": "b.html"},
        },
        ["a", "out", "b"],
    )
    _serve(monkeypatch, pages)
    joined = _joined(_acquire(tmp_path))
    assert "Reference" not in joined
    assert "Inner" not in joined


def test_topic_headings_shift_below_their_toc_depth(tmp_path, monkeypatch):
    pages = dict(_PAGES)
    pages[f"{_DIR}a.html"] = _article(
        "A", '<section class="chapter"><h2 id="s">Part</h2><h3 id="t">Detail</h3></section>'
    )
    _serve(monkeypatch, pages)
    staged = _staged(_acquire(tmp_path))
    assert re.search(r'<h2 id="x.topic">A</h2>', staged[1])
    assert '<h3 id="s">Part</h3>' in staged[1]
    assert '<h4 id="t">Detail</h4>' in staged[1]
    assert re.search(r"<h1[^>]*>Welcome</h1>", staged[0])


def test_heading_shift_caps_at_h6(tmp_path, monkeypatch):
    chain = {
        f"n{i}": {"id": f"n{i}", "title": f"N{i}", "level": i, "pages": [f"n{i + 1}"]}
        for i in range(5)
    }
    chain["n5"] = {"id": "n5", "title": "Deep", "url": "a.html", "level": 5}
    pages = dict(_PAGES)
    pages[f"{_DIR}HelpTOC.json"] = _toc(chain, ["n0"])
    pages[f"{_DIR}a.html"] = _article("A", '<h2 id="s">Part</h2>')
    _serve(monkeypatch, pages)
    staged = _staged(_acquire(tmp_path))
    assert "<h5>N4</h5>" in staged[0]
    assert '<h6 id="x.topic">A</h6>' in staged[0]
    assert '<h6 id="s">Part</h6>' in staged[0]


# --- section pages --------------------------------------------------------------


def test_a_section_page_is_rendered_from_its_starting_page_json(tmp_path, monkeypatch):
    _serve(monkeypatch, _PAGES)
    welcome = _staged(_acquire(tmp_path))[0]
    assert re.search(r"<h1[^>]*>Welcome</h1>", welcome)
    assert "<p>Prod Documentation</p>" in welcome
    assert "<p>Prod builds things.</p>" in welcome


_CARDS = {
    "title": "Prod Documentation",
    "tips": [
        {"title": "\n  Get started\n ", "description": "Make a first project.", "url": "a.html"}
    ],
    "main": {
        "title": "First steps",
        "data": [{"title": "Basics", "description": "\n  Syntax in\n  brief.\n", "url": "b.html"}],
    },
    "highlighted": {"title": "", "data": []},
    "groups": [
        {
            "title": "Featured",
            "type": "cards",
            "data": [
                {"title": "API", "description": "Every call.", "url": "https://api.example.org/"},
                {"title": "Notes", "description": "", "url": "/help/other/notes.html"},
            ],
        },
        {
            "type": "links",
            "data": [
                {"title": "Server", "data": [{"title": "Routing", "description": "Route it."}]},
                {"title": "Empty", "data": []},
            ],
        },
    ],
}


def test_starting_page_cards_keep_their_one_line_descriptions(tmp_path, monkeypatch):
    pages = dict(_PAGES)
    pages[f"{_DIR}starting-page-s.json"] = json.dumps(_CARDS)
    _serve(monkeypatch, pages)
    welcome = _staged(_acquire(tmp_path))[0]

    assert (
        f'<ul><li><a href="{_DIR}a.html">Get started</a> — Make a first project.</li></ul>'
        f'<h2>First steps</h2><ul><li><a href="{_DIR}b.html">Basics</a> — Syntax in brief.</li></ul>'
        '<h2>Featured</h2><ul><li><a href="https://api.example.org/">API</a> — Every call.</li>'
        '<li><a href="https://docs.example.com/help/other/notes.html">Notes</a></li></ul>'
        "<h2>Server</h2><ul><li>Routing — Route it.</li></ul>"
    ) in welcome
    assert "Empty" not in welcome


def test_a_section_page_whose_json_fails_keeps_its_title(tmp_path, monkeypatch):
    pages = dict(_PAGES)
    del pages[f"{_DIR}starting-page-s.json"]
    _serve(monkeypatch, pages)
    acq = _acquire(tmp_path)
    assert re.search(r"<h1[^>]*>Welcome</h1>", _staged(acq)[0])
    assert acq.pages == 3
    assert acq.lost == 0


def test_a_section_page_json_title_repeating_the_heading_is_not_doubled(tmp_path, monkeypatch):
    pages = dict(_PAGES)
    pages[f"{_DIR}starting-page-s.json"] = json.dumps({"title": "welcome", "subtitle": ""})
    _serve(monkeypatch, pages)
    welcome = _staged(_acquire(tmp_path))[0]
    assert welcome.lower().count("welcome") == welcome.lower().count("welcome.html") + 1


# --- content cleanup ------------------------------------------------------------


def test_chrome_is_dropped(tmp_path, monkeypatch):
    _serve(monkeypatch, _PAGES)
    joined = _joined(_acquire(tmp_path))
    for chrome in (
        "Prod Help",
        "26 March 2025",
        "Previous page",
        "Next page",
        "feedback",
        "disqus",
    ):
        assert chrome not in joined
    assert "<script" not in joined


def test_code_blocks_become_preformatted_with_their_language(tmp_path, monkeypatch):
    code = (
        '<div class="code-block" data-lang="kotlin" id="c1">\n'
        "fun main() {\n    println(&quot;Hi&quot;)\n}\n</div>"
        '<div class="code-block" data-lang="bash" data-title="Console">\n'
        "                    brew install prod\n                </div>"
    )
    pages = dict(_PAGES)
    pages[f"{_DIR}a.html"] = _article("A", code)
    _serve(monkeypatch, pages)
    a = _staged(_acquire(tmp_path))[1]
    assert '<pre><code class="language-kotlin">fun main() {\n    println("Hi")\n}</code></pre>' in a
    assert '<pre><code class="language-bash">brew install prod</code></pre>' in a
    assert "code-block" not in a


def test_a_runnable_sample_loses_its_fold_markers_but_keeps_every_line(tmp_path, monkeypatch):
    code = (
        '<div class="code-block" data-lang="kotlin" data-runnable="true">\n'
        "fun main() {\n//sampleStart\n    println(1)\n    //sampleEnd\n}\n</div>"
        '<div class="code-block" data-lang="kotlin">\n//sampleStart\nval y = 2\n</div>'
    )
    pages = dict(_PAGES)
    pages[f"{_DIR}a.html"] = _article("A", code)
    _serve(monkeypatch, pages)
    a = _staged(_acquire(tmp_path))[1]
    assert '<pre><code class="language-kotlin">fun main() {\n    println(1)\n}</code></pre>' in a
    assert '<pre><code class="language-kotlin">//sampleStart\nval y = 2</code></pre>' in a


def test_a_collapsed_code_sample_keeps_its_synopsis(tmp_path, monkeypatch):
    code = (
        '<div class="code-collapse" data-is-expanded="false" data-lang="kotlin" '
        'data-synopsis="Example solution">\nval x = 1\n</div>'
    )
    pages = dict(_PAGES)
    pages[f"{_DIR}a.html"] = _article("A", code)
    _serve(monkeypatch, pages)
    a = _staged(_acquire(tmp_path))[1]
    assert "<p>Example solution</p>" in a
    assert '<pre><code class="language-kotlin">val x = 1</code></pre>' in a
    assert a.index("Example solution") < a.index("val x = 1")


def test_tab_titles_surface_above_each_tab(tmp_path, monkeypatch):
    tabs = (
        '<div class="tabs" data-anchors="[mac,win]"><div class="tabs__content" '
        'data-title="macOS/Linux" id="mac"><p>brew</p></div><div class="tabs__content" '
        'data-title="Windows" id="win"><p>winget</p></div></div>'
    )
    pages = dict(_PAGES)
    pages[f"{_DIR}a.html"] = _article("A", tabs)
    _serve(monkeypatch, pages)
    a = _staged(_acquire(tmp_path))[1]
    assert a.index("<p><strong>macOS/Linux</strong></p>") < a.index("brew")
    assert a.index("brew") < a.index("<p><strong>Windows</strong></p>") < a.index("winget")


def test_the_tldr_block_is_expanded_from_its_data_attribute(tmp_path, monkeypatch):
    payload = json.dumps(
        {"microFormat": ['<p><b>Code example</b>: <a href="https://git.example.org/s">s</a></p>']}
    )
    micro = f"<div class=\"micro-format\" data-content='{payload}'></div>"
    pages = dict(_PAGES)
    pages[f"{_DIR}a.html"] = _article("A", micro + "<p>Alpha body.</p>")
    _serve(monkeypatch, pages)
    a = _staged(_acquire(tmp_path))[1]
    assert '<b>Code example</b>: <a href="https://git.example.org/s">s</a>' in a
    assert "data-content" not in a
    assert a.index("Code example") < a.index("Alpha body.")


def test_an_unreadable_tldr_block_is_dropped(tmp_path, monkeypatch):
    micro = "<div class=\"micro-format\" data-content='not json'></div>"
    pages = dict(_PAGES)
    pages[f"{_DIR}a.html"] = _article("A", micro)
    _serve(monkeypatch, pages)
    assert "micro-format" not in _staged(_acquire(tmp_path))[1]


def test_callouts_carry_their_type_as_a_label(tmp_path, monkeypatch):
    asides = (
        '<aside class="prompt" data-title="" data-type="warning"><p>Careful.</p></aside>'
        '<aside class="prompt" data-title="Heads up" data-type="note"><p>Noted.</p></aside>'
    )
    pages = dict(_PAGES)
    pages[f"{_DIR}a.html"] = _article("A", asides)
    _serve(monkeypatch, pages)
    a = _staged(_acquire(tmp_path))[1]
    assert a.index("<p><strong>Warning:</strong></p>") < a.index("Careful.")
    assert a.index("<p><strong>Heads up:</strong></p>") < a.index("Noted.")


def test_an_embedded_video_becomes_a_link(tmp_path, monkeypatch):
    video = (
        '<div class="video-player"><object data="https://www.youtube.com/v/abc?rel=0&amp;hd=1" '
        'type="application/x-shockwave-flash"></object></div>'
    )
    pages = dict(_PAGES)
    pages[f"{_DIR}a.html"] = _article("A", video)
    _serve(monkeypatch, pages)
    a = _staged(_acquire(tmp_path))[1]
    assert '<a href="https://www.youtube.com/v/abc?rel=0&amp;hd=1">' in a
    assert "<object" not in a


def test_empty_placeholder_tables_are_dropped(tmp_path, monkeypatch):
    content = (
        '<div class="table-wrapper"><table class=""><tbody></tbody></table></div>'
        '<div class="table-wrapper"><table><tr><td>real</td></tr></table></div>'
    )
    pages = dict(_PAGES)
    pages[f"{_DIR}a.html"] = _article("A", content)
    _serve(monkeypatch, pages)
    a = _staged(_acquire(tmp_path))[1]
    assert a.count("<table") == 1
    assert "real" in a


def test_refs_are_absolutized_and_dark_variants_dropped(tmp_path, monkeypatch):
    content = (
        '<figure><img alt="shot" src="images/shot.png" data-dark-src="images/shot_dark.png"></figure>'
        '<a class="lightbox" href="images/big.png" data-dark-href="images/big_dark.png">'
        '<img src="images/big.png" data-dark-src="images/big_dark.png"></a>'
        '<p><a href="b.html#part">B</a></p>'
    )
    pages = dict(_PAGES)
    pages[f"{_DIR}a.html"] = _article("A", content)
    _serve(monkeypatch, pages)
    a = _staged(_acquire(tmp_path))[1]
    assert f'src="{_DIR}images/shot.png"' in a
    assert f'href="{_DIR}b.html#part"' in a
    assert f'href="{_DIR}images/big.png"' in a
    assert "dark" not in a


def test_an_animation_the_client_loads_from_a_data_attribute_is_kept(tmp_path, monkeypatch):
    content = (
        '<figure data-theme="light"><img alt="Adding plugins" class="js-gif" '
        'data-gif-src="images/add.gif" data-dark-gif-src="images/add_dark.gif" width="706">'
        "</figure>"
    )
    pages = dict(_PAGES)
    pages[f"{_DIR}a.html"] = _article("A", content)
    _serve(monkeypatch, pages)
    a = _staged(_acquire(tmp_path))[1]
    assert f'src="{_DIR}images/add.gif"' in a
    assert "gif-src" not in a


def test_link_hover_summaries_are_dropped(tmp_path, monkeypatch):
    content = '<p><a href="b.html" data-tooltip="\n  What B is about.\n">B</a></p>'
    pages = dict(_PAGES)
    pages[f"{_DIR}a.html"] = _article("A", content)
    _serve(monkeypatch, pages)
    a = _staged(_acquire(tmp_path))[1]
    assert "What B is about." not in a
    assert f'<a href="{_DIR}b.html">B</a>' in a


def test_a_row_of_navigation_buttons_is_dropped(tmp_path, monkeypatch):
    content = (
        '<ul class="list _bullet" id="tour-nav">'
        '<li class="list__item"><p><a as="button" href="p.html">Previous step</a></p></li>'
        '<li class="list__item"><p><a as="button" href="n.html">Next step</a></p></li></ul>'
        '<p>Ready? <a as="button" href="start.html">Start the tour</a></p>'
        '<ul><li><a as="button" href="x.html">Install</a> the plugin first</li></ul>'
    )
    pages = dict(_PAGES)
    pages[f"{_DIR}a.html"] = _article("A", content)
    _serve(monkeypatch, pages)
    a = _staged(_acquire(tmp_path))[1]
    assert "Previous step" not in a
    assert "Next step" not in a
    assert "Start the tour" in a
    assert "the plugin first" in a


_CONFIG = json.dumps(
    {
        "productName": "Prod",
        "labels": {
            "exp": {"abbreviation": "E", "name": "Experimental", "description": "May change."},
            "lib": {"abbreviation": "Standard library", "name": ""},
            "wip": {"abbreviation": "WIP", "name": "Work in progress"},
        },
    }
)


def test_heading_labels_are_named_from_the_instance_config(tmp_path, monkeypatch):
    """Primary labels show their name, secondary labels their short name."""
    content = '<h2 data-label-id="exp" data-annotation-ids="lib,wip,gone" id="f">Feature</h2>'
    pages = dict(_PAGES)
    pages[f"{_DIR}a.html"] = _article("A", content)
    pages[f"{_DIR}config.json"] = _CONFIG
    seen = _serve(monkeypatch, pages)
    staged = _staged(_acquire(tmp_path))
    assert '<h3 id="f">Feature [Experimental] [Standard library] [WIP]</h3>' in staged[1]
    assert seen.count(f"{_DIR}config.json") == 1


def test_the_config_is_not_fetched_when_no_heading_carries_a_label(tmp_path, monkeypatch):
    pages = dict(_PAGES)
    pages[f"{_DIR}config.json"] = _CONFIG
    seen = _serve(monkeypatch, pages)
    _acquire(tmp_path)
    assert f"{_DIR}config.json" not in seen


def test_labels_are_skipped_when_the_config_is_unreadable(tmp_path, monkeypatch):
    content = '<h2 data-label-id="exp" id="f">Feature</h2>'
    pages = dict(_PAGES)
    pages[f"{_DIR}a.html"] = _article("A", content)
    pages[f"{_DIR}b.html"] = _article("B", content.replace("Feature", "Other"))
    pages[f"{_DIR}config.json"] = "<html>not json</html>"
    seen = _serve(monkeypatch, pages)
    acq = _acquire(tmp_path)
    assert '<h3 id="f">Feature</h3>' in _staged(acq)[1]
    assert acq.lost == 0
    assert seen.count(f"{_DIR}config.json") == 1


# --- losses and caps ------------------------------------------------------------


def test_a_page_that_fails_to_fetch_counts_as_lost(tmp_path, monkeypatch):
    pages = dict(_PAGES)
    del pages[f"{_DIR}a.html"]
    _serve(monkeypatch, pages)
    acq = _acquire(tmp_path)
    assert acq.pages == 2
    assert acq.lost == 1
    assert "Guides &amp; more" in _staged(acq)[1]  # the group heading moves to the next page


@pytest.mark.parametrize("skip", ["fetch_fails", "no_article", "duplicate", "listed_twice"])
def test_a_skipped_topic_still_heads_the_topics_under_it(tmp_path, monkeypatch, skip):
    entities = {
        "first": {"id": "first", "title": "First", "url": "first.html"},
        "grp": {"id": "grp", "title": "Guides", "pages": ["a", "b"]},
        "a": {"id": "a", "title": "A", "url": "a.html", "pages": ["c"]},
        "c": {"id": "c", "title": "C", "url": "c.html"},
        "b": {"id": "b", "title": "B", "url": "b.html"},
    }
    pages = {
        **_PAGES,
        f"{_DIR}first.html": _article("First", "<p>First body.</p>"),
        f"{_DIR}c.html": _article("C", "<p>Gamma body.</p>"),
    }
    if skip == "fetch_fails":
        del pages[f"{_DIR}a.html"]
    elif skip == "no_article":
        pages[f"{_DIR}a.html"] = "<html><body><p>You will be redirected shortly</p></body></html>"
    elif skip == "duplicate":
        pages[f"{_DIR}first.html"] = pages[f"{_DIR}a.html"]
    else:
        entities["first"]["url"] = "a.html"
    pages[f"{_DIR}HelpTOC.json"] = _toc(entities, ["first", "grp"])
    _serve(monkeypatch, pages)

    gamma = next(s for s in _staged(_acquire(tmp_path)) if "Gamma body." in s)

    assert gamma.index("<h2>A</h2>") < gamma.index("Gamma body.")


def test_a_page_without_an_article_counts_as_lost(tmp_path, monkeypatch):
    pages = dict(_PAGES)
    pages[f"{_DIR}a.html"] = "<html><body><p>You will be redirected shortly</p></body></html>"
    _serve(monkeypatch, pages)
    acq = _acquire(tmp_path)
    assert acq.pages == 2
    assert acq.lost == 1


def test_a_page_redirecting_out_of_the_instance_counts_as_lost(tmp_path, monkeypatch):
    _serve(monkeypatch, _PAGES, finals={f"{_DIR}a.html": "https://elsewhere.example.org/a.html"})
    acq = _acquire(tmp_path)
    assert acq.pages == 2
    assert acq.lost == 1
    assert "Alpha body." not in _joined(acq)


def test_the_page_cap_truncates(tmp_path, monkeypatch):
    monkeypatch.setattr(_writerside, "_MAX_PAGES", 2)
    seen = _serve(monkeypatch, _PAGES)
    acq = _acquire(tmp_path)
    assert acq.pages == 2
    assert acq.truncated
    assert f"{_DIR}b.html" not in seen


def test_a_stalled_crawl_stops_and_reports_truncated(tmp_path, monkeypatch):
    """A host that starts refusing every topic must not be walked to the end of a
    long TOC: without a saved page for the stall window, the crawl stops."""
    toc = _toc(
        {f"p{i}": {"id": f"p{i}", "title": "P", "url": f"p{i}.html"} for i in range(40)},
        [f"p{i}" for i in range(40)],
    )
    clock = {"t": 0.0}
    monkeypatch.setattr(_toc_stage.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(cfg, "CRAWL_STALL_AFTER_S", 30)
    seen: list[str] = []

    def fetch(url, **kwargs):
        seen.append(url)
        clock["t"] += 5.0
        if url.endswith("HelpTOC.json"):
            return url, toc
        raise OSError(f"403 {url}")

    monkeypatch.setattr(http, "fetch_text", fetch)
    acq = _acquire(tmp_path)
    assert acq.truncated
    assert acq.pages == 0
    assert len(seen) < 20


# --- identity -------------------------------------------------------------------


def test_title_is_the_manual_name_not_the_entry_topic(tmp_path, monkeypatch):
    _serve(monkeypatch, _PAGES)
    acq = _acquire(tmp_path, title="Getting started | IntelliJ\xa0IDEA Documentation")
    assert acq.title == "IntelliJ IDEA Documentation"


def test_title_without_a_separator_is_kept(tmp_path, monkeypatch):
    _serve(monkeypatch, _PAGES)
    assert _acquire(tmp_path, title="Prod").title == "Prod"


@pytest.mark.parametrize(
    ("instance", "slug"),
    [
        ("https://docs.example.com/help/prod/", "example-prod"),
        ("https://docs.example.com/docs/", "example"),
        ("https://docs.example.com/docs/3.5.2/", "example-3-5-2"),
        ("https://docs.example.com/example/", "example"),
    ],
)
def test_slug_names_the_instance_directory_on_a_shared_host(tmp_path, monkeypatch, instance, slug):
    """One host serves one help instance per product; the host label alone collides."""
    pages = {
        f"{instance}HelpTOC.json": _toc({"a": {"id": "a", "title": "A", "url": "a.html"}}, ["a"]),
        f"{instance}a.html": _article("A", "<p>x</p>"),
    }
    _serve(monkeypatch, pages)
    assert _acquire(tmp_path, base=f"{instance}a.html").slug == slug


# --- the merged deliverable -----------------------------------------------------


def test_normalize_merges_the_staged_pages_in_toc_order(tmp_path, monkeypatch):
    _serve(monkeypatch, _PAGES)
    acq = _acquire(tmp_path)
    out = DocsProbePattern().normalize(acq, tmp_path).read_text(encoding="utf-8")
    assert "<title>Prod Documentation</title>" in out
    order = [out.index(s) for s in ("Prod builds things.", "Guides &amp; more", "Alpha", "Beta")]
    assert order == sorted(order)


def test_formulas_keep_their_text(tmp_path, monkeypatch):
    """MathJax draws each formula as glyph paths; its MathML is rebuilt from the SVG."""
    formula = (
        '<mjx-container class="MathJax" jax="SVG"><svg role="img"><g>'
        '<g data-mml-node="math"><g data-mml-node="mi"><path data-c="1D465" d="M0 0"></path>'
        "</g></g></g></svg></mjx-container>"
    )
    pages = dict(_PAGES)
    pages[f"{_DIR}a.html"] = _article("A", f"<p>Let {formula} be it.</p>")
    _serve(monkeypatch, pages)

    assert "<p>Let <math><mi>x</mi></math> be it.</p>" in _joined(_acquire(tmp_path))
