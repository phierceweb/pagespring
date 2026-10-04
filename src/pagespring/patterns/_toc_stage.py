"""A TOC staged in reading order: each topic's section goes to raw/ under the headings of the entries
above it that staged nothing, so nesting survives a skipped, failed or repeated topic."""

from __future__ import annotations

import time
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pf_core.exceptions import FlowException
from pf_core.log import get_logger

from pagespring import http
from pagespring.config import cfg
from pagespring.liveness import ProgressWatchdog
from pagespring.patterns._site import toc_heading

log = get_logger(__name__)


class TocItem(Protocol):
    """An entry of a TOC in reading order, at its nesting depth."""

    @property
    def depth(self) -> int: ...

    @property
    def title(self) -> str: ...


class TopicLost(FlowException):
    """A topic the TOC lists that could not be staged; it counts as lost."""


@dataclass(frozen=True)
class Section:
    """A topic ready to stage: ``digest`` stages identical topics once, ``source`` and ``stem``
    label its raw file."""

    html: str
    digest: str
    source: str
    stem: str


@dataclass(frozen=True)
class Staged:
    """What staging came to: pages written, topics lost, and whether a stall cut it short."""

    pages: int
    lost: int
    stalled: bool


def stage_toc[T: TocItem](
    items: Sequence[T],
    raw_dir: Path,
    *,
    key: Callable[[T], Hashable | None],
    fetch: Callable[[T], Section | None],
    max_pages: int,
    event: str,
) -> Staged:
    """Stage ``items`` into ``raw_dir``: ``key`` None makes an entry a heading only, ``fetch`` None
    an empty topic. ``fetch`` raises ``TopicLost`` for a topic that cannot be staged.
    """
    pending: list[str] = []
    fetched: set[Hashable] = set()
    digests: set[str] = set()
    saved = lost = 0
    watchdog = ProgressWatchdog(stall_after_s=cfg.CRAWL_STALL_AFTER_S, now=time.monotonic)
    for i, item in enumerate(items):
        name = key(item)
        if name is None:
            pending.append(toc_heading(item.depth, item.title))
            continue
        # A topic skipped below still heads the topics under it, or they would re-parent.
        parent = i + 1 < len(items) and items[i + 1].depth > item.depth
        kept = [toc_heading(item.depth, item.title)] if parent else []
        if name in fetched:
            pending.extend(kept)
            continue
        if len(fetched) >= max_pages:
            break
        if watchdog.stalled():
            log.warning(f"{event}.stalled", saved=saved, idle_s=round(watchdog.idle_s()))
            return Staged(saved, lost, stalled=True)
        fetched.add(name)
        http.polite_sleep()
        try:
            section = fetch(item)
        except TopicLost as exc:
            lost += 1
            log.warning(f"{event}.lost", topic=item.title, error=str(exc))
            pending.extend(kept)
            continue
        if section is None or section.digest in digests:
            if section is not None:
                log.info(f"{event}.duplicate_page", source=section.source)
            pending.extend(kept)
            continue
        digests.add(section.digest)
        (raw_dir / f"{saved:04d}-{section.stem}.html").write_text(
            f"<!-- source: {section.source} -->\n<section>\n{''.join(pending)}\n{section.html}\n"
            "</section>\n",
            encoding="utf-8",
        )
        pending.clear()
        saved += 1
        watchdog.progress()
    return Staged(saved, lost, stalled=False)
