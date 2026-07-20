"""github_markdown — docs kept as markdown in a GitHub repo (e.g. laravel/docs).

acquire: resolve the repo's default branch, list its ``.md`` files **recursively**
via the git-trees API (scoped to a subdir when the URL includes one), order them
by the repo's table-of-contents file if present (Laravel's ``documentation.md``)
else by path, and download each raw ``.md``. normalize: concatenate in order.

Point it at the repo: ``https://github.com/<owner>/<repo>`` — optionally
``/tree/<branch>`` or ``/tree/<branch>/<subdir>`` to scope a big/nested repo
(e.g. a single product area of MicrosoftDocs/*).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from pf_core.log import get_logger
from pf_core.utils.slugify import slugify

from pagespring import http
from pagespring.base import AcquireResult

log = get_logger(__name__)

_API = "https://api.github.com"
_RAW = "https://raw.githubusercontent.com"
_MAX_FILES = 2000  # safety cap so an unscoped huge repo can't fan out forever
# Navigation/meta files: used for ordering, not emitted as content.
_META = {"documentation.md", "readme.md", "license.md", "contributing.md", "changelog.md"}
_LINK_RE = re.compile(r"\]\(([^)]+)\)")


def _parse_repo(url: str) -> tuple[str, str, str | None, str]:
    """(owner, repo, branch|None, subdir) from a github.com URL."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    owner, repo = parts[0], parts[1]
    branch, subdir = None, ""
    if len(parts) >= 4 and parts[2] == "tree":
        branch, subdir = parts[3], "/".join(parts[4:])
    return owner, repo, branch, subdir


def _default_branch(owner: str, repo: str) -> str:
    _f, body = http.fetch_text(f"{_API}/repos/{owner}/{repo}")
    return str(json.loads(body).get("default_branch", "main"))


def _list_md(owner: str, repo: str, branch: str, subdir: str) -> dict[str, str]:
    """All .md blobs under subdir (recursive) -> {path: raw download URL}."""
    _f, body = http.fetch_text(f"{_API}/repos/{owner}/{repo}/git/trees/{branch}?recursive=1")
    data = json.loads(body)
    prefix = (subdir.rstrip("/") + "/") if subdir else ""
    out: dict[str, str] = {}
    for node in data.get("tree", []):
        path = node.get("path", "")
        if node.get("type") == "blob" and path.lower().endswith(".md") and path.startswith(prefix):
            out[path] = f"{_RAW}/{owner}/{repo}/{branch}/{path}"
    if data.get("truncated"):
        log.warning("github_markdown.tree_truncated", repo=f"{owner}/{repo}")
    return out


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _ordered_content(md: dict[str, str]) -> list[str]:
    """Content paths in TOC order (via a root documentation.md if present, e.g.
    Laravel) else by path; meta files (README/LICENSE/…) excluded."""
    ordered: list[str] = []
    if "documentation.md" in md:
        try:
            _f, toc = http.fetch_text(md["documentation.md"])
        except Exception:
            toc = ""
        for target in _LINK_RE.findall(toc):
            seg = target.split("#")[0].split("?")[0].rstrip("/").rsplit("/", 1)[-1]
            name = re.sub(r"\.md$", "", seg) + ".md"
            if name in md and name.lower() not in _META and name not in ordered:
                ordered.append(name)
    rest = sorted(p for p in md if p not in ordered and _basename(p).lower() not in _META)
    return ordered + rest


class GitHubMarkdownPattern:
    name = "github_markdown"
    convert_recipe = ["--split-sections"]

    def match(self, url: str) -> bool:
        p = urlparse(url)
        if p.netloc.lower() not in ("github.com", "www.github.com"):
            return False
        return len([s for s in p.path.split("/") if s]) >= 2  # /<owner>/<repo>

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        owner, repo, branch, subdir = _parse_repo(url)
        branch = branch or _default_branch(owner, repo)
        md = _list_md(owner, repo, branch, subdir)
        order = _ordered_content(md)
        if len(order) > _MAX_FILES:
            log.warning("github_markdown.capped", found=len(order), cap=_MAX_FILES)
            order = order[:_MAX_FILES]

        raw_dir = workdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        for i, path in enumerate(order):
            try:
                _f, body = http.fetch_text(md[path])
            except Exception as exc:
                log.warning("github_markdown.fetch_error", file=path, error=str(exc))
                continue
            flat = path.replace("/", "__")
            (raw_dir / f"{i:04d}-{flat}").write_text(
                f"<!-- source: {md[path]} -->\n\n{body}\n", encoding="utf-8"
            )
            saved += 1
            http.polite_sleep()

        slug_base = subdir.rstrip("/").split("/")[-1] if subdir else f"{owner}-{repo}"
        slug = slugify(slug_base) or "docs"
        log.info(
            "github_markdown.acquire", repo=f"{owner}/{repo}", branch=branch, pages=saved, slug=slug
        )
        return AcquireResult(raw_dir=raw_dir, kind="markdown", slug=slug, pages=saved)

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        parts = [p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.md"))]
        out = workdir / f"{acq.slug}.md"
        out.write_text("\n\n---\n\n".join(parts), encoding="utf-8")
        log.info("github_markdown.normalize", slug=acq.slug, out=str(out), pages=len(parts))
        return out
