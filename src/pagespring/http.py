"""Tiny shared HTTP fetch over the standard library (urllib).

Deliberately no httpx dependency — the acquire step only does plain GETs.
Provides an identifying User-Agent (override via PAGESPRING_UA), a timeout,
status-aware retries (permanent 4xx fail fast; 429 honors Retry-After;
5xx/network errors back off), and a polite inter-request delay for crawls.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from email.message import Message
from typing import TypedDict

from pf_core.utils.env import resolve_str

from pagespring import __version__

_RETRY_AFTER_CAP = 30.0  # seconds — don't let a server park a crawl for minutes

_UA_DEFAULT = f"pagespring/{__version__} (+https://github.com/phierceweb/pagespring)"
_UA_ENV_VAR = "PAGESPRING_UA"


def _ua() -> str:
    """The identifying default UA, or PAGESPRING_UA for sources that need another."""
    return resolve_str(None, _UA_ENV_VAR, default=_UA_DEFAULT) or _UA_DEFAULT


def _request(url: str, extra: dict[str, str] | None = None) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": _ua(),
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            **(extra or {}),
        },
    )


def _retry_after(exc: urllib.error.HTTPError, attempt: int) -> float:
    """Seconds to wait on a 429 — the server's Retry-After (capped) when sane,
    else the normal backoff."""
    try:
        return min(float(exc.headers.get("Retry-After", "")), _RETRY_AFTER_CAP)
    except ValueError:
        return 0.5 * (attempt + 1)


def _read(url: str, timeout: float, retries: int) -> tuple[str, bytes, Message]:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(_request(url), timeout=timeout) as r:
                return r.geturl(), r.read(), r.headers
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 429:
                if attempt < retries:
                    time.sleep(_retry_after(exc, attempt))
            elif 400 <= exc.code < 500 and exc.code != 408:
                raise  # permanent client error — retrying can't help
            elif attempt < retries:  # 5xx / 408
                time.sleep(0.5 * (attempt + 1))
        except Exception as exc:  # URLError, timeout, connection reset, …
            last = exc
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    raise last  # type: ignore[misc]


def fetch_text(
    url: str, *, timeout: float = 30, retries: int = 2, encoding: str | None = None
) -> tuple[str, str]:
    """Return (final_url, decoded_text) after following redirects.

    Decodes with ``encoding`` when given, else the response's Content-Type
    charset, else utf-8 — always with replacement, never raising."""
    final_url, raw, headers = _read(url, timeout, retries)
    return final_url, raw.decode(encoding or headers.get_content_charset() or "utf-8", "replace")


def fetch_bytes(url: str, *, timeout: float = 180, retries: int = 2) -> tuple[str, bytes]:
    """Return (final_url, raw_bytes) — for binary downloads (PDFs, archives,
    images). Longer default timeout than fetch_text: vendor PDFs/doc archives
    can be tens of MB on slow CDNs."""
    final_url, raw, _headers = _read(url, timeout, retries)
    return final_url, raw


class Validators(TypedDict):
    """The response's cache validators (either may be absent from a server)."""

    etag: str | None
    last_modified: str | None


def fetch_bytes_meta(
    url: str, *, timeout: float = 180, retries: int = 2
) -> tuple[str, bytes, Validators]:
    """``fetch_bytes`` + the response's cache validators, for callers that
    persist them (a later ``not_modified`` probe skips the re-download)."""
    final_url, raw, headers = _read(url, timeout, retries)
    return (
        final_url,
        raw,
        {
            "etag": headers.get("ETag"),
            "last_modified": headers.get("Last-Modified"),
        },
    )


def not_modified(url: str, *, etag: str | None, last_modified: str | None) -> bool:
    """One conditional GET: True ONLY on a definitive 304. False on anything
    else — changed content, no validators to send, or any error — so a caller
    can always fall back to the full fetch path safely. Never raises."""
    if not etag and not last_modified:
        return False
    extra: dict[str, str] = {}
    if etag:
        extra["If-None-Match"] = etag
    if last_modified:
        extra["If-Modified-Since"] = last_modified
    try:
        with urllib.request.urlopen(_request(url, extra), timeout=30):
            return False
    except urllib.error.HTTPError as exc:
        return exc.code == 304
    except Exception:
        return False


def polite_sleep(seconds: float = 0.25) -> None:
    """Sleep between crawl requests to avoid hammering the source."""
    time.sleep(seconds)
