"""_shiki — shiki-highlighted code blocks flattened to plain text (pure, no network)."""

from bs4 import BeautifulSoup

from pagespring.patterns._shiki import flatten_shiki


def _flat(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(f"<div id='root'>{html}</div>", "html.parser")
    root = soup.find(id="root")
    flatten_shiki(root)
    return soup


def _line(*tokens: str, cls: str = "line") -> str:
    spans = "".join(
        f'<span style="--shiki-light:#1E754F;--shiki-dark:#F286C4">{t}</span>' for t in tokens
    )
    return f'<span class="{cls}">{spans}</span>'


def test_token_spans_flatten_to_newline_joined_text():
    html = (
        '<pre class="shiki shiki-themes vitesse-light vp-code" tabindex="0" '
        'style="--shiki-dark-bg:#000">'
        f'<code>{_line("import", " x")}\n<span class="line"></span>\n'
        f"{_line('x', '()')}</code></pre>"
    )
    soup = _flat(html)

    pre = soup.find("pre")
    assert str(pre) == "<pre><code>import x\n\nx()</code></pre>"


def test_lines_without_newlines_between_them_still_break():
    html = f'<pre class="shiki"><code>{_line("a")}{_line("b")}</code></pre>'

    assert _flat(html).find("code").get_text() == "a\nb"


def test_the_language_comes_from_the_code_the_pre_or_its_wrapper():
    wrapped = (
        '<div class="language-ts vp-adaptive-theme">'
        f'<pre class="shiki"><code>{_line("a")}</code></pre></div>'
    )
    on_pre = f'<pre class="shiki" data-language="sh"><code>{_line("a")}</code></pre>'
    on_code = f'<pre class="shiki"><code class="language-py">{_line("a")}</code></pre>'

    assert str(_flat(wrapped).find("code")) == '<code class="language-ts">a</code>'
    assert str(_flat(on_pre).find("code")) == '<code class="language-sh">a</code>'
    assert str(_flat(on_code).find("code")) == '<code class="language-py">a</code>'


def test_a_pre_without_a_shiki_class_is_recognised_by_its_token_styles():
    html = f"<pre><code>{_line('npm', ' i')}\n{_line('npm', ' test')}</code></pre>"

    assert str(_flat(html).find("pre")) == "<pre><code>npm i\nnpm test</code></pre>"


def test_diff_lines_keep_the_marks_the_theme_draws_in_the_gutter():
    html = (
        '<pre class="shiki has-diff"><code>'
        f"{_line('a')}\n{_line('old', cls='line diff remove')}\n{_line('new', cls='line diff add')}"
        "</code></pre>"
    )

    assert _flat(html).find("code").get_text() == " a\n-old\n+new"


def test_twoslash_popups_are_dropped_from_the_code():
    html = (
        '<pre class="shiki twoslash"><code><span class="line"><span class="twoslash-hover">'
        '<span class="twoslash-popup-container"><code>const x: number</code></span>'
        '<span style="--shiki-light:#000">x</span></span></span></code></pre>'
    )

    assert _flat(html).find("pre").get_text() == "x"


def test_other_code_blocks_are_left_alone():
    prism = (
        '<pre class="prism-code language-js"><code><span class="token-line">'
        '<span class="token keyword">let</span></span></code></pre>'
    )
    plain = '<pre><code class="language-sh">npm i</code></pre>'
    linked = '<pre><code>see <a href="x.html">x</a></code></pre>'

    for html in (prism, plain, linked):
        assert str(_flat(html).find("pre")) == html
