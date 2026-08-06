"""adobe_helpx — Adobe AEM "helpx" product guides (mocked fetch).

helpx emits no ``<meta name="generator">``; the guide is identified by host and
the ``ul.tocList`` index, which lists every topic as a leaf link.

The trap this pattern exists to avoid: ``<main>`` **contains** the TOC sidebar,
so the whole 490-entry navigation is inside the content container on every page.
Keeping it would bury ~700 KB of real content under ~8 MB of repeated nav.
"""

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.patterns.adobe_helpx import AdobeHelpxPattern

ENTRY = "https://helpx.adobe.com/illustrator/user-guide.html"

_GUIDE = """<html><head><title>Illustrator User Guide</title></head><body>
<main><div class="content">
  <div class="sideNavigation" daa-lh="TOC"><div class="tocContainer"><ul class="tocList">
    <li><a class="leafNode" href="/illustrator/desktop/basics/workspace.html">Workspace</a></li>
    <li><a class="leafNode" href="/illustrator/desktop/basics/tools.html">Tools</a></li>
    <li><a class="leafNode" href="/illustrator/desktop/basics/tools.html#anchor">Tools again</a></li>
    <li><a class="leafNode" href="/photoshop/desktop/other-product.html">Other product</a></li>
  </ul></div></div>
</div></main></body></html>"""

_TOPIC = """<html><head><title>Workspace</title></head><body>
<header>global adobe chrome</header>
<main><div class="content">
  <div class="sideNavigation" daa-lh="TOC"><div class="tocContainer"><ul class="tocList">
    <li><a class="leafNode" href="/illustrator/desktop/basics/workspace.html">Workspace</a></li>
  </ul></div></div>
  <h1>Workspace basics</h1>
  <p>Arrange panels to suit your workflow.</p>
  <img src="/content/dam/help/ill/workspace.png">
</div></main></body></html>"""


def _fetch(seen=None, *, guide=_GUIDE):
    def fetch(url, **kwargs):
        if seen is not None:
            seen.append(url)
        if "user-guide" in url or url.endswith("/desktop.html"):
            return url, guide
        return url, _TOPIC

    return fetch


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)


def test_match_claims_helpx_product_guides_only():
    p = AdobeHelpxPattern()
    assert p.match(ENTRY)
    assert p.match("https://helpx.adobe.com/photoshop/desktop/basics/tools.html")
    assert not p.match("https://www.adobe.com/products/illustrator.html")
    assert not p.match("https://helpx.adobe.com/")


def test_slug_is_the_product():
    p = AdobeHelpxPattern()
    assert p._product(ENTRY) == "illustrator"
    assert p._product("https://helpx.adobe.com/photoshop/desktop/x/y.html") == "photoshop"


def test_toc_dedupes_anchors_and_scopes_to_the_product(tmp_path, monkeypatch):
    """The TOC repeats entries with #anchors and links sibling products."""
    seen: list[str] = []
    monkeypatch.setattr(http, "fetch_text", _fetch(seen))

    acq = AdobeHelpxPattern().acquire(ENTRY, tmp_path)

    assert acq.pages == 2, "3 illustrator entries collapse to 2; photoshop excluded"
    assert not any("/photoshop/" in u for u in seen), "must not wander into another product"
    assert acq.slug == "illustrator"


def test_toc_sidebar_is_stripped_from_the_content(tmp_path, monkeypatch):
    """<main> CONTAINS the sidebar — keeping it repeats the whole TOC on every page."""
    monkeypatch.setattr(http, "fetch_text", _fetch())

    acq = AdobeHelpxPattern().acquire(ENTRY, tmp_path)
    joined = "\n".join(p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html")))

    assert "Arrange panels to suit your workflow." in joined
    assert "tocList" not in joined
    assert "sideNavigation" not in joined
    assert "global adobe chrome" not in joined


def test_absolutizes_asset_refs(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fetch())

    acq = AdobeHelpxPattern().acquire(ENTRY, tmp_path)
    joined = "\n".join(p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html")))

    assert "https://helpx.adobe.com/content/dam/help/ill/workspace.png" in joined


def test_empty_toc_is_an_input_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        http, "fetch_text", _fetch(guide="<html><body><main>no toc here</main></body></html>")
    )
    with pytest.raises(InvalidInputError, match="no topics"):
        AdobeHelpxPattern().acquire(ENTRY, tmp_path)


def test_a_topic_that_fails_to_fetch_counts_as_lost(tmp_path, monkeypatch):
    """A topic the TOC listed but the fetch raised on is reported as lost, not skipped."""

    def fetch(url, **kwargs):
        if "user-guide" in url or url.endswith("/desktop.html"):
            return url, _GUIDE
        if url.endswith("/tools.html"):
            raise OSError("connection reset")
        return url, _TOPIC

    monkeypatch.setattr(http, "fetch_text", fetch)

    acq = AdobeHelpxPattern().acquire(ENTRY, tmp_path)

    assert acq.lost == 1
    assert acq.pages == 1
    assert not any("tools" in p.name for p in acq.raw_dir.glob("*.html"))


def test_a_topic_without_main_counts_as_lost(tmp_path, monkeypatch):
    """A 200 topic whose <main> is absent is reported as lost, not silently dropped."""
    no_main = "<html><body><div class='content'>layout changed</div></body></html>"

    def fetch(url, **kwargs):
        if "user-guide" in url or url.endswith("/desktop.html"):
            return url, _GUIDE
        if url.endswith("/tools.html"):
            return url, no_main
        return url, _TOPIC

    monkeypatch.setattr(http, "fetch_text", fetch)

    acq = AdobeHelpxPattern().acquire(ENTRY, tmp_path)

    assert acq.lost == 1
    assert acq.pages == 1
    assert not any("tools" in p.name for p in acq.raw_dir.glob("*.html"))


def test_normalize_merges_pages(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fetch())
    p = AdobeHelpxPattern()

    acq = p.acquire(ENTRY, tmp_path)
    out = p.normalize(acq, tmp_path).read_text(encoding="utf-8")

    assert out.startswith("<!DOCTYPE html>")
    assert out.count("<h1>Workspace basics</h1>") == 2  # both topics merged


def test_inline_script_and_style_are_dropped(tmp_path, monkeypatch):
    """helpx inlines a <style> block per component — 12,378 of them on Illustrator,
    32% of the deliverable. They carry no content and bloat the hand-off."""
    styled = _TOPIC.replace(
        "<h1>Workspace basics</h1>",
        "<style>.dexter-Foo{color:red}</style><script>window.x=1</script><h1>Workspace basics</h1>",
    )

    def fetch(url, **kwargs):
        if "user-guide" in url or url.endswith("/desktop.html"):
            return url, _GUIDE
        return url, styled

    monkeypatch.setattr(http, "fetch_text", fetch)

    acq = AdobeHelpxPattern().acquire(ENTRY, tmp_path)
    joined = "\n".join(p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html")))

    assert "Arrange panels to suit your workflow." in joined
    assert "dexter-Foo" not in joined
    assert "window.x" not in joined


def test_extract_drops_every_aem_chrome_component():
    """helpx bolts five AEM components onto every topic inside <main> — promo
    cards, the feedback widget, prev/next arrows, social share, and the CTA
    footer experience-fragment. One set per page, ~490 pages, no documentation
    in any of them."""
    html = """<html><body><main>
      <h1>Draw with the Pen tool</h1><p>Real documentation.</p>
      <div class="dexter-FlexContainer">
        <div class="contentcard aem-GridColumn"><a class="contentcard-target">
          <p class="contentcard-description">Learn with step-by-step video tutorials.</p></a></div>
      </div>
      <div class="feedbackV2 aem-GridColumn aem-GridColumn--default--12">
        <div class="feedbackV2-container" data-welcome-banner-question-text="Was this page helpful?">x</div></div>
      <div class="pagenavigationarrows aem-GridColumn aem-GridColumn--default--12">
        <a class="prev-content">Previous Modify brushes</a><a class="next-content">Next Convert strokes</a></div>
      <div class="socialmediashare"><a href="https://twitter.com/share">Share</a></div>
      <div class="xf" id="root_content_position_position-par_xfreference_footer">
        <p>Design with precision in Illustrator</p><a>Open the app</a></div>
      <div class="xfreference experiencefragment aem-GridColumn">
        <p>Try it in the app</p><p>Use the Pen tool to draw lines in a few simple steps.</p></div>
    </main></body></html>"""
    frag = AdobeHelpxPattern()._extract(html, "https://helpx.adobe.com/illustrator/using/pen.html")
    assert frag is not None
    assert "Real documentation." in frag
    for gone in (
        "contentcard",
        "step-by-step video tutorials",
        "feedbackV2",
        "Was this page helpful?",
        "pagenavigationarrows",
        "Previous Modify brushes",
        "socialmediashare",
        "xfreference_footer",
        "Design with precision",
        "xfreference experiencefragment",
        "Try it in the app",
    ):
        assert gone not in frag, f"{gone!r} survived extraction"


def test_declines_pdf_urls_so_they_keep_routing_to_pdf_url():
    """helpx serves PDFs at /pdf/<app>_reference.pdf. adobe_helpx is registered
    ahead of pdf_url, so without this it hijacks them and derives the product
    "pdf" — fetching a guide page that does not exist."""
    p = AdobeHelpxPattern()
    assert not p.match("https://helpx.adobe.com/pdf/illustrator_reference.pdf")
    assert p.match("https://helpx.adobe.com/illustrator/user-guide.html")
