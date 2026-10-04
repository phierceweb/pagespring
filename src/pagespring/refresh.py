"""The refresh sweep: re-ingest slugs from their manifests' ``source_url`` with ``--if-changed``
semantics, isolating each failure so one dead source can't stop the sweep."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Literal, TypedDict

from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger, log_exception

from pagespring import http, manifest
from pagespring._integrity import deliverable_intact
from pagespring.config import cfg
from pagespring.orchestrate import AcquireError, EmptyOutputError, NoPatternError, run_ingest
from pagespring.paths import slug_dir
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
    incoming_dir = slug_dir(slug)
    m = manifest.read_manifest(incoming_dir)
    if m is None:
        return {"slug": slug, "status": "skipped", "detail": "no manifest — ingest it first"}
    if not _refreshable(m):
        return {"slug": slug, "status": "skipped", "detail": "unreadable manifest — re-ingest"}

    # Validators vouch for one response of source_url, never a crawl or the staged copy;
    # an acquire that fetched the PDF from another URL records none.
    pattern = pattern_by_name(m.get("pattern") or "")
    if m.get("kind") == "pdf" or getattr(pattern, "single_fetch", False):
        etag, last_modified = m.get("etag"), m.get("last_modified")
        intact = (etag or last_modified) and deliverable_intact(incoming_dir, m)
        if intact and http.not_modified(m["source_url"], etag=etag, last_modified=last_modified):
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


def refresh_all(*, patterns: Collection[str] | None = None) -> list[RefreshOutcome]:
    """Sweep every ``incoming/<slug>/`` in sorted order, or only the slugs whose
    manifest records one of ``patterns``.

    Raises:
        InvalidInputError: a name in ``patterns`` is not a registered pattern.
    """
    if patterns is not None:
        unknown = sorted(p for p in patterns if pattern_by_name(p) is None)
        if unknown:
            raise InvalidInputError(
                f"no registered pattern named {', '.join(unknown)} — `pagespring patterns` lists them"
            )
    incoming = Path(cfg.INCOMING_DIR)
    slugs = sorted(p.name for p in incoming.glob("*") if p.is_dir()) if incoming.is_dir() else []
    if patterns is not None:
        slugs = [s for s in slugs if _recorded_pattern(s) in patterns]
    return [_refresh_isolated(s) for s in slugs]


def refresh_slugs(slugs: Sequence[str]) -> list[RefreshOutcome]:
    """``refresh_slug`` for each named slug, once per folded slug, isolated like the sweep.

    Raises:
        InvalidInputError: a name folds to no slug, or names no manifest a refresh
            can replay. Every name is checked before the first fetch.
    """
    folded = list(dict.fromkeys(slug_dir(s).name for s in slugs))
    missing = [s for s in folded if not _refreshable(manifest.read_manifest(slug_dir(s)))]
    if missing:
        raise InvalidInputError(
            f"no readable manifest for {', '.join(missing)} — ingest it first; nothing was refreshed"
        )
    return [_refresh_isolated(s) for s in folded]


def _refreshable(m: object) -> bool:
    return isinstance(m, dict) and bool(m.get("source_url")) and bool(m.get("slug"))


def _recorded_pattern(slug: str) -> str | None:
    """The pattern the manifest ``refresh_slug`` would read records, if a string."""
    try:
        m = manifest.read_manifest(slug_dir(slug))
    except InvalidInputError:
        return None
    pattern = m.get("pattern") if m is not None else None
    return pattern if isinstance(pattern, str) else None


def _refresh_isolated(slug: str) -> RefreshOutcome:
    """``refresh_slug``, with an exception no handler names reported as ``failed``."""
    try:
        return refresh_slug(slug)
    except Exception as exc:
        log_exception(exc, message_prepend="refresh.unexpected", additional_context={"slug": slug})
        return {"slug": slug, "status": "failed", "detail": f"{type(exc).__name__}: {exc}"}
