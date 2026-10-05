"""audit: $0 read-only checks of what each manifest claims against what is on disk. Error-level
findings mean the deliverable can't be trusted; warnings are real but survivable."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, TypedDict

from pf_core.log import get_logger

from pagespring import manifest
from pagespring._integrity import LOCAL_IMG_RE, image_pass_ran, integrity, usable
from pagespring._staging import source_key
from pagespring.config import cfg
from pagespring.paths import slug_dir
from pagespring.registry import pattern_by_name

log = get_logger(__name__)

Level = Literal["error", "warning"]

# Not the localizer's matcher: a ref it declines to claim is never downloaded or counted, so its
# count would report clean on a deliverable still remote.
_REMOTE_IMG_RE = re.compile(
    r'(?:<img\b[^>]*?\bsrc=["\']|!\[[^\]]*\]\()(https?://[^"\')\s]+)', re.IGNORECASE
)
_MD_HEADING_RE = re.compile(r"^#{1,6} ", re.MULTILINE)
_HTML_HEADING_RE = re.compile(r"<h[1-6][\s>]", re.IGNORECASE)


class Finding(TypedDict):
    """One defect: which check fired, how bad, and what it saw."""

    check: str
    level: Level
    detail: str


def _f(check: str, level: Level, detail: str) -> Finding:
    return {"check": check, "level": level, "detail": detail}


def _sha_findings(incoming_dir: Path, m: manifest.Manifest, doc_text: str) -> list[Finding]:
    mismatch = _f("sha_mismatch", "error", "on-disk content differs from the recorded sha256")
    if m.get("image_pass_open"):
        interrupted = _f(
            "localize_interrupted",
            "warning",
            "an image pass was cut short before recording its outcome — re-run localize",
        )
        return [interrupted] + ([mismatch] if integrity(incoming_dir, m) == "damaged" else [])
    # Localize re-points refs, so `localized_sha256` — not `sha256` — describes a
    # localized file.
    localized = image_pass_ran(incoming_dir, m, doc_text)
    expected = m.get("localized_sha256") or (None if localized else m["sha256"])
    actual = manifest.sha256_file(incoming_dir / m["deliverable"])
    if expected is not None:
        return [mismatch] if actual != expected else []
    if actual != m["sha256"]:
        # Warning, not error: nothing here can tell, and "ok" reads as verified.
        return [
            _f(
                "sha_unverified",
                "warning",
                "localized deliverable carries no localized_sha256 — integrity "
                "unverifiable; re-ingest with --download-images to record one",
            )
        ]
    return []


def audit_slug(slug: str) -> list[Finding]:
    """Audit one ``incoming/<slug>/``; empty list ⇒ healthy."""
    incoming_dir = slug_dir(slug)
    m = manifest.read_manifest(incoming_dir)
    if m is None:
        return [_f("manifest_missing", "error", f"no manifest.json in {incoming_dir}/")]
    if not usable(m):
        return [
            _f(
                "manifest_missing",
                "error",
                f"manifest.json in {incoming_dir}/ is missing required fields — re-ingest",
            )
        ]

    deliverable = incoming_dir / m["deliverable"]
    if not deliverable.exists():
        return [_f("deliverable_missing", "error", f"{m['deliverable']} is gone — re-ingest")]
    if deliverable.stat().st_size == 0:
        return [_f("deliverable_empty", "error", f"{m['deliverable']} is 0 bytes — re-ingest")]

    doc_text = (
        deliverable.read_text(encoding="utf-8", errors="replace")
        if m["kind"] in ("markdown", "html")
        else ""
    )
    findings = _sha_findings(incoming_dir, m, doc_text)

    # The crawl stopped short, and nothing in the content shows it: when the source grew, the
    # truncated copy still outweighs the last one.
    if m.get("truncated"):
        findings.append(
            _f(
                "crawl_truncated",
                "error",
                f"the crawl stopped short at {m['pages']} pages (a page cap, a stall, or an "
                "unreadable sitemap) — the deliverable is partial",
            )
        )

    # Pages discovered but never staged: `truncated` marks a crawl that stopped short, so one bled
    # dry by throttling passes every content check.
    lost = m.get("lost") or 0
    if lost:
        staged = m["pages"] or 0
        pct = 100 * lost / max(staged + lost, 1)
        # Rounding must not flatten the share to a lie at either end: "0%" for a
        # real loss, or "100%" while pages were staged.
        share = f"{pct:.0f}%"
        if pct < 1:
            share = "<1%"
        elif share == "100%" and staged:
            share = ">99%"
        findings.append(
            _f(
                "pages_lost",
                "error",
                f"{lost} of {staged + lost} discovered page(s) never staged ({share}) — "
                "the source threw errors mid-crawl; re-ingest",
            )
        )

    # One page from a crawl pattern: the seed named a page, not the index. Skipped for an unknown
    # pattern, a PDF, or a source marked as one document.
    pattern = pattern_by_name(m["pattern"])
    if (
        m["kind"] != "pdf"
        and pattern is not None
        and not getattr(pattern, "single_fetch", False)
        and not m.get("single_document")
        and m["pages"] == 1
    ):
        findings.append(
            _f(
                "single_page_crawl",
                "error",
                f"{m['pattern']} yielded 1 page — seed URL likely names a page, not the index",
            )
        )

    if m["kind"] in ("markdown", "html"):
        # images/ existing is the fact that a localize pass ran; the count can be
        # 0 when every download failed.
        if m["images"] > 0 or (incoming_dir / "images").is_dir():
            remaining = len(set(_REMOTE_IMG_RE.findall(doc_text)))
            if remaining:
                findings.append(
                    _f(
                        "localize_incomplete",
                        "warning",
                        f"{remaining} remote image ref(s) remain — re-run localize",
                    )
                )
        # A local ref whose file is gone renders broken; the remote-ref check above can't see it.
        dangling = sorted(
            ref for ref in set(LOCAL_IMG_RE.findall(doc_text)) if not (incoming_dir / ref).exists()
        )
        if dangling:
            findings.append(
                _f(
                    "broken_image_ref",
                    "error",
                    f"{len(dangling)} local image ref(s) point at missing files "
                    f"(e.g. {dangling[0]}) — re-ingest and re-localize",
                )
            )

        pages = m["pages"]
        if pages is not None and pages > 1:
            heading_re = _MD_HEADING_RE if m["kind"] == "markdown" else _HTML_HEADING_RE
            if not heading_re.search(doc_text):
                findings.append(
                    _f(
                        "no_headings",
                        "warning",
                        f"{pages} pages normalized to zero headings — splits into nothing",
                    )
                )

    return findings


def _corpus_findings(slugs: list[str]) -> dict[str, list[Finding]]:
    """Checks on the relation between slugs, keyed by the slug they attach to; derived fresh from
    the manifests, since a duplicate can be ingested after the slug it collides with."""
    incoming = Path(cfg.INCOMING_DIR)
    by_sha: dict[str, list[str]] = {}
    by_url: dict[str, list[str]] = {}
    for slug in slugs:
        m = manifest.read_manifest(incoming / slug)
        if usable(m):
            by_sha.setdefault(m["sha256"], []).append(slug)
            by_url.setdefault(source_key(m["source_url"]), []).append(slug)

    out: dict[str, list[Finding]] = {}
    # Same source_url under two slugs is a staging error; same bytes from
    # different URLs is only suspicious.
    checks: tuple[tuple[dict[str, list[str]], str, Level, str], ...] = (
        (by_sha, "duplicate_content", "warning", "byte-identical content"),
        (by_url, "duplicate_source_url", "error", "the same source_url"),
    )
    for group, check, level, what in checks:
        for members in group.values():
            if len(members) < 2:
                continue
            for slug in members:
                others = ", ".join(s for s in members if s != slug)
                out.setdefault(slug, []).append(_f(check, level, f"{what} as: {others}"))
    return out


def audit_all() -> list[tuple[str, list[Finding]]]:
    """Audit every ``incoming/<slug>/`` in sorted order, plus corpus-wide checks."""
    incoming = Path(cfg.INCOMING_DIR)
    slugs = sorted(p.name for p in incoming.glob("*") if p.is_dir()) if incoming.is_dir() else []
    corpus = _corpus_findings(slugs)
    return [(s, audit_slug(s) + corpus.get(s, [])) for s in slugs]
