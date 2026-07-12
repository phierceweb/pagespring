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

from pf_core.utils.env import resolve_str

from pagespring import __version__

_RETRY_AFTER_CAP = 30.0  # seconds — don't let a server park a crawl for minutes

_UA_DEFAULT = f"pagespring/{__version__} (+https://github.com/phierceweb/pagespring)"
_UA_ENV_VAR = "PAGESPRING_UA"


def _ua() -> str:
    """The identifying default UA, or PAGESPRING_UA for sources that need another."""
    return resolve_str(None, _UA_ENV_VAR, default=_UA_DEFAULT) or _UA_DEFAULT


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": _ua(),
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )


def _retry_after(exc: urllib.error.HTTPError, attempt: int) -> float:
    """Seconds to wait on a 429 — the server's Retry-After (capped) when sane,
    else the normal backoff."""
    try:
        return min(float(exc.headers.get("Retry-After", "")), _RETRY_AFTER_CAP)
    except ValueError:
        return 0.5 * (attempt + 1)


def _read(url: str, timeout: float, retries: int) -> tuple[str, bytes, str | None]:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(_request(url), timeout=timeout) as r:
                return r.geturl(), r.read(), r.headers.get_content_charset()
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
    final_url, raw, charset = _read(url, timeout, retries)
    return final_url, raw.decode(encoding or charset or "utf-8", "replace")


def fetch_bytes(url: str, *, timeout: float = 180, retries: int = 2) -> tuple[str, bytes]:
    """Return (final_url, raw_bytes) — for binary downloads (PDFs, archives,
    images). Longer default timeout than fetch_text: vendor PDFs/doc archives
    can be tens of MB on slow CDNs."""
    final_url, raw, _charset = _read(url, timeout, retries)
    return final_url, raw


def polite_sleep(seconds: float = 0.25) -> None:
    """Sleep between crawl requests to avoid hammering the source."""
    time.sleep(seconds)
