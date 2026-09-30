"""github_markdown — match + recursive acquire/normalize with a mocked GitHub API."""

import json

import pytest

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns import github_markdown
from pagespring.patterns.github_markdown import GitHubMarkdownPattern

_REPO = '{"default_branch": "13.x"}'
# git-trees recursive response: flat root files + a nested subdir file.
_TREE = json.dumps(
    {
        "tree": [
            {"path": "documentation.md", "type": "blob"},
            {"path": "installation.md", "type": "blob"},
            {"path": "routing.md", "type": "blob"},
            {"path": "license.md", "type": "blob"},
            {"path": "guides", "type": "tree"},
            {"path": "guides/deploy.md", "type": "blob"},
            {"path": "art/logo.png", "type": "blob"},
        ],
        "truncated": False,
    }
)
_DOCUMENTATION = (
    "- [Routing](/docs/{{version}}/routing)\n- [Installation](/docs/{{version}}/installation)\n"
)
_PAGES = {
    "https://raw.githubusercontent.com/laravel/docs/13.x/documentation.md": _DOCUMENTATION,
    "https://raw.githubusercontent.com/laravel/docs/13.x/installation.md": "# Installation\nInstall.",
    "https://raw.githubusercontent.com/laravel/docs/13.x/routing.md": "# Routing\nRoutes.",
    "https://raw.githubusercontent.com/laravel/docs/13.x/license.md": "MIT License",
    "https://raw.githubusercontent.com/laravel/docs/13.x/guides/deploy.md": "# Deploy\nShip it.",
}


def _fake_fetch_text(url, **kwargs):
    if url.endswith("/repos/laravel/docs"):
        return url, _REPO
    if "/git/trees/" in url:
        return url, _TREE
    return url, _PAGES[url]


def test_match():
    p = GitHubMarkdownPattern()
    assert p.match("https://github.com/laravel/docs")
    assert p.match("https://github.com/MicrosoftDocs/OfficeDocs/tree/public/sub/area")
    assert not p.match("https://github.com/laravel")
    assert not p.match("https://gitlab.com/x/y")


def test_acquire_recursive_orders_by_toc_excludes_meta(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = GitHubMarkdownPattern()

    acq = p.acquire("https://github.com/laravel/docs", tmp_path)
    assert acq.kind == "markdown"
    assert acq.slug == "laravel-docs"
    assert acq.pages == 3  # routing, installation, guides/deploy — meta excluded

    text = p.normalize(acq, tmp_path).read_text(encoding="utf-8")
    # TOC order first (Routing before Installation), then nested rest (guides/deploy).
    assert text.index("# Routing") < text.index("# Installation")
    assert "# Deploy" in text  # nested subdir file picked up recursively
    assert "MIT License" not in text  # meta excluded
    assert "/docs/{{version}}/routing" not in text  # the TOC file itself not emitted


def _acquire_two_file_repo(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fake_fetch_text)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    return GitHubMarkdownPattern().acquire("https://github.com/laravel/docs", tmp_path)


def test_capped_file_list_marks_truncated(tmp_path, monkeypatch):
    """A repo with more markdown than the cap yields a partial deliverable — the
    manifest must say so, or audit sees a healthy-looking doc."""
    from pagespring.patterns import github_markdown as mod

    monkeypatch.setattr(mod, "_MAX_FILES", 1)
    acq = _acquire_two_file_repo(tmp_path, monkeypatch)
    assert acq.truncated is True


def test_uncapped_file_list_is_not_truncated(tmp_path, monkeypatch):
    acq = _acquire_two_file_repo(tmp_path, monkeypatch)
    assert acq.truncated is False


def test_files_lost_to_fetch_errors_are_counted(tmp_path, monkeypatch):
    """A raw.githubusercontent failure drops one listed file; uncounted, the
    manifest reports a complete crawl and audit sees nothing missing."""

    def fake(url, **kwargs):
        if url.endswith("/routing.md"):
            raise OSError("503 throttled")
        return _fake_fetch_text(url, **kwargs)

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    acq = GitHubMarkdownPattern().acquire("https://github.com/laravel/docs", tmp_path)

    assert acq.pages == 2
    assert acq.lost == 1
    assert acq.truncated is False


def test_a_blob_url_honours_its_branch_and_directory():
    """A blob URL carries both the branch and the path to crawl; recognising only `tree`
    discards them and crawls the whole repo at the default branch."""
    owner, repo, branch, subdir = github_markdown._parse_repo(
        "https://github.com/laravel/docs/blob/9.x/queues.md"
    )
    assert (owner, repo, branch, subdir) == ("laravel", "docs", "9.x", "")

    assert github_markdown._parse_repo("https://github.com/o/r/blob/main/docs/guide/intro.md") == (
        "o",
        "r",
        "main",
        "docs/guide",
    )
    # tree URLs are unchanged: the whole remainder is the directory
    assert github_markdown._parse_repo("https://github.com/o/r/tree/main/docs/guide") == (
        "o",
        "r",
        "main",
        "docs/guide",
    )


def test_a_non_markdown_blob_is_left_to_its_own_pattern():
    """github_markdown outranks api_spec/pdf_url in the registry, so claiming a spec or
    PDF blob crawls the repo instead of ingesting the file."""
    p = GitHubMarkdownPattern()
    assert p.match("https://github.com/o/r")
    assert p.match("https://github.com/o/r/blob/main/docs/intro.md")
    assert not p.match("https://github.com/o/r/blob/main/openapi.json")
    assert not p.match("https://github.com/o/r/blob/main/manual.pdf")


def test_raw_urls_percent_encode_spaces_and_unicode(monkeypatch):
    """urllib raises InvalidURL for a raw path carrying a space or a non-ASCII character,
    dropping every such file from the deliverable."""
    tree = json.dumps(
        {
            "tree": [
                {"type": "blob", "path": "docs/getting started.md"},
                {"type": "blob", "path": "docs/快速开始.md"},
            ]
        }
    )
    monkeypatch.setattr(http, "fetch_text", lambda url, **kw: (url, tree))

    md, _truncated = github_markdown._list_md("o", "r", "main", "docs")

    assert md["docs/getting started.md"].endswith("/main/docs/getting%20started.md")
    assert md["docs/快速开始.md"].endswith("/main/docs/%E5%BF%AB%E9%80%9F%E5%BC%80%E5%A7%8B.md")
    assert " " not in md["docs/getting started.md"], "space reached the fetch URL"


_SCOPED_TREE = json.dumps(
    {
        "tree": [
            {"path": "README.md", "type": "blob"},
            {"path": "CONTRIBUTING.md", "type": "blob"},
            {"path": "docs", "type": "tree"},
            {"path": "docs/README.md", "type": "blob"},
            {"path": "docs/usage.md", "type": "blob"},
        ],
        "truncated": False,
    }
)
_SCOPED_PAGES = {
    "https://raw.githubusercontent.com/phierceweb/pagespring/main/docs/README.md": "# Docs index\nThe table.",
    "https://raw.githubusercontent.com/phierceweb/pagespring/main/docs/usage.md": "# Usage\nDrive it.",
}


def _fake_scoped_fetch(url, **kwargs):
    if "/git/trees/" in url:
        return url, _SCOPED_TREE
    return url, _SCOPED_PAGES[url]


def _acquire_scoped(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fake_scoped_fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    return GitHubMarkdownPattern().acquire(
        "https://github.com/phierceweb/pagespring/blob/main/docs/usage.md", tmp_path
    )


def test_a_readme_inside_a_directory_is_content_not_meta(tmp_path, monkeypatch):
    """A docs directory's README is its index page — the page the reader starts on."""
    acq = _acquire_scoped(tmp_path, monkeypatch)

    assert acq.pages == 2
    text = GitHubMarkdownPattern().normalize(acq, tmp_path).read_text(encoding="utf-8")
    assert "# Docs index" in text
    assert "# Usage" in text


def test_a_subdir_scoped_slug_carries_its_repo(tmp_path, monkeypatch):
    """`docs` alone names every repo that keeps its manual there."""
    acq = _acquire_scoped(tmp_path, monkeypatch)

    assert acq.slug == "phierceweb-pagespring-docs"


_MIXED_TREE = json.dumps(
    {
        "tree": [
            {"path": "README.md", "type": "blob"},
            {"path": "LICENSE.md", "type": "blob"},
            {"path": "docs/README.md", "type": "blob"},
            {"path": "docs/guide.md", "type": "blob"},
            {"path": "docs/documentation.md", "type": "blob"},
            {"path": "packages/a/CHANGELOG.md", "type": "blob"},
        ],
        "truncated": False,
    }
)
_MIXED_PAGES = {
    "README.md": "# The repo",
    "LICENSE.md": "MIT License",
    "docs/README.md": "# Docs index",
    "docs/guide.md": "# Guide",
    "docs/documentation.md": "- [Guide](/docs/guide)",
    "packages/a/CHANGELOG.md": "## 1.2.3 released",
}


def _fake_mixed_fetch(url, **kwargs):
    if url.endswith("/repos/o/r"):
        return url, '{"default_branch": "main"}'
    if "/git/trees/" in url:
        return url, _MIXED_TREE
    return url, _MIXED_PAGES[url.rsplit("/main/", 1)[-1]]


def _mixed_text(tmp_path, monkeypatch):
    monkeypatch.setattr(http, "fetch_text", _fake_mixed_fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = GitHubMarkdownPattern()
    acq = p.acquire("https://github.com/o/r", tmp_path)
    return acq, p.normalize(acq, tmp_path).read_text(encoding="utf-8")


def test_repo_meta_is_excluded_at_every_depth(tmp_path, monkeypatch):
    """A vendored LICENSE and a per-package CHANGELOG are meta wherever they sit."""
    acq, text = _mixed_text(tmp_path, monkeypatch)

    assert acq.pages == 2
    assert "# Docs index" in text
    assert "# Guide" in text
    assert "1.2.3 released" not in text
    assert "MIT License" not in text
    assert "# The repo" not in text


def test_a_nested_toc_file_is_not_emitted_as_content(tmp_path, monkeypatch):
    """The TOC branch keys on the root `documentation.md`, so a nested one orders nothing
    — staging it as content spills a page of raw template links into the deliverable."""
    _acq, text = _mixed_text(tmp_path, monkeypatch)

    assert "[Guide](/docs/guide)" not in text


_LOCALE_TREE = json.dumps(
    {
        "tree": [
            {"path": "docs/en/guide/intro.md", "type": "blob"},
            {"path": "docs/ja/guide/intro.md", "type": "blob"},
        ],
        "truncated": False,
    }
)


def test_two_locales_of_one_manual_do_not_fold_onto_one_slug(tmp_path, monkeypatch):
    """Two locales of one manual differ only above their last segment."""
    monkeypatch.setattr(
        http,
        "fetch_text",
        lambda url, **k: (url, _LOCALE_TREE) if "/git/trees/" in url else (url, "# Intro"),
    )
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = GitHubMarkdownPattern()

    en = p.acquire("https://github.com/o/r/tree/main/docs/en/guide", tmp_path / "en")
    ja = p.acquire("https://github.com/o/r/tree/main/docs/ja/guide", tmp_path / "ja")

    assert en.slug != ja.slug
    assert (en.slug, ja.slug) == ("o-r-docs-en-guide", "o-r-docs-ja-guide")


def test_only_repo_tree_and_markdown_blob_urls_are_claimed():
    """A release asset, raw file or source archive is a file its own pattern ingests;
    claiming it here stages the repo's markdown instead."""
    p = GitHubMarkdownPattern()
    assert p.match("https://github.com/o/r/")
    assert p.match("https://github.com/o/r/tree/main")
    assert p.match("https://github.com/o/r/tree/main/docs/guide")
    assert p.match("https://github.com/o/r/blob/main/docs/Intro.MD")
    for url in (
        "https://github.com/o/r/releases/download/v1/manual.pdf",
        "https://github.com/o/r/releases/download/v1/docs.zip",
        "https://github.com/o/r/raw/main/openapi.yaml",
        "https://github.com/o/r/raw/main/docs/intro.md",
        "https://github.com/o/r/archive/refs/tags/v1.tar.gz",
        "https://github.com/o/r/issues",
        "https://github.com/o/r/wiki",
        "https://github.com/o/r/tree",
    ):
        assert not p.match(url), url


def _tree_fetch(paths, fail=()):
    tree = json.dumps({"tree": [{"path": p, "type": "blob"} for p in paths]})

    def fake(url, **kwargs):
        if url.endswith("/repos/o/r"):
            return url, '{"default_branch": "main"}'
        if "/git/trees/" in url:
            return url, tree
        path = url.rsplit("/main/", 1)[-1]
        if path in fail:
            raise OSError("503 throttled")
        return url, f"# page {path}"

    return fake


def test_an_uppercase_md_extension_reaches_the_deliverable(tmp_path, monkeypatch):
    """Every page acquire counts must be one normalize concatenates."""
    monkeypatch.setattr(http, "fetch_text", _tree_fetch(["docs/intro.md", "docs/Usage.MD"]))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = GitHubMarkdownPattern()

    acq = p.acquire("https://github.com/o/r", tmp_path)
    text = p.normalize(acq, tmp_path).read_text(encoding="utf-8")

    assert acq.pages == 2
    assert "# page docs/intro.md" in text
    assert "# page docs/Usage.MD" in text


def test_a_file_at_a_long_path_stages_under_a_short_file_name(tmp_path, monkeypatch):
    long_path = "docs/" + "a" * 300 + ".mdx"  # past a file name's 255-byte limit
    monkeypatch.setattr(http, "fetch_text", _tree_fetch(["docs/intro.md", long_path]))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = GitHubMarkdownPattern()

    acq = p.acquire("https://github.com/o/r", tmp_path)
    text = p.normalize(acq, tmp_path).read_text(encoding="utf-8")

    assert acq.pages == 2
    assert f"# page {long_path}" in text
    assert max(len(f.name) for f in acq.raw_dir.iterdir()) <= 100


def test_numbered_files_order_numerically(tmp_path, monkeypatch):
    monkeypatch.setattr(
        http, "fetch_text", _tree_fetch(["10-advanced.md", "2-install.md", "1-intro.md"])
    )
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = GitHubMarkdownPattern()

    text = p.normalize(p.acquire("https://github.com/o/r", tmp_path), tmp_path).read_text(
        encoding="utf-8"
    )

    assert text.index("1-intro") < text.index("2-install") < text.index("10-advanced")


def test_a_failed_fetch_still_paces_the_next_request(tmp_path, monkeypatch):
    sleeps: list[None] = []
    monkeypatch.setattr(
        http, "fetch_text", _tree_fetch(["a.md", "b.md", "c.md"], fail={"a.md", "b.md"})
    )
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: sleeps.append(None))

    acq = GitHubMarkdownPattern().acquire("https://github.com/o/r", tmp_path)

    assert (acq.pages, acq.lost) == (1, 2)
    assert len(sleeps) == 3


def test_a_directory_readme_leads_its_siblings(tmp_path, monkeypatch):
    """A nested README is its directory's index page, so it opens that directory."""
    monkeypatch.setattr(
        http,
        "fetch_text",
        _tree_fetch(["docs/usage.md", "docs/sub/b.md", "docs/README.md", "docs/api.md"]),
    )
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = GitHubMarkdownPattern()

    text = p.normalize(p.acquire("https://github.com/o/r", tmp_path), tmp_path).read_text(
        encoding="utf-8"
    )

    order = ["docs/README.md", "docs/api.md", "docs/sub/b.md", "docs/usage.md"]
    assert [text.index(f"# page {path}") for path in order] == sorted(
        text.index(f"# page {path}") for path in order
    )


def test_mdx_pages_are_listed_and_reach_the_deliverable_without_jsx(tmp_path, monkeypatch):
    pages = {
        "docs/intro.mdx": 'import X from "./x"\n\n# Intro\n\n<Callout>\nRead this.\n</Callout>\n',
        "docs/guide.md": "# Guide\n\n<Keep>markdown is not MDX</Keep>\n",
    }
    tree = json.dumps({"tree": [{"path": p, "type": "blob"} for p in pages]})

    def fake(url, **kwargs):
        if url.endswith("/repos/o/r"):
            return url, '{"default_branch": "main"}'
        if "/git/trees/" in url:
            return url, tree
        return url, pages[url.rsplit("/main/", 1)[-1]]

    monkeypatch.setattr(http, "fetch_text", fake)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = GitHubMarkdownPattern()

    acq = p.acquire("https://github.com/o/r", tmp_path)
    text = p.normalize(acq, tmp_path).read_text(encoding="utf-8")

    assert acq.pages == 2
    assert "Read this." in text
    assert "<Callout>" not in text
    assert "import X" not in text
    assert "<Keep>markdown is not MDX</Keep>" in text


def test_an_mdx_blob_url_is_claimed():
    p = GitHubMarkdownPattern()
    assert p.match("https://github.com/vercel/next.js/blob/canary/docs/index.mdx")
    assert github_markdown._parse_repo(
        "https://github.com/vercel/next.js/blob/canary/docs/01-app/index.mdx"
    ) == ("vercel", "next.js", "canary", "docs/01-app")


def test_a_directory_index_leads_its_siblings(tmp_path, monkeypatch):
    """`index.mdx` is the page a docs directory opens on, like its README."""
    monkeypatch.setattr(
        http,
        "fetch_text",
        _tree_fetch(
            ["docs/01-install.mdx", "docs/index.mdx", "docs/02-pages/index.mdx", "index.md"]
        ),
    )
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = GitHubMarkdownPattern()

    text = p.normalize(p.acquire("https://github.com/o/r", tmp_path), tmp_path).read_text(
        encoding="utf-8"
    )

    order = ["index.md", "docs/index.mdx", "docs/01-install.mdx", "docs/02-pages/index.mdx"]
    positions = [text.index(f"# page {path}") for path in order]
    assert positions == sorted(positions)


def test_repo_meta_in_mdx_is_excluded(tmp_path, monkeypatch):
    monkeypatch.setattr(
        http,
        "fetch_text",
        _tree_fetch(["README.mdx", "CHANGELOG.mdx", "docs/README.mdx", "docs/guide.mdx"]),
    )
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    p = GitHubMarkdownPattern()

    acq = p.acquire("https://github.com/o/r", tmp_path)
    text = p.normalize(acq, tmp_path).read_text(encoding="utf-8")

    assert acq.pages == 2
    assert "# page docs/README.mdx" in text
    assert "# page README.mdx" not in text
    assert "CHANGELOG" not in text


@pytest.mark.parametrize("suffix", [".md", ".mdx"])
def test_front_matter_gives_way_to_its_title_as_the_page_heading(tmp_path, suffix):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / f"0000-intro{suffix}").write_text(
        "<!-- source: https://x/intro -->\n\n---\ntitle: Getting Started\n"
        "description: Learn the basics.\n---\n\nWelcome to the docs.\n",
        encoding="utf-8",
    )
    (raw / f"0001-setup{suffix}").write_text(
        "---\ntitle: Setup\n---\n\n# Installing\n\nRun the installer.\n", encoding="utf-8"
    )
    acq = AcquireResult(raw_dir=raw, kind="markdown", slug="repo", pages=2)

    out = GitHubMarkdownPattern().normalize(acq, tmp_path).read_text(encoding="utf-8")

    assert "title:" not in out and "description:" not in out
    assert "<!-- source: https://x/intro -->\n\n# Getting Started\n\nWelcome to the docs." in out
    assert "# Installing\n\nRun the installer." in out and "# Setup" not in out
