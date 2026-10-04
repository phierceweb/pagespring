"""_flare: MadCap Flare HTML5 help, read from its TOC data and topic pages (mocked fetch)."""

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.config import cfg
from pagespring.patterns import _flare, _toc_stage
from pagespring.patterns.docs_probe import DocsProbePattern

_ROOT = "https://help.vendor.example/manual/"
_ENTRY = f"{_ROOT}Widget_Pro_Guide.htm"

# The entry shell as Flare writes it: no generator meta, no title, an empty body.
_SHELL = (
    '<!DOCTYPE html>\n<html class="_Skins_HTML5_sidenav" data-mc-runtime-file-type="Default">'
    '<head><meta charset="utf-8" /><title></title>'
    '<script src="Resources/Scripts/require.min.js"></script></head><body></body></html>'
)


def _topic(title: str, content: str, up: str = "../") -> str:
    return (
        '<!DOCTYPE html>\n<html lang="en-us" data-mc-search-type="Stem"'
        ' data-mc-help-system-file-name="Widget_Pro_Guide.xml"'
        f' data-mc-path-to-help-system="{up}" data-mc-toc-path="Getting Started"'
        ' data-mc-target-type="WebHelp2" data-mc-runtime-file-type="Topic">'
        f"<head><title>{title}</title>"
        f'<script src="{up}Resources/Scripts/require.min.js"></script></head><body>'
        '<div class="nocontent"><div class="MCBreadcrumbsBox_0 breadcrumbs" role="navigation">'
        '<span class="MCBreadcrumbsPrefix">You are here: </span></div></div>'
        f'<div role="main" id="mc-main-content">{content}</div>'
        '<div class="buttons topic-buttons"><button class="previous-topic-button">'
        "Previous</button></div></body></html>"
    )


def _help_system(toc: str = "Data/Tocs/Widget_Pro.js", output: str | None = "Widget_Pro_Guide.htm"):
    out_attr = f' OutputFile="{output}"' if output else ""
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<WebHelpSystem DefaultUrl="Content/Welcome.htm"{out_attr} Toc="{toc}"'
        ' Index="Data/Index.js" TargetType="WebHelp2" xml:lang="en-us">'
        '<CatapultSkin Version="6" SkinType="WebHelp2" Tabs="TOC" /></WebHelpSystem>'
    )


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


# --- detection --------------------------------------------------------------


def test_recognises_the_entry_shell_by_its_runtime_attribute():
    assert _flare.is_flare(_SHELL)


def test_recognises_a_topic_page():
    assert _flare.is_flare(_topic("Install", "<h1>Install</h1>"))


@pytest.mark.parametrize(
    "page",
    [
        "<html><head><title>Docs</title></head><body><p>Hello</p></body></html>",
        '<html data-theme="dark"><body><main>x</main></body></html>',
        # A page about Flare quoting its markup as text is not Flare output.
        "<html><body><pre>&lt;html data-mc-runtime-file-type=&quot;Topic&quot;&gt;</pre></body>"
        '</html><p data-mc-runtime-file-type="Topic">quoted</p>',
    ],
)
def test_rejects_pages_flare_did_not_write(page):
    assert not _flare.is_flare(page)


@pytest.mark.parametrize(
    ("page_url", "page", "root"),
    [
        (_ENTRY, _SHELL, _ROOT),
        (f"{_ROOT}Content/Getting_Started/Install.htm", _topic("I", "", up="../../"), _ROOT),
        (f"{_ROOT}Content/Welcome.htm", _topic("W", ""), _ROOT),
        ("https://help.vendor.example/manual", _SHELL, _ROOT),
        ("https://help.vendor.example", _SHELL, "https://help.vendor.example/"),
    ],
)
def test_the_help_system_root_comes_from_the_page_it_was_found_on(page_url, page, root):
    assert _flare.help_root(page_url, page) == root


# --- acquire ----------------------------------------------------------------

_TOC = (
    "define({numchunks:2,prefix:'Widget_Pro_Chunk',"
    "chunkstart:['/Content/Getting_Started/Install Steps.htm','/Content/Getting_Started/Connect.htm'],"
    "tree:{n:[{i:0,c:0},{i:1,c:1,n:[{i:2,c:0},{i:3,c:1,n:[{i:4,c:1}]}]},"
    "{i:5,c:0,f:'_blank'},{i:6,c:1}]}});"
)
_CHUNK0 = (
    "define({'/Content/Getting_Started/Install Steps.htm':{i:[2],t:['Install'],b:['']},"
    "'/Content/Welcome.htm':{i:[0],t:['Welcome'],b:['']},"
    "'https://www.vendor.example/widget/':{i:[5],t:['Product page'],b:['']}});"
)
_CHUNK1 = (
    "define({'/Content/Getting_Started/Connect.htm':{i:[3],t:['Connect'],b:['']},"
    "'/Content/Getting_Started/Wiring.htm':{i:[4],t:['Wiring'],b:['']},"
    "'/Content/Welcome.htm':{i:[6],t:['Welcome again'],b:['']},"
    "'___':{i:[1],t:['Getting Started'],b:['']}});"
)
_DROPDOWN = (
    '<div class="MCDropDown MCDropDown_Closed dropDown dropDownHeading">'
    '<span class="MCDropDownHead dropDownHead"><a aria-expanded="false" class="MCDropDownHotSpot'
    ' dropDownHotspot MCDropDownHotSpot_ MCHotSpotImage" href="javascript:void(0)">'
    '<img alt="Closed" class="MCDropDown_Image_Icon" data-mc-alt2="Open" height="11"'
    ' src="../../Skins/Default/Stylesheets/Images/transparent.gif" width="16" />Cable Types</a>'
    '</span><div class="MCDropDownBody dropDownBody"><p>Use shielded cable.</p></div></div>'
)
_PAGES = {
    _ENTRY: _SHELL,
    f"{_ROOT}Data/HelpSystem.xml": _help_system(),
    f"{_ROOT}Data/Tocs/Widget_Pro.js": _TOC,
    f"{_ROOT}Data/Tocs/Widget_Pro_Chunk0.js": _CHUNK0,
    f"{_ROOT}Data/Tocs/Widget_Pro_Chunk1.js": _CHUNK1,
    f"{_ROOT}Content/Welcome.htm": _topic(
        "Welcome",
        '<h2>Welcome</h2><p>Welcome body.</p><p><img src="../Resources/Images/splash.png" /></p>',
    ),
    f"{_ROOT}Content/Getting_Started/Install%20Steps.htm": _topic(
        "Install",
        '<h2>Install</h2><p>Install body, then <a href="Connect.htm">connect</a>.</p>'
        "<script>track()</script>",
        up="../../",
    ),
    f"{_ROOT}Content/Getting_Started/Connect.htm": _topic(
        "Connect",
        f"<h1>Connect</h1><h3>Before you start</h3><p>Connect body.</p>{_DROPDOWN}",
        up="../../",
    ),
    f"{_ROOT}Content/Getting_Started/Wiring.htm": _topic(
        "Wiring", "<p>Wiring body.</p>", up="../../"
    ),
}


def _acquire(tmp_path, base=_ENTRY, slug="vendor", title=None):
    return _flare.acquire(base, tmp_path, slug=slug, title=title)


def _staged(acq) -> list[str]:
    return [p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html"))]


def test_topics_are_staged_in_toc_order(tmp_path, monkeypatch):
    _serve(monkeypatch, _PAGES)

    acq = _acquire(tmp_path)

    sources = [s.split("\n", 1)[0] for s in _staged(acq)]
    assert sources == [
        f"<!-- source: {_ROOT}Content/Welcome.htm -->",
        f"<!-- source: {_ROOT}Content/Getting_Started/Install%20Steps.htm -->",
        f"<!-- source: {_ROOT}Content/Getting_Started/Connect.htm -->",
        f"<!-- source: {_ROOT}Content/Getting_Started/Wiring.htm -->",
    ]
    assert (acq.kind, acq.pages, acq.lost, acq.truncated) == ("html", 4, 0, False)


def test_raw_files_are_numbered_fragments_named_for_their_topic(tmp_path, monkeypatch):
    _serve(monkeypatch, _PAGES)

    acq = _acquire(tmp_path)

    assert [p.name for p in sorted(acq.raw_dir.glob("*.html"))] == [
        "0000-content-welcome.html",
        "0001-content-getting-started-install-steps.html",
        "0002-content-getting-started-connect.html",
        "0003-content-getting-started-wiring.html",
    ]
    assert all("mc-main-content" not in s for s in _staged(acq))


def test_a_book_without_a_page_heads_the_topics_under_it(tmp_path, monkeypatch):
    _serve(monkeypatch, _PAGES)

    install = _staged(_acquire(tmp_path))[1]

    assert install.index("<h1>Getting Started</h1>") < install.index("<h2>Install</h2>")


def test_topic_headings_are_seated_at_their_toc_depth(tmp_path, monkeypatch):
    _serve(monkeypatch, _PAGES)

    welcome, _install, connect, wiring = _staged(_acquire(tmp_path))

    assert "<h1>Welcome</h1>" in welcome
    assert "<h2>Connect</h2>" in connect and "<h4>Before you start</h4>" in connect
    assert "<h3>Wiring</h3>" in wiring


def test_a_topic_placed_twice_is_fetched_once_and_links_out_are_not_fetched(tmp_path, monkeypatch):
    seen = _serve(monkeypatch, _PAGES)

    _acquire(tmp_path)

    assert seen.count(f"{_ROOT}Content/Welcome.htm") == 1
    assert not [u for u in seen if "www.vendor.example" in u]


def test_identical_topics_under_two_paths_are_staged_once(tmp_path, monkeypatch):
    pages = dict(_PAGES)
    pages[f"{_ROOT}Content/Getting_Started/Wiring.htm"] = pages[
        f"{_ROOT}Content/Getting_Started/Connect.htm"
    ]
    _serve(monkeypatch, pages)

    acq = _acquire(tmp_path)

    assert (acq.pages, acq.lost) == (3, 0)


@pytest.mark.parametrize("skip", ["duplicate_body", "placed_again", "fetch_fails"])
def test_a_skipped_topic_still_heads_the_topics_under_it(tmp_path, monkeypatch, skip):
    connect = f"{_ROOT}Content/Getting_Started/Connect.htm"
    pages = dict(_PAGES)
    if skip == "duplicate_body":
        pages[connect] = pages[f"{_ROOT}Content/Getting_Started/Install%20Steps.htm"]
    elif skip == "placed_again":
        pages[f"{_ROOT}Data/Tocs/Widget_Pro_Chunk1.js"] = _CHUNK1.replace(
            "/Getting_Started/Connect.htm", "/Getting_Started/Install Steps.htm"
        )
    else:
        del pages[connect]
    _serve(monkeypatch, pages)

    wiring = next(s for s in _staged(_acquire(tmp_path)) if "Wiring body." in s)

    assert wiring.index("<h2>Connect</h2>") < wiring.index("<h3>Wiring</h3>")


def test_page_chrome_and_runtime_controls_are_dropped(tmp_path, monkeypatch):
    _serve(monkeypatch, _PAGES)

    joined = "\n".join(_staged(_acquire(tmp_path)))

    assert "You are here" not in joined and "Previous" not in joined
    assert "track()" not in joined
    assert "javascript:" not in joined and "transparent.gif" not in joined
    assert "Cable Types" in joined and "Use shielded cable." in joined


def test_links_and_images_resolve_against_the_topic(tmp_path, monkeypatch):
    _serve(monkeypatch, _PAGES)

    welcome, install, *_ = _staged(_acquire(tmp_path))

    assert f'src="{_ROOT}Resources/Images/splash.png"' in welcome
    assert f'href="{_ROOT}Content/Getting_Started/Connect.htm"' in install


def test_a_hotspot_linking_nowhere_keeps_its_label_but_not_its_link(tmp_path, monkeypatch):
    hotspot = (
        '<div class="MCDropDown MCDropDown_Closed dropDown"><span class="MCDropDownHead">'
        '<a aria-expanded="false" class="MCDropDownHotSpot dropDownHotspot" href="#">'
        'Previous releases</a></span><div class="MCDropDownBody"><p>Older notes.</p></div></div>'
        '<p>See <a href="#Clock">the clock section</a>.</p>'
    )
    pages = {
        **_PAGES,
        f"{_ROOT}Content/Welcome.htm": _topic("Welcome", f"<h1>Welcome</h1>{hotspot}"),
    }
    _serve(monkeypatch, pages)

    welcome = _staged(_acquire(tmp_path))[0]

    assert 'href="#"' not in welcome and "Previous releases" in welcome
    assert 'href="#Clock"' in welcome


def test_a_glossary_popup_keeps_its_term_and_drops_the_hidden_definition(tmp_path, monkeypatch):
    popup = (
        '<p>An eight-band parametric <a href="javascript:void(0)" class="MCTextPopup'
        ' MCTextPopupHotSpot glossaryTerm glossaryTermPopup" glossTerm="Glossary.Term0">equalizer'
        '<img class="MCHelpControl_Image_Icon" src="../Skins/Default/Stylesheets/Images/'
        'transparent.gif" height="11" width="16" alt="Closed" /><span class="MCTextPopupBody'
        ' popupBody"><span class="MCTextPopupArrow"> </span>A filter that adjusts a frequency'
        " range.</span></a> with optional metering.</p>"
    )
    pages = {**_PAGES, f"{_ROOT}Content/Welcome.htm": _topic("Welcome", f"<h1>Welcome</h1>{popup}")}
    _serve(monkeypatch, pages)

    welcome = _staged(_acquire(tmp_path))[0]

    assert "An eight-band parametric equalizer with optional metering." in welcome
    assert "adjusts a frequency" not in welcome


def test_a_related_topics_control_becomes_the_links_it_lists(tmp_path, monkeypatch):
    controls = (
        '<p><a href="javascript:void(0);" class="MCHelpControl MCHelpControl-Related relatedTopics"'
        ' data-mc-topics="Wiring|Wiring.htm||Install|Install%20Steps.htm">'
        '<span class="MCHelpControl-RelatedHotSpot_"><img class="MCHelpControl_Image_Icon"'
        ' src="../../Skins/Default/Stylesheets/Images/transparent.gif" alt="Related Topics Link'
        ' Icon" />Related Topics</span></a></p>'
        '<p><a href="javascript:void(0);" class="MCHelpControl MCHelpControl-Keyword keywordLink"'
        ' data-mc-keywords="cables"><span>Keyword Links</span></a></p>'
    )
    pages = {
        **_PAGES,
        f"{_ROOT}Content/Getting_Started/Connect.htm": _topic(
            "Connect", f"<h1>Connect</h1>{controls}", up="../../"
        ),
    }
    _serve(monkeypatch, pages)

    connect = _staged(_acquire(tmp_path))[2]

    base = f"{_ROOT}Content/Getting_Started/"
    assert (
        f'Related Topics: <a href="{base}Wiring.htm">Wiring</a>, '
        f'<a href="{base}Install%20Steps.htm">Install</a>'
    ) in connect
    assert "Keyword Links" not in connect


def test_a_code_snippet_becomes_plain_code_in_its_language(tmp_path, monkeypatch):
    snippet = (
        '<div class="codeSnippet"><a class="codeSnippetCopyButton" role="button"'
        ' href="javascript:void(0);">Copy</a> <div style="mc-code-lang: Lua;"'
        ' class="codeSnippetBody" data-mc-use-line-numbers="False"><pre><code>'
        '<span style="color: #969896; ">--Set up the widget</span><br />'
        'Widget = Component.New(<span style="color: #df5000; ">"Widget 1"</span>)'
        "</code></pre></div></div>"
    )
    pages = {
        **_PAGES,
        f"{_ROOT}Content/Welcome.htm": _topic("Welcome", f"<h1>Welcome</h1>{snippet}"),
    }
    _serve(monkeypatch, pages)

    welcome = _staged(_acquire(tmp_path))[0]

    assert (
        '<pre><code class="language-lua">--Set up the widget\n'
        'Widget = Component.New("Widget 1")</code></pre>'
    ) in welcome
    assert "Copy" not in welcome


def test_a_topic_seed_finds_the_help_system_above_it(tmp_path, monkeypatch):
    _serve(monkeypatch, _PAGES)

    acq = _acquire(tmp_path, base=f"{_ROOT}Content/Getting_Started/Connect.htm")

    assert acq.pages == 4


@pytest.mark.parametrize(
    ("failure", "url"),
    [
        ("fetch", f"{_ROOT}Content/Getting_Started/Wiring.htm"),
        ("no_body", f"{_ROOT}Content/Getting_Started/Wiring.htm"),
        ("redirect", f"{_ROOT}Content/Getting_Started/Wiring.htm"),
    ],
)
def test_a_topic_that_cannot_be_staged_counts_as_lost(tmp_path, monkeypatch, failure, url):
    pages = dict(_PAGES)
    finals = {}
    if failure == "fetch":
        del pages[url]
    elif failure == "no_body":
        pages[url] = "<html data-mc-runtime-file-type='Topic'><body><p>Moved.</p></body></html>"
    else:
        finals[url] = "https://www.vendor.example/landing"
    _serve(monkeypatch, pages, finals=finals)

    acq = _acquire(tmp_path)

    assert (acq.pages, acq.lost) == (3, 1)


def test_the_page_cap_truncates(tmp_path, monkeypatch):
    _serve(monkeypatch, _PAGES)
    monkeypatch.setattr(_flare, "_MAX_PAGES", 2)

    acq = _acquire(tmp_path)

    assert (acq.pages, acq.truncated) == (2, True)


def test_a_stalled_crawl_stops_and_reports_truncated(tmp_path, monkeypatch):
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(_toc_stage.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(cfg, "CRAWL_STALL_AFTER_S", 150)
    pages = {k: v for k, v in _PAGES.items() if "Content/" not in k or "Welcome" in k}
    _serve(monkeypatch, pages)

    acq = _acquire(tmp_path)

    assert acq.truncated is True and acq.pages == 1 and acq.lost < 3


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({f"{_ROOT}Data/HelpSystem.xml": None}, "HelpSystem.xml"),
        ({f"{_ROOT}Data/HelpSystem.xml": "<html><body>Not found</body></html>"}, "HelpSystem.xml"),
        ({f"{_ROOT}Data/HelpSystem.xml": '<?xml version="1.0"?><WebHelpSystem/>'}, "no TOC"),
        ({f"{_ROOT}Data/Tocs/Widget_Pro.js": None}, "Widget_Pro.js"),
        ({f"{_ROOT}Data/Tocs/Widget_Pro_Chunk1.js": None}, "Widget_Pro_Chunk1.js"),
        (
            {
                f"{_ROOT}Data/Tocs/Widget_Pro.js": (
                    "define({numchunks:1,prefix:'Widget_Pro_Chunk',tree:{n:[{i:5,c:0}]}});"
                )
            },
            "no topic pages",
        ),
    ],
)
def test_a_help_system_that_cannot_list_its_topics_is_refused(tmp_path, monkeypatch, change, match):
    pages = {**_PAGES, **change}
    _serve(monkeypatch, {k: v for k, v in pages.items() if v is not None})

    with pytest.raises(InvalidInputError, match=match):
        _acquire(tmp_path)


# --- identity ---------------------------------------------------------------


def test_slug_and_title_name_the_output_file(tmp_path, monkeypatch):
    _serve(monkeypatch, _PAGES)

    acq = _acquire(tmp_path)

    assert (acq.slug, acq.title) == ("vendor-widget-pro-guide", "Widget Pro Guide")


@pytest.mark.parametrize(
    ("output", "toc", "slug", "title"),
    [
        ("Default.htm", "Data/Tocs/Widget_Pro.js", "vendor-widget-pro", "Widget Pro"),
        (None, "Data/Tocs/DesignerTOC.js", "vendor-designer", "Designer"),
        ("Index.htm", "Data/Tocs/Master.js", "vendor", None),
        ("Vendor_Widget.htm", "Data/Tocs/Master.js", "vendor-widget", "Vendor Widget"),
    ],
)
def test_generic_flare_names_are_skipped_for_the_next_one(
    tmp_path, monkeypatch, output, toc, slug, title
):
    toc_name = toc.rsplit("/", 1)[1].removesuffix(".js")
    pages = {k: v for k, v in _PAGES.items() if "Data/Tocs" not in k}
    pages[f"{_ROOT}Data/HelpSystem.xml"] = _help_system(toc=toc, output=output)
    pages[f"{_ROOT}{toc}"] = _TOC.replace("Widget_Pro_Chunk", f"{toc_name}_Chunk")
    pages[f"{_ROOT}Data/Tocs/{toc_name}_Chunk0.js"] = _CHUNK0
    pages[f"{_ROOT}Data/Tocs/{toc_name}_Chunk1.js"] = _CHUNK1
    _serve(monkeypatch, pages)

    acq = _acquire(tmp_path)

    assert (acq.slug, acq.title) == (slug, title)


def test_a_title_the_entry_shell_declares_wins(tmp_path, monkeypatch):
    pages = {**_PAGES, _ENTRY: _SHELL.replace("<title></title>", "<title>Widget Help</title>")}
    _serve(monkeypatch, pages)

    acq = _acquire(tmp_path, title="Widget Help")

    assert acq.title == "Widget Help"


def test_a_topic_seed_does_not_lend_the_manual_its_title(tmp_path, monkeypatch):
    _serve(monkeypatch, _PAGES)

    acq = _acquire(tmp_path, base=f"{_ROOT}Content/Getting_Started/Connect.htm", title="Connect")

    assert acq.title == "Widget Pro Guide"


def _rebased(root: str, output: str, toc: str = "Data/Tocs/Master.js") -> dict[str, str]:
    toc_name = toc.rsplit("/", 1)[1].removesuffix(".js")
    pages = {root + k[len(_ROOT) :]: v for k, v in _PAGES.items() if "Data/" not in k}
    pages[f"{root}Data/HelpSystem.xml"] = _help_system(toc=toc, output=output)
    pages[f"{root}{toc}"] = _TOC.replace("Widget_Pro_Chunk", f"{toc_name}_Chunk")
    pages[f"{root}Data/Tocs/{toc_name}_Chunk0.js"] = _CHUNK0
    pages[f"{root}Data/Tocs/{toc_name}_Chunk1.js"] = _CHUNK1
    pages[f"{root}Widget_Pro_Guide.htm"] = _SHELL
    return pages


def test_the_root_directory_names_a_manual_whose_files_keep_flares_defaults(tmp_path, monkeypatch):
    root = "https://help.vendor.example/widget-studio/"
    _serve(monkeypatch, _rebased(root, "Default.htm"))

    acq = _acquire(tmp_path, base=f"{root}Widget_Pro_Guide.htm")

    assert (acq.slug, acq.title) == ("vendor-widget-studio", "Widget Studio")


def test_a_locale_directory_does_not_name_the_manual(tmp_path, monkeypatch):
    root = "https://help.vendor.example/en-us/"
    _serve(monkeypatch, _rebased(root, "Default.htm"))

    acq = _acquire(tmp_path, base=f"{root}Widget_Pro_Guide.htm")

    assert (acq.slug, acq.title) == ("vendor", None)


# --- docs_probe -------------------------------------------------------------


def test_docs_probe_routes_a_flare_entry_shell_and_stages_its_topics(tmp_path, monkeypatch):
    _serve(monkeypatch, _PAGES)

    acq = DocsProbePattern().acquire(_ENTRY, tmp_path)

    assert (acq.slug, acq.pages) == ("vendor-widget-pro-guide", 4)
