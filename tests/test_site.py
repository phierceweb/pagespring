"""_site — shared docs-site helpers (pure functions, no network)."""

import pytest
from bs4 import BeautifulSoup

from pagespring.patterns import _site
from pagespring.patterns._site import (
    absolutize_refs,
    generator_meta,
    page_title,
    slug_from_host,
)


def test_slug_from_host():
    assert slug_from_host("docs.pytest.org") == "pytest"
    assert slug_from_host("www.mkdocs.org") == "mkdocs"
    assert slug_from_host("docusaurus.io") == "docusaurus"
    assert slug_from_host("manual.audacityteam.org") == "audacityteam"
    assert slug_from_host("squidfunk.github.io") == "squidfunk"


def test_page_title_and_generator_meta():
    html = (
        "<html><head><title> MkDocs </title>"
        '<meta name="generator" content="mkdocs-1.6.1, mkdocs-material-9.5.0">'
        "</head><body></body></html>"
    )
    assert page_title(html) == "MkDocs"
    assert generator_meta(html) == "mkdocs-1.6.1, mkdocs-material-9.5.0"
    assert generator_meta("<html><head></head></html>") == ""


def test_absolutize_refs():
    soup = BeautifulSoup(
        '<article><a href="/guide/x">x</a><img src="img/pic.png">'
        '<a href="https://ex.com/abs">abs</a><a href="#frag">frag</a></article>',
        "html.parser",
    )
    article = soup.find("article")
    absolutize_refs(article, "https://docs.ex.com/section/page/")
    html = str(soup)
    assert 'href="https://docs.ex.com/guide/x"' in html
    assert 'src="https://docs.ex.com/section/page/img/pic.png"' in html
    assert 'href="https://ex.com/abs"' in html  # untouched
    assert 'href="#frag"' in html  # untouched


# --- flatten_responsive_images ---------------------------------------------


def _flat(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    _site.flatten_responsive_images(soup)
    return str(soup)


def test_picture_collapses_to_its_img():
    """Apple ships <source media="(not all)"> — a query that never matches —
    holding the dark-mode variant. It is inert markup the localizer cannot see,
    and every <picture> in the corpus has an <img> fallback."""
    out = _flat(
        '<picture><source media="(not all)" srcset="https://cdn/dark.png"/>'
        '<img alt="Main window" src="https://cdn/light.png"/></picture>'
    )

    assert "<picture>" not in out
    assert "<source" not in out
    assert 'src="https://cdn/light.png"' in out
    assert 'alt="Main window"' in out


def test_picture_without_an_img_keeps_its_densest_source():
    """Defensive: dropping the sources with no fallback would lose the image."""
    out = _flat('<picture><source srcset="https://cdn/a.png 1x, https://cdn/a2.png 2x"/></picture>')

    assert 'src="https://cdn/a2.png"' in out
    assert "<source" not in out


def test_srcset_is_dropped_from_img():
    """srcset holds other widths of the same image; src is canonical and is what
    the localizer downloads."""
    out = _flat('<img src="images/d.jpg" srcset="https://cdn/d-2400.jpg 2400w" sizes="100vw"/>')

    assert 'src="images/d.jpg"' in out
    assert "srcset" not in out
    assert "sizes" not in out


def test_data_src_is_promoted_over_a_placeholder_src():
    """Lazy-loaded images park a data: URI in src and the real URL in data-src.
    Dropping data-src would lose the only real reference."""
    out = _flat(
        '<img src="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=" data-src="https://cdn/real.png"/>'
    )

    assert 'src="https://cdn/real.png"' in out
    assert "data-src" not in out
    assert "base64" not in out


def test_data_src_does_not_clobber_a_real_src():
    """When src already names a real image, it wins — data-src may be a
    thumbnail or a duplicate."""
    out = _flat('<img src="https://cdn/real.png" data-src="https://cdn/thumb.png"/>')

    assert 'src="https://cdn/real.png"' in out
    assert "thumb.png" not in out


def test_plain_images_are_untouched():
    out = _flat('<p>text</p><img alt="a" src="https://cdn/a.png"/>')

    assert 'src="https://cdn/a.png"' in out
    assert 'alt="a"' in out
    assert "<p>text</p>" in out


def test_runs_before_absolutize_so_promoted_refs_are_absolutized():
    """A promoted data-src is relative on the source page; absolutize must still
    see it."""
    soup = BeautifulSoup(
        '<img src="data:image/gif;base64,R0lGOD" data-src="pics/x.png"/>', "html.parser"
    )
    _site.flatten_responsive_images(soup)
    _site.absolutize_refs(soup, "https://h.test/docs/page.html")

    assert 'src="https://h.test/docs/pics/x.png"' in str(soup)


def test_srcset_survives_when_it_is_the_only_reference():
    """Deleting the carrier before resolving a winner destroys the image."""
    out = _flat('<img alt="z" srcset="https://x/a-800.png 800w, https://x/a-1600.png 1600w"/>')

    assert 'src="https://x/a-1600.png"' in out
    assert "srcset" not in out


def test_srcset_beats_a_placeholder_src():
    out = _flat('<img src="data:image/gif;base64,R0lGOD" srcset="https://x/real.png 2x"/>')

    assert 'src="https://x/real.png"' in out
    assert "base64" not in out


def test_data_srcset_is_honoured():
    """Adobe parks a transparent SVG spacer in src/srcset and the real URL in
    the data-* twin — preferring srcset would blank every figure."""
    out = _flat(
        '<img src="data:image/svg+xml;base64,PHN2Zz4=" data-srcset="https://x/real.png 2x"/>'
    )

    assert 'src="https://x/real.png"' in out
    assert "data-srcset" not in out


def test_a_base64_srcset_yields_no_winner():
    """A data: URI contains its own comma, so naive srcset splitting truncates
    it into a bogus URL."""
    out = _flat('<picture><source srcset="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="/></picture>')

    assert "PHN2Zz48L3N2Zz4" not in out
    assert 'src="data:image/svg+xml;base64"' not in out


def test_image_with_no_usable_reference_is_dropped():
    """AEM emits empty lazy-load placeholders; a bare <img> is noise."""
    out = _flat('<p>keep</p><img class="dexter-LazyImage" emptytext="Image"/>')

    assert "<img" not in out
    assert "<p>keep</p>" in out


def test_real_src_still_outranks_every_carrier():
    out = _flat(
        '<img src="images/local.png" srcset="https://x/big.png 2400w" '
        'data-src="https://x/thumb.png"/>'
    )

    # an already-localized ref is never swapped back to a remote rendition
    assert 'src="images/local.png"' in out
    assert "big.png" not in out and "thumb.png" not in out


def test_entity_escaped_query_string_survives():
    """A scene7 URL's query IS the asset selector — losing it fetches a
    different rendition."""
    out = _flat(
        '<img src="data:image/svg+xml;base64,PHN2Zz4=" '
        'data-src="https://s7.test/is/image/X/y?$pjpeg$&amp;jpegSize=300&amp;wid=1920"/>'
    )

    assert "jpegSize=300" in out
    assert "wid=1920" in out


def test_root_relative_promotion_is_absolutized():
    """A promoted data-src is often page-relative, which is why the flatten runs
    before absolutize_refs."""
    soup = BeautifulSoup(
        '<img src="data:image/svg+xml;base64,PHN2Zz4=" data-src="/content/dam/a.jpg"/>',
        "html.parser",
    )
    _site.flatten_responsive_images(soup)
    _site.absolutize_refs(soup, "https://helpx.adobe.com/illustrator/using/x.html")

    assert soup.find("img")["src"] == "https://helpx.adobe.com/content/dam/a.jpg"


def test_largest_declared_width_wins_across_candidates():
    """The corpus feeds a vision pass, so resolution is the goal. Width comes
    from a srcset `w` descriptor or a `wid=` query param."""
    out = _flat(
        "<picture>"
        '<source media="(min-width: 1200px)" data-srcset="https://s7/x?$png$&amp;wid=1199"/>'
        '<source media="(max-width: 599px)" data-srcset="https://s7/x?$png$&amp;wid=599"/>'
        '<img src="data:image/svg+xml;base64,PHN2Zz4=" data-src="https://s7/x"/>'
        "</picture>"
    )

    assert "wid=1199" in out
    assert "wid=599" not in out


@pytest.mark.parametrize("dead_media", ["(not all)", "not all"])
def test_a_source_whose_media_never_matches_is_excluded(dead_media):
    """A dark-mode asset parked behind a query no browser selects. The width
    descriptor is the point: without it, plain `<img src>` precedence already
    wins and the exclusion is never what the assertion proves."""
    out = _flat(
        "<picture>"
        f'<source media="{dead_media}" srcset="https://cdn/dark.png 2400w"/>'
        '<img src="https://cdn/light.png"/></picture>'
    )

    assert 'src="https://cdn/light.png"' in out
    assert "dark.png" not in out


def test_widest_srcset_candidate_wins():
    out = _flat(
        '<img src="https://cdn/small.jpg" '
        'srcset="https://cdn/a-2400.jpg 2400w, https://cdn/a-300.jpg 300w"/>'
    )

    assert 'src="https://cdn/a-2400.jpg"' in out


def test_precedence_still_applies_when_no_width_is_declared():
    """Apple declares no widths anywhere; src must keep winning there."""
    out = _flat('<img src="https://cdn/real.png" data-src="https://cdn/other.png"/>')

    assert 'src="https://cdn/real.png"' in out


def test_dead_build_metadata_is_dropped():
    """originalimagename names a source-tree file that does not ship, and leaks
    "~dark" filenames into every figure."""
    out = _flat('<img originalimagename="Art/S0758~dark.png" src="https://cdn/a.png"/>')

    assert "originalimagename" not in out
    assert "~dark" not in out
