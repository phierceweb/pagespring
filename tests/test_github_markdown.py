"""github_markdown — match + recursive acquire/normalize with a mocked GitHub API."""

import json

from pagespring import http
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
