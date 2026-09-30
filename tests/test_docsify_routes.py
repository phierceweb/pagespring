"""_docsify_routes — route, link and file resolution from a Docsify config."""

import pytest

from pagespring.patterns._docsify_routes import (
    Site,
    asset_url,
    file_url,
    link_url,
    read_site,
    route_of,
)

_ROOT = "https://ex.test/docs/"


def _site(alias: tuple[tuple[str, str], ...] = (), *, relative: bool = False) -> Site:
    return Site(root=_ROOT, homepage="README.md", alias=alias, relative_links=relative)


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("#/configuration?id=options", ("/configuration", "options")),
        ("guide/", ("/guide/", "")),
        ("../a/./b.md#top", ("/a/b", "top")),
        ("/", ("/", "")),
        ("#local", None),
        ("https://other.test/x", None),
        ("//cdn.test/x", None),
        ("?id=only-a-query", None),
    ],
)
def test_route_of(target, expected):
    assert route_of(target) == expected


def test_aliases_apply_until_none_matches_and_a_bad_pattern_is_skipped():
    alias = (("([", "/never"), ("/a/(.*)", "/b/$1"), ("/b/(.*)", "/c/$1$2"))

    assert file_url("/a/x", _site(alias)) == f"{_ROOT}c/x$2.md"


def test_the_homepage_stands_in_for_the_root_readme():
    site = Site(root=_ROOT, homepage="intro.md", alias=(), relative_links=False)

    assert file_url("/", site) == f"{_ROOT}intro.md"
    assert file_url("/guide/", site) == f"{_ROOT}guide/README.md"


def test_links_resolve_from_the_root_unless_relative_path_is_set():
    assert link_url("b.md", "/a/page", _site()) == f"{_ROOT}b.md"
    assert link_url("b.md", "/a/page", _site(relative=True)) == f"{_ROOT}a/b.md"


def test_assets_resolve_from_the_page_directory():
    assert asset_url("img/x.png", "/a/page", _site()) == f"{_ROOT}a/img/x.png"
    assert asset_url("/img/x.png", "/a/page", _site()) == f"{_ROOT}img/x.png"


def test_read_site_takes_options_from_the_config_text():
    js = "basePath: '/md/', homepage: 'home.md', relativePath: true, alias: {'/x': '/y'}"

    site = read_site("https://ex.test/app/", js)

    assert site == Site(
        root="https://ex.test/md/", homepage="home.md", alias=(("/x", "/y"),), relative_links=True
    )


def test_a_route_loads_its_file_under_the_ext_option():
    site = Site(root=_ROOT, homepage="README.md", alias=(), relative_links=False, ext=".markdown")

    assert file_url("/guide/intro", site) == f"{_ROOT}guide/intro.markdown"
    assert file_url("/guide/", site) == f"{_ROOT}guide/README.markdown"
    assert file_url("/guide/intro.markdown", site) == f"{_ROOT}guide/intro.markdown"
    assert file_url("/", site) == f"{_ROOT}README.md"
    assert link_url("intro.md", "/", site) == f"{_ROOT}intro.markdown"


def test_a_version_dot_is_not_a_file_extension():
    assert file_url("/release-1.2", _site()) == f"{_ROOT}release-1.2.md"
    assert file_url("/files/ex.zip", _site()) == f"{_ROOT}files/ex.zip"


def test_read_site_takes_the_ext_option():
    assert read_site("https://ex.test/", "ext: '.markdown'").ext == ".markdown"
    assert read_site("https://ex.test/", "loadSidebar: true").ext == ".md"
