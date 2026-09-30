"""``ingest --batch``: one ingest per URL line of a file, each line's failure isolated
so one dead source can't stop the batch."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Literal, NamedTuple, TypedDict

from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger, log_exception

from pagespring import http
from pagespring._staging import source_key
from pagespring.orchestrate import (
    AcquireError,
    EmptyOutputError,
    IngestResult,
    NoPatternError,
    run_ingest,
)

log = get_logger(__name__)

Status = Literal["staged", "unchanged", "failed", "skipped"]


class BatchLine(NamedTuple):
    number: int  # 1-based, in the batch file
    url: str


class BatchOutcome(TypedDict):
    """One line's result (the CLI prints one line per outcome)."""

    line: int
    url: str
    status: Status
    detail: str  # error text, or the line a repeat points at; "" when none
    result: IngestResult | None  # None unless the line was ingested


def read_batch(path: Path) -> list[BatchLine]:
    """The URL lines of ``path``: stripped, skipping blank lines and ``#`` comments.

    Raises:
        InvalidInputError: the file can't be read, or holds no URL line.
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        raise InvalidInputError(f"can't read batch file {path}: {exc}") from None
    lines = [
        BatchLine(n, s)
        for n, raw in enumerate(text.splitlines(), start=1)
        if (s := raw.strip()) and not s.startswith("#")
    ]
    if not lines:
        raise InvalidInputError(f"no URLs in batch file {path} — one per line; # starts a comment")
    return lines


def ingest_batch(
    lines: Sequence[tuple[int, str]],
    *,
    keep_raw: bool = False,
    download_images: bool = False,
    if_changed: bool = False,
    replace: bool = False,
) -> Iterator[BatchOutcome]:
    """``run_ingest`` each line in order with the same options, yielding its outcome
    as it finishes. A source already in the batch, however spelled, is skipped, not
    crawled again, and no line takes over a slug an earlier line staged."""
    first_seen: dict[str, int] = {}
    staged: set[str] = set()
    for i, (number, url) in enumerate(lines):
        key = source_key(url)
        if key in first_seen:
            yield _outcome(number, url, "skipped", f"repeats line {first_seen[key]}")
            continue
        first_seen[key] = number
        if i:
            http.polite_sleep()
        outcome = _ingest_isolated(
            number,
            url,
            keep_raw=keep_raw,
            download_images=download_images,
            if_changed=if_changed,
            replace=replace,
            protected_slugs=frozenset(staged),
        )
        if outcome["result"] is not None:
            staged.add(outcome["result"]["slug"])
        yield outcome


def _ingest_isolated(
    number: int,
    url: str,
    *,
    keep_raw: bool,
    download_images: bool,
    if_changed: bool,
    replace: bool,
    protected_slugs: frozenset[str],
) -> BatchOutcome:
    try:
        res = run_ingest(
            url,
            keep_raw=keep_raw,
            download_images=download_images,
            if_changed=if_changed,
            slug_override=None,
            replace=replace,
            protected_slugs=protected_slugs,
        )
    except AcquireError as exc:
        return _failed(number, url, exc.detail)
    except NoPatternError:
        return _failed(number, url, "no pattern matched — `patterns` lists the registered ones")
    except EmptyOutputError:
        return _failed(number, url, "normalize produced an empty file; nothing staged")
    except InvalidInputError as exc:
        return _failed(number, url, str(exc))
    except Exception as exc:
        log_exception(exc, message_prepend="batch.unexpected", additional_context={"url": url})
        return _failed(number, url, f"{type(exc).__name__}: {exc}")
    status: Status = "staged" if res["changed"] else "unchanged"
    return _outcome(number, url, status, "", res)


def _failed(number: int, url: str, detail: str) -> BatchOutcome:
    log.warning("batch.failed", line=number, url=url, error=detail)
    return _outcome(number, url, "failed", detail)


def _outcome(
    number: int, url: str, status: Status, detail: str, result: IngestResult | None = None
) -> BatchOutcome:
    return {"line": number, "url": url, "status": status, "detail": detail, "result": result}
