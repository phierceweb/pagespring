"""CLI commands that stage or re-stage a deliverable: ingest, renormalize, localize.

Registered on the app in ``cli.py``.
"""

from pathlib import Path

import typer
from pf_core.exceptions import InvalidInputError, PreconditionError

from pagespring.batch import BatchOutcome, ingest_batch, read_batch
from pagespring.config import cfg
from pagespring.localize import localize_images
from pagespring.orchestrate import AcquireError, EmptyOutputError, NoPatternError, run_ingest
from pagespring.renormalize import run_renormalize


def ingest(
    url: str = typer.Argument(
        None, help="Manual URL (e.g. a support.apple.com/guide/<app>/ welcome page)."
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
    slug: str = typer.Option(
        None,
        "--slug",
        help="Override the derived slug (folded to kebab-case) — names the incoming/ dir and deliverable.",
    ),
    replace: bool = typer.Option(
        False,
        "--replace",
        help="Take over a slug holding a DIFFERENT source (deleting it), or accept a "
        "same-source re-crawl far smaller than the manual staged there.",
    ),
    batch: str = typer.Option(
        None,
        "--batch",
        help="Ingest each URL in this file (one per line; blank and # lines skipped) with "
        "the other options. Exit 1 if any line failed.",
    ),
) -> None:
    """Acquire a manual from URL (or each URL in a --batch file) and normalize it into
    incoming/<slug>/."""
    if batch is not None:
        if url is not None or slug is not None:
            typer.echo("--batch takes no URL argument or --slug.", err=True)
            raise typer.Exit(2)
        _ingest_batch(
            Path(batch),
            keep_raw=keep_raw,
            download_images=download_images,
            if_changed=if_changed,
            replace=replace,
        )
        return
    if not url:
        typer.echo("Give a URL or --batch <file>.", err=True)
        raise typer.Exit(2)
    try:
        result = run_ingest(
            url,
            keep_raw=keep_raw,
            download_images=download_images,
            if_changed=if_changed,
            slug_override=slug,
            replace=replace,
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
    if result.get("duplicate_of"):
        typer.echo(
            f"warning  : content identical to incoming/{result['duplicate_of']}/ — "
            "same manual under two slugs?"
        )
    if result.get("changed") is False:
        typer.echo(
            "status   : unchanged — source matches the existing deliverable, nothing re-staged"
        )
        return
    if result.get("pages") is not None:
        typer.echo(f"pages    : {result['pages']}")
    typer.echo(f"size     : {human_size(result['bytes'])}")
    if result.get("images"):
        downloaded = result.get("images_downloaded", 0)
        typer.echo(f"images   : {result['images']} in images/ ({downloaded} downloaded)")


def _ingest_batch(
    path: Path, *, keep_raw: bool, download_images: bool, if_changed: bool, replace: bool
) -> None:
    try:
        lines = read_batch(path)
    except InvalidInputError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None
    outcomes = []
    for o in ingest_batch(
        lines,
        keep_raw=keep_raw,
        download_images=download_images,
        if_changed=if_changed,
        replace=replace,
    ):
        outcomes.append(o)
        typer.echo(_batch_line(o))
    counts = {
        s: sum(1 for o in outcomes if o["status"] == s)
        for s in ("staged", "unchanged", "failed", "skipped")
    }
    typer.echo(f"{len(outcomes)} URLs: " + ", ".join(f"{n} {s}" for s, n in counts.items() if n))
    failed = [str(o["line"]) for o in outcomes if o["status"] == "failed"]
    if failed:
        typer.echo(f"failed lines: {', '.join(failed)}")
        raise typer.Exit(1)


def _batch_line(o: BatchOutcome) -> str:
    res = o["result"]
    if res is None:
        return f"line {o['line']}: {o['url']}: {o['status']} — {o['detail']}"
    line = f"line {o['line']}: {o['url']} → {res['slug']}: {o['status']}"
    if o["status"] == "staged":
        n = res["pages"]
        pages = f"{n} page{'s' if n != 1 else ''}, " if n is not None else ""
        line += f" ({pages}{human_size(res['bytes'])})"
    if res["duplicate_of"]:
        line += f" — content identical to incoming/{res['duplicate_of']}/"
    return line


def renormalize(
    slug: str = typer.Argument(
        ..., help="Ingested slug (with kept raw/) to re-normalize — no re-crawl, no network."
    ),
) -> None:
    """Re-run the pattern's current normalize against incoming/<slug>/raw/ and
    re-stage the deliverable — no acquire. Requires an ingest made with
    --keep-raw. Byte-identical output re-stages nothing and reports unchanged."""
    try:
        result = run_renormalize(slug)
    except (InvalidInputError, PreconditionError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None
    except EmptyOutputError:
        typer.echo(
            f"Normalize produced an empty file for {slug} — nothing re-staged; "
            "the existing deliverable is untouched.",
            err=True,
        )
        raise typer.Exit(3) from None

    typer.echo(f"pattern  : {result['pattern']}")
    typer.echo(f"slug     : {result['slug']}")
    typer.echo(f"incoming : {result['clean']}")
    if result["changed"] is False:
        typer.echo("status   : unchanged — normalize output matches the staged deliverable")
        return
    if result["pages"] is not None:
        typer.echo(f"pages    : {result['pages']}")
    typer.echo(f"size     : {human_size(result['bytes'])}")


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
        except InvalidInputError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(2) from None
        except PreconditionError as exc:
            typer.echo(f"skip {s}: {exc}", err=True)
            if not all_books:
                raise typer.Exit(2) from None
            continue
        tail = "done" if r["remaining"] == 0 else f"{r['remaining']} remaining — re-run to continue"
        reused = f", {r['reused']} reused" if r["reused"] else ""
        pruned = f", {r['pruned']} pruned" if r["pruned"] else ""
        typer.echo(
            f"{s}: +{r['localized']} images{reused}{pruned} (total {r['images_total']}) — {tail}"
        )


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
