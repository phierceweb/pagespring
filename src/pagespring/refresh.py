"""The refresh sweep: re-check every ``incoming/<slug>/`` against its live
source and re-stage what changed.

``refresh_slug`` re-ingests one slug from its manifest's ``source_url`` with
``--if-changed`` semantics (byte-identical → untouched); ``refresh_all`` sweeps
every slug, isolating per-slug failures so one dead source can't stop the
sweep. The per-slug outcome (changed/unchanged/failed/skipped) is the
hand-off signal for downstream re-conversion (pagespeak) and re-indexing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, TypedDict

from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger

from pagespring import http, manifest
from pagespring.config import cfg
from pagespring.orchestrate import AcquireError, EmptyOutputError, NoPatternError, run_ingest
from pagespring.registry import pattern_by_name

log = get_logger(__name__)

Status = Literal["changed", "unchanged", "failed", "skipped"]


class RefreshOutcome(TypedDict):
    """One slug's sweep result (the CLI prints one line per outcome)."""

    slug: str
    status: Status
    detail: str  # reason/extra: error text or probe note, "" when none


def refresh_slug(slug: str) -> RefreshOutcome:
    """Re-ingest ``slug`` from its manifest's source_url; report what happened."""
    incoming_dir = Path(cfg.INCOMING_DIR) / slug
    m = manifest.read_manifest(incoming_dir)
    if m is None:
        return {"slug": slug, "status": "skipped", "detail": "no manifest — ingest it first"}

    # Fast path: only single-fetch patterns may trust stored validators — a
    # crawl's entry-page 304 proves nothing about the rest of the site.
    pattern = pattern_by_name(m["pattern"])
    if pattern is not None and getattr(pattern, "single_fetch", False):
        etag, last_modified = m.get("etag"), m.get("last_modified")
        if (etag or last_modified) and http.not_modified(
            m["source_url"], etag=etag, last_modified=last_modified
        ):
            log.info("refresh.not_modified", slug=slug)
            return {"slug": slug, "status": "unchanged", "detail": "not modified (validator probe)"}

    # Keep the kept-raw property across the replace, and pin the recorded slug
    # — a retitled source (or --slug override) must not mint a duplicate dir.
    keep_raw = (incoming_dir / "raw").is_dir()
    try:
        res = run_ingest(
            m["source_url"], if_changed=True, keep_raw=keep_raw, slug_override=m["slug"]
        )
    except AcquireError as exc:
        log.warning("refresh.failed", slug=slug, error=exc.detail)
        return {"slug": slug, "status": "failed", "detail": exc.detail}
    except EmptyOutputError:
        detail = "normalize produced empty output; previous deliverable kept"
        log.warning("refresh.failed", slug=slug, error=detail)
        return {"slug": slug, "status": "failed", "detail": detail}
    except (NoPatternError, InvalidInputError) as exc:
        log.warning("refresh.failed", slug=slug, error=str(exc))
        return {"slug": slug, "status": "failed", "detail": str(exc)}

    if res["changed"]:
        return {"slug": slug, "status": "changed", "detail": ""}
    return {"slug": slug, "status": "unchanged", "detail": ""}


def refresh_all() -> list[RefreshOutcome]:
    """Sweep every ``incoming/<slug>/`` in sorted order."""
    incoming = Path(cfg.INCOMING_DIR)
    slugs = sorted(p.name for p in incoming.glob("*") if p.is_dir()) if incoming.is_dir() else []
    return [refresh_slug(s) for s in slugs]
