"""pagespring's policy over ``pf_core.fetch``: User-Agent, ``timeout=``, crawl delay, size caps. Raw
urllib errors propagate, TLS is always verified, and private addresses are refused."""

from __future__ import annotations

import time

from pf_core.fetch import Fetcher, Validators
from pf_core.utils.env import resolve_int, resolve_str

from pagespring import __version__

__all__ = [
    "Validators",
    "fetch_bytes",
    "fetch_bytes_meta",
    "fetch_text",
    "not_modified",
    "polite_sleep",
]

_UA_DEFAULT = f"pagespring/{__version__} (+https://github.com/phierceweb/pagespring)"
_UA_ENV_VAR = "PAGESPRING_UA"


def _ua() -> str:
    """The identifying default UA, or PAGESPRING_UA for sources that need another."""
    return resolve_str(None, _UA_ENV_VAR, default=_UA_DEFAULT) or _UA_DEFAULT


# pf-core's fetch default is unlimited. Two budgets because an HTML page and a
# vendor PDF differ by an order of magnitude; one cap is useless or too tight.
_TEXT_MAX_BYTES_DEFAULT = 25 * 1024 * 1024
_DOWNLOAD_MAX_BYTES_DEFAULT = 250 * 1024 * 1024
_TEXT_MAX_BYTES_ENV_VAR = "PAGESPRING_MAX_TEXT_BYTES"
_DOWNLOAD_MAX_BYTES_ENV_VAR = "PAGESPRING_MAX_DOWNLOAD_BYTES"


def _text_max_bytes() -> int:
    """Cap for HTML / sitemap fetches; PAGESPRING_MAX_TEXT_BYTES overrides."""
    n: int = resolve_int(None, _TEXT_MAX_BYTES_ENV_VAR, default=_TEXT_MAX_BYTES_DEFAULT)
    return n if n > 0 else _TEXT_MAX_BYTES_DEFAULT


def _download_max_bytes() -> int:
    """Cap for binary downloads; PAGESPRING_MAX_DOWNLOAD_BYTES overrides."""
    n: int = resolve_int(None, _DOWNLOAD_MAX_BYTES_ENV_VAR, default=_DOWNLOAD_MAX_BYTES_DEFAULT)
    return n if n > 0 else _DOWNLOAD_MAX_BYTES_DEFAULT


def _fetcher(retries: int = 2, *, max_bytes: int | None = None) -> Fetcher:
    """A fetch core with pagespring's UA, built per call so ``PAGESPRING_UA`` can change; TLS is
    explicit (pf-core's switch is process-wide), and the cap bounds the inflated body."""
    return Fetcher(user_agent=_ua(), retries=retries, verify_tls=True, max_bytes=max_bytes)


def fetch_text(
    url: str, *, timeout: float = 30, retries: int = 2, encoding: str | None = None
) -> tuple[str, str]:
    """(final_url, text) after redirects, decoded by ``encoding``, else the Content-Type charset,
    else utf-8, with replacement."""
    return _fetcher(retries, max_bytes=_text_max_bytes()).get_text(
        url, timeout_s=timeout, encoding=encoding
    )


def fetch_bytes(url: str, *, timeout: float = 180, retries: int = 2) -> tuple[str, bytes]:
    """(final_url, bytes) for binary downloads, with a longer timeout: vendor PDFs and archives can
    be tens of MB on a slow CDN."""
    return _fetcher(retries, max_bytes=_download_max_bytes()).get_bytes(url, timeout_s=timeout)


def fetch_bytes_meta(
    url: str, *, timeout: float = 180, retries: int = 2
) -> tuple[str, bytes, Validators]:
    """``fetch_bytes`` + the response's cache validators, for callers that
    persist them (a later ``not_modified`` probe skips the re-download)."""
    return _fetcher(retries, max_bytes=_download_max_bytes()).get_bytes_meta(url, timeout_s=timeout)


def not_modified(url: str, *, etag: str | None, last_modified: str | None) -> bool:
    """One conditional GET: True on a 304, or a 200 carrying the strong ETag it sent; False on
    anything else, errors included, so the caller can always fall back to a full fetch."""
    return _fetcher(max_bytes=_text_max_bytes()).not_modified(
        url, etag=etag, last_modified=last_modified
    )


def polite_sleep(seconds: float = 0.25) -> None:
    """Sleep between crawl requests to avoid hammering the source."""
    time.sleep(seconds)
