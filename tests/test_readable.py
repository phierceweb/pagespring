"""_readable — main-content extraction for crawled docs pages (pure, no network)."""

import pytest
from bs4 import BeautifulSoup

from pagespring.patterns import _readable


def _main(html: str):
    return _readable.main_content(BeautifulSoup(html, "html.parser"))


def test_a_dominant_article_inside_main_beats_main():
    node = _main(
        "<main><div class='toc'>On this page</div>"
        "<article><h1>Title</h1><p>" + "body text " * 20 + "</p></article></main>"
    )
    assert node is not None and node.name == "article"


def test_main_wins_when_its_articles_are_cards():
    node = _main(
        "<main><h1>Home</h1><p>" + "intro " * 20 + "</p>"
        "<article class='card'>one</article><article class='card'>two</article></main>"
    )
    assert node is not None and node.name == "main"


def test_role_main_then_a_sole_article_then_the_densest_block():
    assert _main("<div role='main'><p>x</p></div><article>y</article>").get("role") == "main"
    assert _main("<div><article><p>only</p></article></div>").name == "article"
    node = _main(
        "<div id='menu'><p>a</p></div>"
        "<div id='body'><p>" + "real prose " * 30 + "</p><p>more prose</p></div>"
    )
    assert node is not None and node.get("id") == "body"


def test_a_page_with_no_text_block_has_no_main_content():
    assert _main("<html><body><img src='x.png'></body></html>") is None


def _clean(html: str, url: str = "https://d.test/guide/page/") -> str:
    soup = BeautifulSoup(f"<div id='root'>{html}</div>", "html.parser")
    root = soup.find(id="root")
    _readable.clean(root, url)
    return str(root)


def test_expressive_code_lines_keep_their_line_breaks():
    """Expressive Code renders each line as a div with no newline between them,
    so a converter reading the <pre> text runs every line together."""
    html = (
        "<div class='expressive-code'><figure class='frame'>"
        "<figcaption class='header'><span class='title'>app.sh</span>"
        "<span class='sr-only'>Terminal window</span></figcaption>"
        "<pre data-language='sh'><code>"
        "<div class='ec-line'><div class='gutter'><div class='ln'>1</div></div>"
        "<div class='code'><span>npm</span><span> install</span></div></div>"
        "<div class='ec-line'><div class='code'>\n</div></div>"
        "<div class='ec-line'><div class='code'><span>npm run build</span></div></div>"
        "</code></pre>"
        "<div class='copy'><button data-code='npm install'><div></div></button></div>"
        "</figure></div>"
    )
    out = BeautifulSoup(_clean(html), "html.parser")

    code = out.find("code")
    assert code.get_text() == "npm install\n\nnpm run build"
    assert code.get("class") == ["language-sh"]
    assert out.find("button") is None
    assert "Terminal window" not in out.get_text()
    assert "app.sh" in out.get_text()


def test_shiki_token_spans_flatten_to_plain_code():
    html = (
        '<figure class="code-block shiki"><pre data-language="js"><code>'
        '<span class="line"><span style="--shiki-dark:#B392F0">npm</span>'
        '<span style="--shiki-dark:#9ECBFF"> i</span></span>'
        '<span class="line"><span style="--shiki-dark:#B392F0">npm test</span></span>'
        "</code></pre></figure>"
    )

    assert '<pre><code class="language-js">npm i\nnpm test</code></pre>' in _clean(html)


def test_screen_reader_text_and_hidden_icons_are_dropped():
    out = _clean(
        "<h2>Setup</h2><a href='#setup'><span aria-hidden='true'><svg><path/></svg></span>"
        "<span class='sr-only'>Section titled Setup</span></a>"
        "<p>Keep <svg role='img'><title>diagram</title></svg></p>"
        "<script>alert(1)</script><link rel='stylesheet' href='x.css'>"
    )
    assert "Section titled" not in out
    assert out.count("<svg") == 1  # the aria-hidden icon goes, the labelled image stays
    assert "<script" not in out and "<link" not in out


def test_tab_panels_are_unhidden_and_carry_their_labels():
    out = _clean(
        "<div><ul role='tablist'>"
        "<li><a role='tab' id='tab-0' href='#panel-0'>npm</a></li>"
        "<li><a role='tab' id='tab-1' href='#panel-1'>pnpm</a></li></ul>"
        "<div role='tabpanel' id='panel-0' aria-labelledby='tab-0'><p>npm i</p></div>"
        "<div role='tabpanel' id='panel-1' aria-labelledby='tab-1' hidden><p>pnpm add</p></div>"
        "</div>"
    )
    soup = BeautifulSoup(out, "html.parser")
    panels = soup.find_all(attrs={"role": "tabpanel"})
    assert [p.find("strong").get_text() for p in panels] == ["npm", "pnpm"]
    assert not any(p.has_attr("hidden") for p in panels)
    assert soup.find(attrs={"role": "tablist"}) is None


def test_a_tablist_whose_panels_name_no_tab_is_kept():
    out = _clean(
        "<ul role='tablist'><li role='tab'>npm</li></ul>"
        "<div role='tabpanel' hidden><p>npm i</p></div>"
    )
    assert "tablist" in out
    assert "hidden" not in out


def test_refs_are_absolutized_and_responsive_images_flattened():
    out = _clean(
        "<a href='../other/'>o</a><img src='data:image/gif;base64,R0lGOD' data-src='img/shot.png'>"
    )
    assert 'href="https://d.test/guide/other/"' in out
    assert 'src="https://d.test/guide/page/img/shot.png"' in out


def test_extract_main_strips_site_chrome_and_refuses_an_empty_page():
    page = (
        "<html><body><header>Site header</header><main>"
        "<nav>Breadcrumbs</nav><h1>Title</h1><p>Body.</p>"
        "<footer>Edit this page</footer></main></body></html>"
    )
    out = _readable.extract_main(page, "https://d.test/p/")

    assert out is not None
    assert "Body." in out and "Title" in out
    assert "Breadcrumbs" not in out and "Edit this page" not in out
    assert (
        _readable.extract_main("<html><body><main> </main></body></html>", "https://d.test/")
        is None
    )


@pytest.mark.parametrize(
    "anchor",
    [
        '<a class="headerlink" href="#setup" title="Permanent link"></a>',
        '<a class="header-anchor" href="#setup">#</a>',
        '<a href="#setup">¶</a>',
    ],
)
def test_heading_permalinks_are_dropped(anchor):
    html = f"<main><h2 id='setup'>Setup{anchor}</h2><p>Install it. See <a href='#setup'>setup</a>.</p></main>"
    out = _readable.extract_main(html, "https://docs.test/page/")
    assert out is not None
    assert "Setup</h2>" in out and "" not in out and "¶" not in out and ">#<" not in out
    assert ">setup</a>" in out


def test_an_in_page_link_around_an_image_stays():
    html = "<main><p>Figure:</p><p><a href='#fig'><img src='f.png'></a></p><p>Body text.</p></main>"
    out = _readable.extract_main(html, "https://docs.test/page/")
    assert out is not None and "f.png" in out


def test_a_button_heading_a_disclosure_keeps_its_text_and_controls_go():
    out = _clean(
        "<h2 class='accordion-header'><button aria-expanded='false' aria-controls='a1'>"
        "How do I reset?</button></h2><div id='a1'><p>Hold power.</p></div>"
        "<pre><code>x</code></pre><button class='copy' title='Copy'>Copy</button>"
    )
    assert '<h2 class="accordion-header">How do I reset?</h2>' in out
    assert "<button" not in out and "Copy" not in out
