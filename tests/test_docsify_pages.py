"""_docsify_pages — the sidebar files a Docsify site is made of (mocked fetch)."""

from urllib.error import HTTPError

import pytest

from pagespring import http
from pagespring.patterns._docsify_pages import Page, sidebar_pages
from pagespring.patterns._docsify_routes import Site

_ROOT = "https://ex.test/"
_SHELL = "<!DOCTYPE html><html><body><div id='app'></div></body></html>"


def _site(ext: str = ".md", *, alias=(), relative: bool = False) -> Site:
    return Site(root=_ROOT, homepage="README.md", alias=alias, relative_links=relative, ext=ext)


def _serve(monkeypatch, files: dict[str, str]) -> list[str]:
    seen: list[str] = []

    def fetch(url, **kwargs):
        seen.append(url)
        if url not in files:
            raise HTTPError(url, 404, "Not Found", {}, None)  # type: ignore[arg-type]
        return url, files[url]

    monkeypatch.setattr(http, "fetch_text", fetch)
    return seen


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)


def _urls(pages: list[Page] | None) -> list[str]:
    return [p.url.removeprefix(_ROOT) for p in pages or []]


def test_the_default_sidebar_file_takes_the_ext_option(monkeypatch):
    seen = _serve(monkeypatch, {f"{_ROOT}_sidebar.markdown": "- [A](a.md)\n"})

    pages = sidebar_pages(_site(".markdown"), True, cap=10)

    assert pages == [Page(f"{_ROOT}a.markdown", "A", "/a")]
    assert seen == [f"{_ROOT}_sidebar.markdown"]


_NESTED = {
    f"{_ROOT}_sidebar.md": "- [Guide](guide/)\n- [Ref](api/ref.md)\n- [Top](top.md)\n",
    f"{_ROOT}guide/_sidebar.md": (
        "- [Home](/)\n- [Guide](guide/)\n  - [Intro](guide/intro.md)\n  - [Setup](guide/setup)\n"
    ),
}


def test_a_directory_sidebar_lists_its_pages_after_the_first_page_there(monkeypatch):
    """The homepage it repeats is left where the caller puts it: first."""
    seen = _serve(monkeypatch, _NESTED)

    pages = sidebar_pages(_site(), True, cap=10)

    assert _urls(pages) == [
        "guide/README.md",
        "guide/intro.md",
        "guide/setup.md",
        "api/ref.md",
        "top.md",
    ]
    assert seen == [
        f"{_ROOT}{name}" for name in ("_sidebar.md", "guide/_sidebar.md", "api/_sidebar.md")
    ]


def test_every_directory_up_to_the_root_is_probed_once(monkeypatch):
    files = {
        f"{_ROOT}_sidebar.md": "- [C](a/b/c.md)\n- [D](a/b/d.md)\n",
        f"{_ROOT}a/_sidebar.md": "- [E](a/e.md)\n",
    }
    seen = _serve(monkeypatch, files)

    pages = sidebar_pages(_site(), True, cap=10)

    assert _urls(pages) == ["a/b/c.md", "a/e.md", "a/b/d.md"]
    assert seen[1:] == [f"{_ROOT}a/b/_sidebar.md", f"{_ROOT}a/_sidebar.md"]


def test_an_alias_can_send_every_directory_to_the_root_sidebar(monkeypatch):
    seen = _serve(monkeypatch, _NESTED)

    pages = sidebar_pages(_site(alias=(("/.*/_sidebar.md", "/_sidebar.md"),)), True, cap=10)

    assert _urls(pages) == ["guide/README.md", "api/ref.md", "top.md"]
    assert seen == [f"{_ROOT}_sidebar.md"]


def test_a_named_sidebar_file_is_looked_up_under_each_directory(monkeypatch):
    files = {
        f"{_ROOT}nav/side.md": "- [Guide](guide/)\n",
        f"{_ROOT}guide/nav/side.md": "- [Intro](guide/intro.md)\n",
    }
    _serve(monkeypatch, files)

    assert _urls(sidebar_pages(_site(), "nav/side.md", cap=10)) == [
        "guide/README.md",
        "guide/intro.md",
    ]


def test_relative_links_in_a_directory_sidebar_resolve_against_it(monkeypatch):
    files = {
        f"{_ROOT}_sidebar.md": "- [Guide](guide/)\n",
        f"{_ROOT}guide/_sidebar.md": "- [Intro](intro.md)\n",
    }
    _serve(monkeypatch, files)

    pages = sidebar_pages(_site(relative=True), True, cap=10)

    assert _urls(pages) == ["guide/README.md", "guide/intro.md"]


def test_a_directory_sidebar_answered_by_the_app_shell_is_absent(monkeypatch):
    files = dict(_NESTED)
    files[f"{_ROOT}guide/_sidebar.md"] = _SHELL
    _serve(monkeypatch, files)

    assert _urls(sidebar_pages(_site(), True, cap=10)) == [
        "guide/README.md",
        "api/ref.md",
        "top.md",
    ]


def test_no_root_sidebar_means_no_directory_is_probed(monkeypatch):
    seen = _serve(monkeypatch, {f"{_ROOT}guide/_sidebar.md": "- [Intro](guide/intro.md)\n"})

    assert sidebar_pages(_site(), True, cap=10) is None
    assert seen == [f"{_ROOT}_sidebar.md"]


def test_directories_stop_being_probed_past_the_cap(monkeypatch):
    seen = _serve(monkeypatch, _NESTED)

    pages = sidebar_pages(_site(), True, cap=2)

    assert _urls(pages) == ["guide/README.md", "api/ref.md", "top.md"]
    assert seen == [f"{_ROOT}_sidebar.md"]


def _refuse(monkeypatch, files: dict[str, str], code: int) -> None:
    def fetch(url, **kwargs):
        if url not in files:
            raise HTTPError(url, code, "Refused", {}, None)  # type: ignore[arg-type]
        return url, files[url]

    monkeypatch.setattr(http, "fetch_text", fetch)


@pytest.mark.parametrize("code", [403, 503])
def test_a_directory_sidebar_that_fails_to_load_is_absent(monkeypatch, code):
    """S3 and CloudFront answer a missing key with 403; the runtime falls back on any failure."""
    _refuse(monkeypatch, {f"{_ROOT}_sidebar.md": "- [Intro](guide/intro.md)\n"}, code)

    assert _urls(sidebar_pages(_site(), True, cap=10)) == ["guide/intro.md"]


def test_a_root_sidebar_the_host_refuses_is_absent(monkeypatch):
    _refuse(monkeypatch, {}, 403)

    assert sidebar_pages(_site(), True, cap=10) is None


def test_an_unset_load_sidebar_probe_that_fails_is_absent(monkeypatch):
    _refuse(monkeypatch, {}, 503)

    assert sidebar_pages(_site(), None, cap=10) is None


def test_a_configured_root_sidebar_that_errors_is_a_fetch_failure(monkeypatch):
    _refuse(monkeypatch, {}, 503)

    with pytest.raises(HTTPError):
        sidebar_pages(_site(), True, cap=10)
