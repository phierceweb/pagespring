"""pagespring's policy over ``pw_core.fetch``: User-Agent, ``timeout=``, URL encoding, crawl delay,
size caps. Raw urllib errors propagate, TLS is always verified, private addresses are refused."""

from __future__ import annotations

import re
import time
import unicodedata
from urllib.parse import quote, urlsplit

from pw_core.fetch import Body, Fetcher, Validators
from pw_foundation.exceptions import ClientError, InvalidInputError
from pw_foundation.utils.env import resolve_int, resolve_str

from pagespring import __version__

__all__ = [
    "Body",
    "Validators",
    "check_download_budget",
    "fetch_body",
    "fetch_bytes",
    "fetch_bytes_meta",
    "fetch_text",
    "not_modified",
    "polite_sleep",
    "post_form",
]

_UA_DEFAULT = f"pagespring/{__version__} (+https://github.com/phierceweb/pagespring)"
_UA_ENV_VAR = "PAGESPRING_UA"


def user_agent() -> str:
    """The identifying default UA, or PAGESPRING_UA for sources that need another."""
    return resolve_str(None, _UA_ENV_VAR, default=_UA_DEFAULT) or _UA_DEFAULT


# pw-core's fetch default is unlimited. Two budgets because an HTML page and a
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


# What an HTTP request line cannot carry: controls, space, DEL and every non-ASCII character.
_UNSENDABLE = re.compile(r"[^\x21-\x7e]")
# Python's IDNA 2003 codec (Unicode 3.2) encodes these unlike a browser's UTS 46: another host.
_IDNA_DEVIATIONS = re.compile(
    r"[\u00df\u1e9e\u03c2\u200c\u200d\u115f\u1160\u17b4\u17b5\u3164\uffa0"
    r"\U0002f868\U0002f874\U0002f91f\U0002f95f\U0002f9bf]"
)
_LABEL_DOTS = re.compile("[.\u3002\uff0e\uff61]")
# A browser drops these anywhere in a URL, and strips C0 controls and spaces from its ends.
_BROWSER_DROPS = re.compile(r"[\t\n\r]")
_C0_AND_SPACE = "".join(map(chr, range(0x21)))


def _newer_and_unsafe(char: str) -> bool:
    """Whether Unicode added ``char`` after the codec's 3.2 and it is not a letter, digit or symbol,
    which both encoders keep; a browser may drop or refuse any other newer character."""
    return (
        unicodedata.ucd_3_2_0.category(char) == "Cn" and unicodedata.category(char)[0] not in "LNS"
    )


def _idna_host(host: str) -> str:
    """``host`` IDNA-encoded as a browser encodes it; a host it might encode otherwise is refused.

    Raises:
        InvalidInputError: the host cannot be encoded, or a browser might reach another host.
    """
    try:
        # The codec folds only non-ASCII labels; a browser lowercases every one.
        ascii_host = host.encode("idna").decode("ascii").lower()
        labels = ascii_host.encode("ascii").decode("idna")
    except UnicodeError as exc:
        raise InvalidInputError(f"not a valid internationalized host: {host!r}") from exc
    if (
        _IDNA_DEVIATIONS.search(host)
        or any(_newer_and_unsafe(c) for c in host)
        or len(_LABEL_DOTS.findall(host)) != ascii_host.count(".")
        or unicodedata.normalize("NFKC", labels.casefold()) != labels
    ):
        raise InvalidInputError(f"host may not encode as a browser sends it: {host!r}")
    return ascii_host


def _ascii_url(url: str) -> str:
    """``url`` as a browser sends it: tabs and line breaks dropped, ends trimmed, the host
    IDNA-encoded, any other unsendable character percent-encoded as UTF-8; escapes are kept.

    Raises:
        InvalidInputError: the URL cannot be read, or its host, userinfo or port cannot be sent.
    """
    if not _UNSENDABLE.search(url):
        return url
    url = _BROWSER_DROPS.sub("", url).strip(_C0_AND_SPACE)
    try:
        netloc = urlsplit(url).netloc
    except ValueError as exc:
        raise InvalidInputError(f"not a readable URL: {url!r}") from exc
    if not netloc.isascii():
        userinfo, at, hostport = netloc.rpartition("@")
        host, colon, port = hostport.partition(":")
        if not (userinfo + port).isascii():
            raise InvalidInputError(f"a URL's userinfo and port must be ASCII: {url!r}")
        ascii_host = _idna_host(host)
        url = url.replace(netloc, f"{userinfo}{at}{ascii_host}{colon}{port}", 1)
    return _UNSENDABLE.sub(lambda m: quote(m.group(), safe=""), url)


def _fetcher(retries: int = 2, *, max_bytes: int | None = None) -> Fetcher:
    """A fetch core with pagespring's UA, built per call so ``PAGESPRING_UA`` can change; TLS is
    explicit (pw-core's switch is process-wide), and the cap bounds the inflated body."""
    return Fetcher(user_agent=user_agent(), retries=retries, verify_tls=True, max_bytes=max_bytes)


def fetch_text(
    url: str, *, timeout: float = 30, retries: int = 2, encoding: str | None = None
) -> tuple[str, str]:
    """(final_url, text) after redirects, decoded by ``encoding``, else the Content-Type charset,
    else utf-8, with replacement."""
    return _fetcher(retries, max_bytes=_text_max_bytes()).get_text(
        _ascii_url(url), timeout_s=timeout, encoding=encoding
    )


def fetch_body(url: str, *, timeout: float = 30, retries: int = 2) -> Body:
    """``fetch_text``'s request, keeping the bytes, their charset and the validators;
    ``Body.text()`` decodes exactly as ``fetch_text`` does."""
    return _fetcher(retries, max_bytes=_text_max_bytes()).get_body(
        _ascii_url(url), timeout_s=timeout
    )


def check_download_budget(url: str, data: bytes) -> None:
    """Refuse a download read under the text budget that the download budget would have refused,
    with the fetch core's own error."""
    cap = _download_max_bytes()
    if len(data) > cap:
        raise ClientError("response exceeded max_bytes", context={"url": url, "max_bytes": cap})


def fetch_bytes(url: str, *, timeout: float = 180, retries: int = 2) -> tuple[str, bytes]:
    """(final_url, bytes) for binary downloads, with a longer timeout: vendor PDFs and archives can
    be tens of MB on a slow CDN."""
    return _fetcher(retries, max_bytes=_download_max_bytes()).get_bytes(
        _ascii_url(url), timeout_s=timeout
    )


def fetch_bytes_meta(
    url: str, *, timeout: float = 180, retries: int = 2
) -> tuple[str, bytes, Validators]:
    """``fetch_bytes`` + the response's cache validators, for callers that
    persist them (a later ``not_modified`` probe skips the re-download)."""
    return _fetcher(retries, max_bytes=_download_max_bytes()).get_bytes_meta(
        _ascii_url(url), timeout_s=timeout
    )


def not_modified(url: str, *, etag: str | None, last_modified: str | None) -> bool:
    """One conditional GET: True on a 304, or a 200 carrying the strong ETag it sent; False on
    anything else, errors included, so the caller can always fall back to a full fetch."""
    try:
        sendable = _ascii_url(url)
    except InvalidInputError:
        return False
    return _fetcher(max_bytes=_text_max_bytes()).not_modified(
        sendable, etag=etag, last_modified=last_modified
    )


def post_form(
    url: str, fields: dict[str, str], *, timeout: float = 30, retries: int = 2
) -> tuple[str, str]:
    """``fetch_text`` for an endpoint that only answers a form POST: same UA, cap, retries and SSRF
    guard; the body is decoded by the Content-Type charset, else utf-8."""
    body = _fetcher(retries, max_bytes=_text_max_bytes()).post_form(
        _ascii_url(url), fields, timeout_s=timeout
    )
    return body.final_url, body.text()


def polite_sleep(seconds: float = 0.25) -> None:
    """Sleep between crawl requests to avoid hammering the source."""
    time.sleep(seconds)
