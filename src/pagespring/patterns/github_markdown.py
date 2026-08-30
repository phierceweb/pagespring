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
from urllib.parse import quote, urlparse

from pf_core.log import get_logger
from pf_core.utils.slugify import slugify

from pagespring import http
from pagespring.base import AcquireResult

log = get_logger(__name__)

_API = "https://api.github.com"
_RAW = "https://raw.githubusercontent.com"
_MAX_FILES = 2000  # safety cap so an unscoped huge repo can't fan out forever
# Never content; documentation.md doubles as the TOC source.
_META = {"documentation.md", "license.md", "contributing.md", "changelog.md"}
_LINK_RE = re.compile(r"\]\(([^)]+)\)")


def _parse_repo(url: str) -> tuple[str, str, str | None, str]:
    """(owner, repo, branch|None, subdir) from a github.com URL."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    owner, repo = parts[0], parts[1]
    branch, subdir = None, ""
    if len(parts) >= 4 and parts[2] in ("tree", "blob"):
        branch, rest = parts[3], "/".join(parts[4:])
        # A tree URL names the directory to crawl; a blob names one file, so its
        # parent directory is the scope.
        subdir = rest if parts[2] == "tree" else rest.rpartition("/")[0]
    return owner, repo, branch, subdir


def _default_branch(owner: str, repo: str) -> str:
    _f, body = http.fetch_text(f"{_API}/repos/{owner}/{repo}")
    return str(json.loads(body).get("default_branch", "main"))


def _list_md(owner: str, repo: str, branch: str, subdir: str) -> tuple[dict[str, str], bool]:
    """All .md blobs under subdir (recursive) -> ({path: raw download URL}, tree_truncated)."""
    _f, body = http.fetch_text(f"{_API}/repos/{owner}/{repo}/git/trees/{branch}?recursive=1")
    data = json.loads(body)
    prefix = (subdir.rstrip("/") + "/") if subdir else ""
    out: dict[str, str] = {}
    for node in data.get("tree", []):
        path = node.get("path", "")
        if node.get("type") == "blob" and path.lower().endswith(".md") and path.startswith(prefix):
            # The key stays the real path; only the fetch URL is encoded, or a
            # space or non-ASCII name raises InvalidURL and the file is dropped.
            out[path] = f"{_RAW}/{owner}/{repo}/{quote(branch)}/{quote(path)}"
    # GitHub's own signal: the tree listing itself was cut short, so files are
    # missing before any cap of ours applies.
    tree_truncated = bool(data.get("truncated"))
    if tree_truncated:
        log.warning("github_markdown.tree_truncated", repo=f"{owner}/{repo}")
    return out, tree_truncated


def _basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def _is_meta(path: str) -> bool:
    """Repo meta, not content. README only at the root — below it, a README is the
    directory's own index page."""
    name = _basename(path).lower()
    return name in _META or (name == "readme.md" and "/" not in path)


def _ordered_content(md: dict[str, str]) -> list[str]:
    """Content paths in TOC order (via a root documentation.md if present, e.g.
    Laravel) else by path; meta excluded."""
    ordered: list[str] = []
    if "documentation.md" in md:
        try:
            _f, toc = http.fetch_text(md["documentation.md"])
        except Exception:
            toc = ""
        for target in _LINK_RE.findall(toc):
            seg = target.split("#")[0].split("?")[0].rstrip("/").rsplit("/", 1)[-1]
            name = re.sub(r"\.md$", "", seg) + ".md"
            if name in md and not _is_meta(name) and name not in ordered:
                ordered.append(name)
    rest = sorted(p for p in md if p not in ordered and not _is_meta(p))
    return ordered + rest


class GitHubMarkdownPattern:
    name = "github_markdown"

    def match(self, url: str) -> bool:
        p = urlparse(url)
        if p.netloc.lower() not in ("github.com", "www.github.com"):
            return False
        parts = [s for s in p.path.split("/") if s]
        if len(parts) < 2:  # /<owner>/<repo>
            return False
        # A blob naming a non-markdown file is a spec, PDF or archive that its
        # own pattern handles; claiming it here would crawl the whole repo.
        return not (
            len(parts) >= 5 and parts[2] == "blob" and not parts[-1].lower().endswith(".md")
        )

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        owner, repo, branch, subdir = _parse_repo(url)
        branch = branch or _default_branch(owner, repo)
        md, tree_truncated = _list_md(owner, repo, branch, subdir)
        order = _ordered_content(md)
        truncated = len(order) > _MAX_FILES or tree_truncated
        if len(order) > _MAX_FILES:
            log.warning("github_markdown.capped", found=len(order), cap=_MAX_FILES)
            order = order[:_MAX_FILES]

        raw_dir = workdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        saved = 0
        lost = 0
        for i, path in enumerate(order):
            try:
                _f, body = http.fetch_text(md[path])
            except Exception as exc:
                lost += 1
                log.warning("github_markdown.fetch_error", file=path, error=str(exc))
                continue
            flat = path.replace("/", "__")
            (raw_dir / f"{i:04d}-{flat}").write_text(
                f"<!-- source: {md[path]} -->\n\n{body}\n", encoding="utf-8"
            )
            saved += 1
            http.polite_sleep()

        # A last segment alone collides: "docs" for every repo that keeps its manual
        # there, "guide" for both locales of docs/<lang>/guide.
        scope = subdir.strip("/").replace("/", "-")
        slug_base = f"{owner}-{repo}-{scope}" if scope else f"{owner}-{repo}"
        slug = slugify(slug_base) or "docs"
        log.info(
            "github_markdown.acquire",
            repo=f"{owner}/{repo}",
            branch=branch,
            pages=saved,
            slug=slug,
            truncated=truncated,
            lost=lost,
        )
        return AcquireResult(
            raw_dir=raw_dir,
            kind="markdown",
            slug=slug,
            pages=saved,
            truncated=truncated,
            lost=lost,
        )

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        parts = [p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.md"))]
        out = workdir / f"{acq.slug}.md"
        out.write_text("\n\n---\n\n".join(parts), encoding="utf-8")
        log.info("github_markdown.normalize", slug=acq.slug, out=str(out), pages=len(parts))
        return out
