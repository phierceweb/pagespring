"""pagespring command-line interface (Typer, via pf_core.cli).

Commands:
    ingest <url>     acquire + normalize ("fix") a manual into incoming/<slug>/
    localize <slug>  grab an already-ingested deliverable's images (resumable; --all)
    patterns         list the registered source patterns
    classify <url>   show which pattern handles a URL (no acquisition)
    status           list incoming/ deliverables (pattern, pages, size, date, source)
"""

from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import typer
from pf_core.cli import create_cli, run_cli
from pf_core.exceptions import InvalidInputError, PreconditionError

from pagespring import manifest
from pagespring.config import cfg
from pagespring.orchestrate import (
    AcquireError,
    EmptyOutputError,
    NoPatternError,
    localize_images,
    run_ingest,
)
from pagespring.registry import PATTERNS, classify

app = create_cli(
    "pagespring",
    help="Find, download, and normalize online software manuals into incoming/.",
)


@app.command()
def ingest(
    url: str = typer.Argument(
        ..., help="Manual URL (e.g. a support.apple.com/guide/<app>/ welcome page)."
    ),
    keep_raw: bool = typer.Option(
        False, "--keep-raw", help="Keep the raw crawl alongside the source in incoming/<slug>/raw."
    ),
    download_images: bool = typer.Option(
        False,
        "--download-images",
        help="Download an html/markdown source's images into incoming/<slug>/images/ and re-point refs. No-op for PDFs.",
    ),
    if_changed: bool = typer.Option(
        False,
        "--if-changed",
        help="Skip re-staging when the re-fetch normalizes to byte-identical content (the crawl still runs).",
    ),
) -> None:
    """Acquire a manual from URL and normalize it into incoming/<slug>/."""
    try:
        result = run_ingest(
            url, keep_raw=keep_raw, download_images=download_images, if_changed=if_changed
        )
    except NoPatternError:
        typer.echo(
            f"No pattern matched: {url}\n"
            "This source needs a new pattern. Run `bin/run patterns` to see the "
            "registered ones; src/pagespring/patterns/ shows the shape to author one.",
            err=True,
        )
        raise typer.Exit(2) from None
    except InvalidInputError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None
    except EmptyOutputError:
        typer.echo(
            f"Normalize produced an empty file for {url} — the source may have "
            "changed shape. Nothing was staged; a previous deliverable in "
            "incoming/ is untouched.",
            err=True,
        )
        raise typer.Exit(3) from None
    except AcquireError as exc:
        typer.echo(
            f"Fetch failed during acquire: {exc.detail}\n"
            f"Source: {exc.url}\n"
            "Nothing was staged. The fetch died mid-acquire — check the URL is "
            "reachable; re-run to retry.",
            err=True,
        )
        raise typer.Exit(4) from None

    typer.echo(f"pattern  : {result['pattern']}")
    typer.echo(f"slug     : {result['slug']}")
    typer.echo(f"incoming : {result['clean']}")
    if result.get("changed") is False:
        typer.echo(
            "status   : unchanged — source matches the existing deliverable, nothing re-staged"
        )
        return
    if result.get("pages") is not None:
        typer.echo(f"pages    : {result['pages']}")
    typer.echo(f"size     : {_human_size(result['bytes'])}")
    if result.get("images"):
        typer.echo(f"images   : {result['images']} downloaded → images/")


@app.command()
def localize(
    slug: str = typer.Argument(
        None, help="Book slug under incoming/ to localize images for (omit when using --all)."
    ),
    all_books: bool = typer.Option(
        False, "--all", help="Localize images for every incoming/<slug>/."
    ),
) -> None:
    """Download an already-ingested deliverable's remote images into images/ and
    re-point refs — no re-crawl. Resumable: re-run until none remain, so a book too
    big to localize in one pass finishes across runs."""
    if all_books:
        incoming = Path(cfg.INCOMING_DIR)
        targets = (
            sorted(p.name for p in incoming.glob("*") if p.is_dir()) if incoming.is_dir() else []
        )
    elif slug:
        targets = [slug]
    else:
        typer.echo("Give a slug or --all.", err=True)
        raise typer.Exit(2)

    for s in targets:
        try:
            r = localize_images(s)
        except PreconditionError as exc:
            typer.echo(f"skip {s}: {exc}", err=True)
            continue
        tail = "done" if r["remaining"] == 0 else f"{r['remaining']} remaining — re-run to continue"
        typer.echo(f"{s}: +{r['localized']} images (total {r['images_total']}) — {tail}")


@app.command()
def patterns() -> None:
    """List the registered source patterns and their convert recipes."""
    for p in PATTERNS:
        recipe = " ".join(p.convert_recipe) or "(none)"
        typer.echo(f"{p.name:12} convert-recipe: {recipe}")


@app.command("classify")
def classify_cmd(
    url: str = typer.Argument(..., help="URL to test against the pattern registry."),
) -> None:
    """Show which pattern (if any) handles a URL — no acquisition, no network."""
    p = classify(url)
    typer.echo(p.name if p else "(no pattern matched)")


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


@app.command()
def status() -> None:
    """One row per incoming/<slug>/, read from its manifest.json: deliverable,
    pattern, pages, size, ingest date, and source host. Legacy (pre-manifest)
    dirs fall back to the deliverable file's own facts. (Conversion into the
    manuals corpus is pagespeak's job — downstream of this tool, out of its view.)"""
    incoming = Path(cfg.INCOMING_DIR)
    slugs = sorted(p for p in incoming.glob("*") if p.is_dir()) if incoming.is_dir() else []
    if not slugs:
        typer.echo("(nothing in incoming/ — run `bin/run ingest <url>`)")
        return
    for d in slugs:
        typer.echo(_status_row(d))


def _status_row(slug_dir: Path) -> str:
    """One status line from the slug's manifest; for legacy (pre-manifest) dirs,
    fall back to the first non-manifest deliverable file's own facts."""
    m = manifest.read_manifest(slug_dir)
    if m is not None:
        deliverable = slug_dir / m["deliverable"]
        size = deliverable.stat().st_size if deliverable.exists() else m["bytes"]
        pages = str(m["pages"]) if m["pages"] is not None else "-"
        host = urlsplit(m["source_url"]).netloc or "-"
        return (
            f"{slug_dir.name:24} {m['deliverable']:32} {m['pattern']:14} "
            f"{pages:>5} {_human_size(size):>9}  {m['ingested_at'][:10]}  {host}"
        )
    files = sorted(
        p for p in slug_dir.iterdir() if p.is_file() and p.name != manifest.MANIFEST_NAME
    )
    if not files:
        return f"{slug_dir.name:24} {'(no clean file)':32} {'-':14} {'-':>5} {'-':>9}  -  -"
    f = files[0]
    when = date.fromtimestamp(f.stat().st_mtime).isoformat()
    return (
        f"{slug_dir.name:24} {f.name:32} {'-':14} "
        f"{'-':>5} {_human_size(f.stat().st_size):>9}  {when}  -"
    )


def main() -> None:
    run_cli(app)


if __name__ == "__main__":
    main()
