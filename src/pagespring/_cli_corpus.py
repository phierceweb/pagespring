"""CLI commands over the whole of incoming/: audit, refresh, status.

Registered on the app in ``cli.py``.
"""

from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import typer
from pf_core.exceptions import InvalidInputError

from pagespring import manifest
from pagespring._cli_ingest import human_size
from pagespring.audit import audit_all, audit_slug
from pagespring.config import cfg
from pagespring.refresh import RefreshOutcome, refresh_all, refresh_slugs


def audit_cmd(
    slug: str = typer.Argument(None, help="Slug under incoming/ to audit (omit when using --all)."),
    all_slugs: bool = typer.Option(False, "--all", help="Audit every incoming/<slug>/."),
    strict: bool = typer.Option(
        False, "--strict", help="Exit 1 when any error-level finding exists (gate a hand-off)."
    ),
) -> None:
    """Deterministic $0 checks over staged deliverables — no network, no LLM.
    Report-only by default (exit 0); --strict turns error-level findings into
    exit 1 so a script can gate a hand-off."""
    if all_slugs:
        results = audit_all()
        if not results:
            # Auditing nothing is not a pass. Exit 2 ("could not do what you
            # asked") keeps 1 for "found errors".
            typer.echo(
                f"Nothing to audit — no slugs under {cfg.INCOMING_DIR}. "
                f"Run `ingest` first, or check the path.",
                err=True,
            )
            raise typer.Exit(2)
    elif slug:
        try:
            results = [(slug, audit_slug(slug))]
        except InvalidInputError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from None
    else:
        typer.echo("Give a slug or --all.", err=True)
        raise typer.Exit(2)

    errors = warnings = 0
    for s, findings in results:
        if not findings:
            typer.echo(f"{s}: ok")
            continue
        for f in findings:
            typer.echo(f"{s}: {f['check']} ({f['level']}) — {f['detail']}")
            errors += f["level"] == "error"
            warnings += f["level"] == "warning"
    typer.echo(_audit_summary(len(results), errors, warnings))

    if strict and errors:
        raise typer.Exit(1)


def _audit_summary(n_slugs: int, errors: int, warnings: int) -> str:
    if not errors and not warnings:
        return f"{n_slugs} audited, all ok"
    parts = [f"{errors} error{'s' if errors != 1 else ''}"] if errors else []
    if warnings:
        parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
    return f"{n_slugs} audited: " + ", ".join(parts)


def refresh(
    slugs: list[str] = typer.Argument(
        None, help="Slugs under incoming/ to re-check (omit when using --all or --pattern)."
    ),
    all_slugs: bool = typer.Option(False, "--all", help="Sweep every incoming/<slug>/."),
    patterns: list[str] = typer.Option(
        None,
        "--pattern",
        help="Sweep only the slugs a pattern acquired (repeatable; `patterns` lists them).",
    ),
) -> None:
    """Re-check ingested manuals against their live sources and re-stage what
    changed. One line per slug (changed / unchanged / failed / skipped);
    exit 1 if any source failed, so a wrapper can tell a clean sweep from a
    degraded one."""
    if slugs and (all_slugs or patterns):
        typer.echo("Give slugs, or --all / --pattern — not both.", err=True)
        raise typer.Exit(2)
    try:
        if patterns:
            outcomes = refresh_all(patterns=set(patterns))
        elif all_slugs:
            outcomes = refresh_all()
        elif slugs:
            outcomes = refresh_slugs(slugs)
        else:
            typer.echo("Give a slug, --all, or --pattern.", err=True)
            raise typer.Exit(2)
    except InvalidInputError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None
    if not outcomes:
        # Sweeping nothing is not a clean sweep. Exit 2 ("could not do what you
        # asked") keeps 1 for "a source failed".
        scope = f" acquired by {', '.join(sorted(patterns))}" if patterns else ""
        typer.echo(
            f"Nothing to refresh — no slugs{scope} under {cfg.INCOMING_DIR}. "
            f"Run `ingest` first, or check the path.",
            err=True,
        )
        raise typer.Exit(2)

    for o in outcomes:
        tail = f" — {o['detail']}" if o["detail"] else ""
        typer.echo(f"{o['slug']}: {o['status']}{tail}")
    typer.echo(_refresh_summary(outcomes))

    if any(o["status"] == "failed" for o in outcomes):
        raise typer.Exit(1)
    if slugs and any(o["status"] == "skipped" for o in outcomes):
        raise typer.Exit(2)  # a slug you named can't be refreshed


def _refresh_summary(outcomes: list[RefreshOutcome]) -> str:
    counts = {
        s: sum(1 for o in outcomes if o["status"] == s)
        for s in ("changed", "unchanged", "failed", "skipped")
    }
    return ", ".join(f"{n} {s}" for s, n in counts.items() if n)


def status() -> None:
    """One row per incoming/<slug>/, read from its manifest.json: deliverable,
    pattern, pages, size, kept raw/, ingest date, and source host. A dir without a
    readable manifest falls back to its file's own facts."""
    incoming = Path(cfg.INCOMING_DIR)
    slugs = sorted(p for p in incoming.glob("*") if p.is_dir()) if incoming.is_dir() else []
    if not slugs:
        typer.echo("(nothing in incoming/ — run `bin/run ingest <url>`)")
        return
    rows = [_status_cells(d) for d in slugs]
    widths = [max(len(row[i]) for row in rows) for i in range(len(_RIGHT_ALIGNED))]
    for row in rows:
        cells = (
            cell.rjust(w) if right else cell.ljust(w)
            for cell, w, right in zip(row, widths, _RIGHT_ALIGNED, strict=True)
        )
        typer.echo("  ".join(cells).rstrip())


_ROW_FIELDS = ("deliverable", "pattern", "pages", "bytes", "source_url", "ingested_at")
# slug, deliverable, pattern, pages, size, raw, date, host
_RIGHT_ALIGNED = (False, False, False, True, True, False, False, False)


def _status_cells(slug_dir: Path) -> tuple[str, str, str, str, str, str, str, str]:
    """One status row's cells from the slug's manifest, or, when it is missing or
    unreadable, from the first non-manifest file's own facts."""
    m = manifest.read_manifest(slug_dir)
    if isinstance(m, dict) and all(k in m for k in _ROW_FIELDS):
        deliverable = slug_dir / str(m["deliverable"])
        size = deliverable.stat().st_size if deliverable.exists() else m["bytes"]
        pages = str(m["pages"]) if m["pages"] is not None else "-"
        host = urlsplit(m["source_url"]).netloc or "-"
        raw = "raw" if m.get("kept_raw") else ""  # replays offline via renormalize
        return (
            slug_dir.name,
            str(m["deliverable"]),
            str(m["pattern"]),
            pages,
            human_size(size),
            raw,
            str(m["ingested_at"])[:10],
            host,
        )
    files = sorted(
        p for p in slug_dir.iterdir() if p.is_file() and p.name != manifest.MANIFEST_NAME
    )
    if not files:
        return (slug_dir.name, "(no clean file)", "-", "-", "-", "", "-", "-")
    f = files[0]
    when = date.fromtimestamp(f.stat().st_mtime).isoformat()
    return (slug_dir.name, f.name, "-", "-", human_size(f.stat().st_size), "", when, "-")
