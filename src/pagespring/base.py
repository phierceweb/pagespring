"""The Pattern contract: ``match`` a family of source URLs, ``acquire`` the raw pages, and
``normalize`` them into the one clean deliverable file."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

# The deliverable's format; a "pdf" is the downloaded file, cut into single pages if printed 2-up.
SourceKind = Literal["html", "markdown", "pdf"]

# Files no URL serves (an archive's own figures) go here beside the normalized file, as
# ``images/<name>``; staging copies them into ``incoming/<slug>/images/``.
IMAGES_DIR = "images"


@dataclass
class AcquireResult:
    """What ``acquire`` produced: a local dir of raw pages + how to treat them."""

    raw_dir: Path  # local dir holding the downloaded raw page(s)
    kind: SourceKind
    slug: str  # short id for the source; becomes the output dir name
    # Source units the deliverable covers — crawl pages / articles / PDF pages.
    # None means "not determinable" (e.g. an unreadable PDF), never a guess.
    pages: int | None = None
    title: str | None = None  # human source title for the deliverable heading (falls back to slug)
    # The crawl stopped short (page cap, stall, unreadable sitemap). Travels to the manifest so
    # audit can fail it: a truncated crawl looks healthy on every content check.
    truncated: bool = False
    # The source IS one document, not a crawled index — tells audit that a
    # 1-page deliverable is correct. Suppresses a check, so set it deliberately.
    single_document: bool = False
    # Pages discovered but never staged (fetch error, no content). A crawl that stops
    # short is loud via `truncated`; losing pages one at a time is not.
    lost: int = 0
    # 2-up spread pages normalize cut into their left and right pages (a PDF printed as spreads).
    spreads_split: int = 0
    # Cache validators from single-fetch acquires — refresh probes with them.
    etag: str | None = None
    last_modified: str | None = None


@runtime_checkable
class Pattern(Protocol):
    """One source type's acquire/normalize knowledge; the registry holds one instance of each, and
    every source-specific rule (crawl scope, chrome, TOC walking, image schemes) lives here."""

    name: str

    def match(self, url: str) -> bool:
        """Cheap check (host/path) for whether this pattern handles ``url``."""
        ...

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        """Download the source's raw pages into ``workdir``."""
        ...

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        """Turn the raw pages into ONE clean .html/.md (absolute asset URLs, or
        ``IMAGES_DIR`` refs to files written beside it)."""
        ...
