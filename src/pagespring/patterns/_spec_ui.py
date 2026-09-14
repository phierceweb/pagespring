"""Spec discovery for API reference UIs (Swagger UI, Redoc, Scalar) — used by docs_probe.

These pages are script shells around an OpenAPI document. Without running script,
the evidence is a spec URL in the page's own query, written literally in the page or
in Swagger UI's initializer script, or listed by a Swagger UI config document, proved
by fetching and parsing it.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
from bs4.element import Tag
from pf_core.exceptions import InvalidInputError

from pagespring import http
from pagespring.patterns.api_spec import is_openapi

Fetch = Callable[[str], str | None]

_MAX_CANDIDATES = 5
# A whole quoted string naming a spec file; a ${...} interpolation breaks the match.
# The query ends at an interpolation or an escaped quote; an interpolated query is a
# cache-buster or template, so the URL is taken without it.
_SPEC_TOKEN_RE = re.compile(
    r"""["'`]((?:https?://)?[\w.~%/:@+-]+\.(?:json|ya?ml))(\?[^"'`\s{}$\\]*)?"""
    r"""((?:\$?\{|\\)[^"'`]*)?["'`]"""
)
_CONFIG_URL_RE = re.compile(r"""\bconfigUrl["']?\s*:\s*["'`]([^"'`\s{}]+)["'`]""")
# The stock Swagger UI dist ships pointed at these; on any site they are not its API.
_DEMO_SPEC_HOSTS = frozenset({"petstore.swagger.io", "petstore3.swagger.io"})
# A Next.js page ships its own text again inside inline scripts; that text is prose.
_PAYLOAD_PREFIX = "self.__next_f"


def fetch_or_none(url: str) -> str | None:
    """The body at ``url``, or None for any failure — a candidate that won't load isn't a spec."""
    try:
        _final, body = http.fetch_text(url)
    except Exception:
        return None
    return body


def _inline_scripts(soup: BeautifulSoup) -> list[str]:
    texts = (s.get_text() for s in soup.find_all("script") if not s.get("src"))
    return [t for t in texts if not t.lstrip().startswith(_PAYLOAD_PREFIX)]


def ui_name(html: str) -> str | None:
    """The API reference UI a page is built on, from its elements and scripts."""
    soup = BeautifulSoup(html, "html.parser")
    srcs = " ".join(str(s.get("src") or "") for s in soup.find_all("script")).lower()
    inline = " ".join(_inline_scripts(soup))
    if soup.find("redoc") is not None or ("redoc" in srcs and "Redoc.init(" in inline):
        return "redoc"
    if (
        "@scalar/api-reference" in srcs
        or ("scalar" in srcs and "createApiReference(" in inline)
        or soup.find("script", id="api-reference") is not None
    ):
        return "scalar"
    if soup.find(id="swagger-ui") is not None and (
        "swagger-ui-bundle" in srcs or "SwaggerUIBundle(" in inline
    ):
        return "swagger-ui"
    return None


def find_spec(
    page_url: str, html: str, ui: str, *, fetch: Fetch = fetch_or_none
) -> tuple[str, str]:
    """(URL, text) of the one OpenAPI document ``html`` names, proved by fetching and parsing it.

    Every fetch is paced, since the caller has just fetched the page.

    Raises:
        InvalidInputError: the page names no parseable spec, or several.
    """
    explicit, literal = _candidates(page_url, html, fetch)
    candidates = explicit or literal
    if len(candidates) > _MAX_CANDIDATES:
        raise InvalidInputError(_listing(page_url, ui, "names", candidates))
    proven: dict[str, str] = {}
    for url in candidates:
        body = _paced(fetch, url)
        if body is not None and is_openapi(body):
            proven[url] = body
    if len(proven) == 1:
        return next(iter(proven.items()))
    if proven:
        raise InvalidInputError(_listing(page_url, ui, "serves", list(proven)))
    raise InvalidInputError(
        f"{page_url} is a {ui} page, but no spec URL written in it parses as OpenAPI "
        "(it may be computed in script). Ingest the spec file's URL, or a saved copy."
    )


def _paced(fetch: Fetch, url: str) -> str | None:
    http.polite_sleep()
    return fetch(url)


def _candidates(page_url: str, html: str, fetch: Fetch) -> tuple[list[str], list[str]]:
    """(explicit, literal) spec URLs: a ``url`` in the page query alone, else element
    attributes and config documents, then script tokens."""
    query = parse_qs(urlparse(page_url).query)
    if query.get("url"):
        return _absolute(page_url, query["url"]), []
    soup = BeautifulSoup(html, "html.parser")
    explicit = [str(t["spec-url"]) for t in soup.find_all("redoc") if t.get("spec-url")]
    reference = soup.find("script", id="api-reference")
    if isinstance(reference, Tag) and reference.get("data-url"):
        explicit.append(str(reference["data-url"]))

    texts = _inline_scripts(soup)
    for script in soup.find_all("script", src=True):
        src = urljoin(page_url, str(script["src"]))
        if src.endswith("/swagger-initializer.js") and _same_origin(src, page_url):
            body = _paced(fetch, src)
            if body is not None:
                texts.append(body)
    config_refs = query.get("configUrl") or [
        ref for text in texts for ref in _CONFIG_URL_RE.findall(text)
    ]
    for config_url in _absolute(page_url, config_refs):
        explicit += _config_spec_refs(_paced(fetch, config_url))
    literal = [_spec_token(m) for text in texts for m in _SPEC_TOKEN_RE.finditer(text)]
    return _absolute(page_url, explicit), _absolute(page_url, literal)


def _spec_token(m: re.Match[str]) -> str:
    path, query, tail = m.group(1), m.group(2) or "", m.group(3) or ""
    return path if tail.startswith(("$", "{")) else path + query


def _config_spec_refs(body: str | None) -> list[str]:
    """The ``url`` and ``urls[].url`` entries of a Swagger UI config document."""
    try:
        config = json.loads(body) if body is not None else None
    except ValueError:
        return []
    if not isinstance(config, dict):
        return []
    urls = config.get("urls")
    entries = [config, *(urls if isinstance(urls, list) else [])]
    return [e["url"] for e in entries if isinstance(e, dict) and isinstance(e.get("url"), str)]


def _absolute(page_url: str, refs: list[str]) -> list[str]:
    urls = (urljoin(page_url, ref) for ref in refs)
    return list(dict.fromkeys(u for u in urls if urlparse(u).hostname not in _DEMO_SPEC_HOSTS))


def _same_origin(url: str, page_url: str) -> bool:
    return urlparse(url).netloc == urlparse(page_url).netloc


def _listing(page_url: str, ui: str, verb: str, urls: list[str]) -> str:
    lines = "\n".join(f"  {url}" for url in urls)
    return f"{page_url} is a {ui} page that {verb} {len(urls)} spec URLs — ingest one:\n{lines}"
