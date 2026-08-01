"""The Pattern contract — the unit that ties acquire + normalize together for
one source type.

A pattern recognizes a family of source URLs (``match``), downloads the raw
pages (``acquire``), and turns them into one clean file with absolute asset
URLs (``normalize``). That file is the deliverable and pagespring stops there:
how it gets converted is pagespeak's decision, derived from the source it can
see for itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

# "html"     -> hand the clean file to pagespeak to convert (markitdown + pipeline)
# "markdown" -> already the source's canonical clean form (e.g. GitBook per-page .md)
# "pdf"      -> a downloaded PDF; already a pagespeak input (normalize passthrough)
SourceKind = Literal["html", "markdown", "pdf"]


@dataclass
class AcquireResult:
    """What ``acquire`` produced: a local dir of raw pages + how to treat them."""

    raw_dir: Path  # local dir holding the downloaded raw page(s)
    kind: SourceKind  # whether normalize emits html (for pagespeak) or markdown
    slug: str  # short id for the source; becomes the output dir name
    # Source units the deliverable covers — crawl pages / articles / PDF pages.
    # None means "not determinable" (e.g. an unreadable PDF), never a guess.
    pages: int | None = None
    title: str | None = None  # human source title for the deliverable heading (falls back to slug)
    # A page cap cut this crawl short. Travels to the manifest so audit can fail it:
    # a truncated crawl looks healthy on every content check when the source grew.
    truncated: bool = False
    # Cache validators from single-fetch acquires — refresh probes with them.
    etag: str | None = None
    last_modified: str | None = None


@runtime_checkable
class Pattern(Protocol):
    """One source type's acquire/normalize knowledge.

    Implementations are instances (see pagespring/patterns/*); the registry holds
    one of each. All *source-specific* knowledge (crawl rules, chrome selectors,
    TOC walking, image-scheme resolution) lives in the pattern — pagespeak stays
    source-agnostic.
    """

    name: str

    def match(self, url: str) -> bool:
        """Cheap check (host/path) for whether this pattern handles ``url``."""
        ...

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        """Download the source's raw pages into ``workdir``."""
        ...

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        """Turn the raw pages into ONE clean .html/.md (absolute asset URLs)."""
        ...
