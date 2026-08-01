"""apple_help — Apple support web User Guides (support.apple.com/guide/<slug>/).

acquire: BFS-crawl every topic page under /guide/<slug>/ for the platform,
saving each page + welcome.html. normalize: strip Apple.com chrome and merge
the saved pages into one clean <slug>.html whose heading hierarchy comes from
the welcome TOC tree (see _apple_merge). Image src URLs stay absolute so
pagespeak downloads them.
"""

from __future__ import annotations

import re
import time
from collections import deque
from pathlib import Path
from urllib.parse import urljoin, urlparse

from pf_core.log import get_logger

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.config import cfg
from pagespring.liveness import ProgressWatchdog
from pagespring.patterns._apple_merge import build_merged_html

log = get_logger(__name__)

_MAX_PAGES = 6000  # a capped crawl sets truncated
# Apple links each topic BOTH as `<words>-<token>` and bare `<token>`; the two
# resolve to the same page. Dedup on the token or half the crawl re-fetches
# pages already on disk. Prefixes vary within one guide (lgcp/lgsi/lgce/ctls…).
_TOPIC_TOKEN_RE = re.compile(r"^[a-z]{2,6}[0-9a-f]{6,}$")


def _parse_apple_url(url: str) -> tuple[str, str]:
    """(slug, platform) from a support.apple.com/guide/<slug>/.../<mac|macos> URL."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    slug = parts[parts.index("guide") + 1] if "guide" in parts else parts[-1]
    platform = "macos" if "macos" in parts else "mac"
    return slug, platform


def _crawl(start_url: str, slug: str, platform: str, outdir: Path) -> tuple[int, bool]:
    """BFS every topic page under /guide/<slug>/ for the platform into outdir.

    Apple embeds the full TOC as JSON in every page, so topic paths are
    harvested by regex from the page text — no DOM parse needed at this stage.
    """
    path_re = re.compile(rf"/guide/{re.escape(slug)}/[A-Za-z0-9-]+/[0-9.]+/{platform}/[0-9.]+")

    def page_id(u: str) -> str:
        """The URL's topic segment — the raw filename, which _apple_merge matches
        TOC anchors against, so it stays descriptive."""
        segs = [p for p in urlparse(u).path.split("/") if p]
        return segs[2] if len(segs) >= 3 else "welcome"

    def topic_key(u: str) -> str:
        """Identity for dedup: the opaque token when the segment carries one."""
        seg = page_id(u)
        tail = seg.rsplit("-", 1)[-1]
        return tail if _TOPIC_TOKEN_RE.fullmatch(tail) else seg

    seen_ids: set[str] = {"welcome"}
    saved = 0
    queue: deque[str] = deque([start_url])
    watchdog = ProgressWatchdog(stall_after_s=cfg.CRAWL_STALL_AFTER_S, now=time.monotonic)
    while queue and saved < _MAX_PAGES:
        if watchdog.stalled():
            log.warning(
                "apple_help.stalled",
                saved=saved,
                idle_s=round(watchdog.idle_s()),
                queued=len(queue),
            )
            break
        url = queue.popleft()
        try:
            final_url, body = http.fetch_text(url)
        except Exception as exc:
            log.warning("apple_help.fetch_error", url=url, error=str(exc))
            continue
        # Mark the post-redirect identity seen too: a short-form URL lands on the
        # long form, and the long form is usually linked elsewhere as well.
        seen_ids.add(topic_key(final_url))
        out_path = outdir / f"{page_id(final_url)}.html"
        if not out_path.exists():
            out_path.write_text(body, encoding="utf-8")
            saved += 1
            watchdog.progress()
        for rel in path_re.findall(body):
            nxt = urljoin(final_url, rel)
            tid = topic_key(nxt)
            if tid not in seen_ids:
                seen_ids.add(tid)
                queue.append(nxt)
        http.polite_sleep()
    if queue:
        log.warning("apple_help.capped", saved=saved, cap=_MAX_PAGES, queued=len(queue))
    return saved, bool(queue)


class AppleHelpPattern:
    name = "apple_help"

    def match(self, url: str) -> bool:
        p = urlparse(url)
        host = p.netloc.lower()
        return (host == "support.apple.com" or host.endswith(".apple.com")) and "/guide/" in p.path

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        slug, platform = _parse_apple_url(url)
        raw_dir = workdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        pages, truncated = _crawl(url, slug, platform, raw_dir)
        log.info(
            "apple_help.acquire", slug=slug, platform=platform, pages=pages, truncated=truncated
        )
        return AcquireResult(
            raw_dir=raw_dir, kind="html", slug=slug, pages=pages, truncated=truncated
        )

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        out_path = workdir / f"{acq.slug}.html"
        merged = build_merged_html(acq.raw_dir, acq.slug)
        out_path.write_text(merged, encoding="utf-8")
        log.info("apple_help.normalize", slug=acq.slug, out=str(out_path), bytes=len(merged))
        return out_path
