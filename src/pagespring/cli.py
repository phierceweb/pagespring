"""pagespring command-line interface (Typer, via pf_core.cli).

The commands live in ``_cli_ingest`` and ``_cli_corpus``; registering them here fixes
their order in ``--help``.
"""

import signal
import urllib.error
from types import FrameType

import typer
from pf_core.cli import create_cli, run_cli
from pf_core.exceptions import ClientError, InvalidInputError

from pagespring._cli_corpus import audit_cmd, refresh, status
from pagespring._cli_ingest import ingest, localize, renormalize
from pagespring.patterns._detect import detect
from pagespring.registry import PATTERNS, classify

app = create_cli(
    "pagespring",
    help="Find, download, and normalize online software manuals into incoming/.",
)
app.command()(ingest)
app.command()(renormalize)
app.command()(localize)
app.command("audit")(audit_cmd)
app.command()(refresh)


@app.command()
def patterns() -> None:
    """List the registered source patterns, in match order."""
    for p in PATTERNS:
        typer.echo(p.name)


@app.command("classify")
def classify_cmd(
    url: str = typer.Argument(..., help="URL to test against the pattern registry."),
    probe: bool = typer.Option(
        False,
        "--probe",
        help="When docs_probe claims the URL, fetch its entry page (and the search-index "
        "and llms.txt probes it needs) and report the route ingest would take. No crawl.",
    ),
) -> None:
    """Show which pattern (if any) handles a URL — no acquisition, and no network
    unless --probe."""
    p = classify(url)
    typer.echo(p.name if p else "(no pattern matched)")
    if not probe or p is None or p.name != "docs_probe":
        return
    try:
        found = detect(url)
    except InvalidInputError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from None
    except (urllib.error.URLError, TimeoutError, ConnectionError, ClientError) as exc:
        typer.echo(f"Fetch failed during the probe: {exc}", err=True)
        raise typer.Exit(4) from None
    # Too large to read as a page, so only ingest's download can confirm the PDF.
    unverified = (
        " — too large to read as a page; ingest checks it is a PDF" if found.oversize else ""
    )
    typer.echo(f"route    : {found.route} (via {found.via}{unverified})")
    if found.generator:
        typer.echo(f"generator: {found.generator}")


app.command()(status)


def _exit_on(signum: int, _frame: FrameType | None) -> None:
    raise SystemExit(128 + signum)


def _unwind_on_termination() -> None:
    """Turn SIGTERM and SIGHUP into ``SystemExit`` so ``finally`` blocks record what a
    pass left; by default they end the process with no unwinding. A signal the caller
    ignores (``nohup``) stays ignored."""
    for name in ("SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is not None and signal.getsignal(sig) is signal.SIG_DFL:
            signal.signal(sig, _exit_on)


def main() -> None:
    _unwind_on_termination()
    run_cli(app)


if __name__ == "__main__":
    main()
