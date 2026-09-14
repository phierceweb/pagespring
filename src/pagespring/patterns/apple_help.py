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

from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.config import cfg
from pagespring.liveness import ProgressWatchdog
from pagespring.patterns._apple_merge import build_merged_html, toc_topic_slugs

log = get_logger(__name__)

_MAX_PAGES = 6000  # a capped crawl sets truncated
# Apple links each topic BOTH as `<words>-<token>` and bare `<token>`; the two
# resolve to the same page. Dedup on the token or half the crawl re-fetches
# pages already on disk. Prefixes vary within one guide (lgcp/lgsi/lgce/ctls…).
_TOPIC_TOKEN_RE = re.compile(r"^[a-z]{2,6}(?:[0-9a-f]{6,}|_[a-z0-9]+)$")
_VERSION_SEG_RE = re.compile(r"^[0-9.]+$")


def _parse_apple_url(url: str) -> tuple[str, str | None]:
    """(slug, platform) from a support.apple.com/guide/<slug>/<topic>/[<ver>/]<platform> URL.

    The platform is None when the path names none.
    """
    parts = [p for p in urlparse(url).path.split("/") if p]
    if "guide" not in parts:
        return (parts[-1] if parts else ""), None
    i = parts.index("guide")
    platform = next((p for p in parts[i + 3 :] if not _VERSION_SEG_RE.fullmatch(p)), None)
    return parts[i + 1], platform


def _welcome_url(url: str, slug: str, platform: str | None) -> str:
    """The guide's welcome page for the seed's host, locale prefix, and release."""
    p = urlparse(url)
    prefix, _, tail = p.path.partition("/guide/")
    release = [s for s in tail.split("/") if s][2:] or ([platform] if platform else [])
    return "/".join([f"{p.scheme}://{p.netloc}{prefix}/guide/{slug}/welcome", *release])


def _topic_link_re(url: str, slug: str, platform: str) -> re.Pattern[str]:
    """Links to this guide's topics for the platform, in url's locale."""
    # A locale seed follows only its own locale's links; a US seed takes the bare form.
    locale = re.escape(urlparse(url).path.partition("/guide/")[0])
    return re.compile(
        rf"{locale}/guide/{re.escape(slug)}/[A-Za-z0-9_-]+(?:/[0-9.]+)?/{re.escape(platform)}"
        r"(?:/[0-9.]+)?(?![A-Za-z0-9_-])"
    )


def _crawl(
    start_url: str, slug: str, platform: str | None, outdir: Path
) -> tuple[int, bool, int, str]:
    """BFS every topic page under /guide/<slug>/ for the platform into outdir.

    Apple embeds the full TOC as JSON in every page, so topic paths are
    harvested by regex from the page text — no DOM parse needed at this stage.
    A seed naming no platform takes it, and its locale, from where Apple
    redirects the first fetch. Returns (saved, truncated, lost, platform).
    """
    path_re = _topic_link_re(start_url, slug, platform) if platform else None

    def page_id(u: str) -> str:
        """The URL's topic segment — the raw filename, which _apple_merge matches
        TOC anchors against, so it stays descriptive."""
        segs = [p for p in urlparse(u).path.split("/") if p]
        i = segs.index("guide") if "guide" in segs else -1
        return segs[i + 2] if i >= 0 and len(segs) > i + 2 else "welcome"

    def topic_key(u: str) -> str:
        """Identity for dedup: the opaque token when the segment carries one."""
        seg = page_id(u)
        tail = seg.rsplit("-", 1)[-1]
        return tail if _TOPIC_TOKEN_RE.fullmatch(tail) else seg

    seen_ids: set[str] = {"welcome", topic_key(start_url)}
    saved = 0
    lost = 0
    queue: deque[str] = deque([start_url])
    if page_id(start_url) != "welcome":
        # The merge builds its outline and title from welcome's TOC; topics never link back.
        queue.appendleft(_welcome_url(start_url, slug, platform))
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
            lost += 1
            log.warning("apple_help.fetch_error", url=url, error=str(exc))
            continue
        if path_re is None:
            landed = final_url if "/guide/" in urlparse(final_url).path else url
            platform = _parse_apple_url(landed)[1] or "mac"
            path_re = _topic_link_re(landed, slug, platform)
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
    return saved, bool(queue), lost, platform or "mac"


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
        pages, truncated, lost, platform = _crawl(url, slug, platform, raw_dir)
        log.info(
            "apple_help.acquire", slug=slug, platform=platform, pages=pages, truncated=truncated
        )
        return AcquireResult(
            raw_dir=raw_dir,
            kind="html",
            slug=slug,
            pages=pages,
            truncated=truncated,
            lost=lost,
        )

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        out_path = workdir / f"{acq.slug}.html"
        merged, dropped = build_merged_html(acq.raw_dir, acq.slug)
        topics = [p for p in acq.raw_dir.glob("*.html") if p.name != "welcome.html"]
        if len(dropped) == len(topics):
            # The titled wrapper alone is non-empty, so staging would accept it and
            # clear the guide it replaces.
            raise InvalidInputError(
                f"{acq.slug}: the crawl captured no topic page — the guide's link shape "
                "changed, or every fetch failed. Nothing was staged."
            )
        unfetched = sorted(
            toc_topic_slugs(acq.raw_dir / "welcome.html", acq.slug) - {p.stem for p in topics}
        )
        if unfetched:
            # The merge skips a TOC entry with no saved page, so a link shape the
            # crawl can't follow would otherwise shrink the guide silently.
            log.warning(
                "apple_help.toc_unfetched",
                slug=acq.slug,
                unfetched=len(unfetched),
                examples=unfetched[:5],
            )
        if dropped:
            # Counted by the crawl but absent from the merge, so `pages` alone
            # would describe topics the deliverable does not carry.
            log.warning(
                "apple_help.merge_dropped",
                slug=acq.slug,
                dropped=len(dropped),
                examples=dropped[:5],
            )
        # The larger, not the sum: a failed fetch is already in the crawl's count, and
        # a replay seeds `lost` with this normalize's own earlier result.
        acq.lost = max(acq.lost, len(unfetched) + len(dropped))
        out_path.write_text(merged, encoding="utf-8")
        log.info("apple_help.normalize", slug=acq.slug, out=str(out_path), bytes=len(merged))
        return out_path
