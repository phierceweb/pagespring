"""How a Docsify site maps routes to files, read from ``window.$docsify`` by regex (options set by
another script are missed): ``alias``, then ``README`` for a directory, ``ext``, ``basePath``."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from pf_core.log import get_logger

from pagespring.patterns._site import names_a_file

log = get_logger(__name__)

_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
# Not the // of a URL or of a protocol-relative string.
_LINE_COMMENT_RE = re.compile(r"(?<![:'\"`\\])//[^\n]*")
_ALIAS_RE = re.compile(r"(?<![\w$.])alias\s*:\s*\{(.*?)\}", re.S)
_PAIR_RE = re.compile(r"(['\"`])(.+?)\1\s*:\s*(['\"`])(.*?)\3")
_SCHEME_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9+.-]*:|//)")
_LINK_OPTION_RE = re.compile(r"(?:^|\s):([\w-]+):?=?([\w%-]+)?")
# Python's re cannot be interrupted mid-match, so a site's alias pattern is run only
# when its backtracking stays polynomial.
_MAX_ALIAS_QUANTIFIERS = 2
_MAX_ALIAS_CHOICES = 2
# Python inline flags ((?x) drops the space in "(a+) +"); JS has none, so no real alias uses one.
_INLINE_FLAG_RE = re.compile(r"\(\?[A-Za-z]")


@dataclass(frozen=True)
class Site:
    root: str
    homepage: str
    alias: tuple[tuple[str, str], ...]
    relative_links: bool
    ext: str = ".md"


def config(html: str) -> str:
    """The inline script holding ``$docsify``, comments removed ('' when absent)."""
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script"):
        text = script.get_text()
        if "$docsify" in text:
            return _LINE_COMMENT_RE.sub("", _BLOCK_COMMENT_RE.sub("", text))
    return ""


def option(js: str, key: str) -> str | bool | None:
    """A string or boolean option; None when the config does not set it."""
    m = re.search(rf"(?<![\w$.]){key}\s*:\s*(?:(['\"`])(.*?)\1|(true|false)\b)", js, re.S)
    if m is None:
        return None
    return m.group(3) == "true" if m.group(3) else m.group(2)


def link_options(title: str) -> dict[str, str | bool]:
    """The ``:key=value`` options a link title carries; a bare ``:key`` is True."""
    return {key: value or True for key, value in _LINK_OPTION_RE.findall(title)}


def _bounded(key: str) -> bool:
    """Whether the alias pattern ``key`` has no inline flag, no quantified group, at most two
    quantifiers, and at most two groups (or the whole key) holding a ``|``."""
    if _INLINE_FLAG_RE.search(key):
        return False
    quantifiers = choices = 0
    bars = [False]  # per open group, the key itself first: holds a | yet
    in_class = escaped = False
    prev = ""
    for c in key:
        if escaped:
            escaped, prev = False, "\\"
            continue
        if c == "\\":
            escaped = True
            continue
        if in_class:
            in_class, prev = c != "]", c
            continue
        if c == "[":
            in_class = True
        elif c == "(":
            bars.append(False)
        elif c == ")" and len(bars) > 1:
            choices += bars.pop()
        elif c == "|":
            bars[-1] = True
        elif c in "*+?{":
            if prev == ")":
                return False
            if not (c == "?" and prev in ("(", "*", "+", "?", "}")):
                quantifiers += 1
        prev = c
    choices += bars[0]
    return quantifiers <= _MAX_ALIAS_QUANTIFIERS and choices <= _MAX_ALIAS_CHOICES


def _root(page_url: str, base_path: str, *, history: bool) -> str:
    """Where routes resolve: ``basePath`` against the page's directory (hash
    routing) or the origin (history routing)."""
    p = urlparse(page_url)
    origin = f"{p.scheme}://{p.netloc}"
    if _SCHEME_RE.match(base_path):
        root = urljoin(page_url, base_path)
    elif base_path.startswith("/"):
        root = origin + base_path
    elif history:
        root = f"{origin}/{base_path}"
    else:
        head, _, last = p.path.rpartition("/")
        directory = f"{head}/" if names_a_file(last) else p.path.rstrip("/") + "/"
        root = origin + directory + base_path
    return root if root.endswith("/") else root + "/"


def read_site(page_url: str, js: str) -> Site:
    base_path = option(js, "basePath")
    homepage = option(js, "homepage")
    ext = option(js, "ext")
    aliases = _ALIAS_RE.search(js)
    pairs = [
        (key, value)
        for _q, key, _q2, value in _PAIR_RE.findall(aliases.group(1) if aliases else "")
    ]
    for key, _value in pairs:
        if not _bounded(key):
            log.warning("docsify.alias_skipped", pattern=key)
    return Site(
        root=_root(
            page_url,
            base_path if isinstance(base_path, str) else "",
            history=option(js, "routerMode") == "history",
        ),
        homepage=homepage if isinstance(homepage, str) and homepage else "README.md",
        alias=tuple(pair for pair in pairs if _bounded(pair[0])),
        relative_links=option(js, "relativePath") is True,
        ext=ext if isinstance(ext, str) else ".md",
    )


def _substitute(value: str, m: re.Match[str]) -> str:
    def group(ref: re.Match[str]) -> str:
        n = int(ref.group(1))
        return (m.group(n) or "") if n <= (m.re.groups or 0) else ref.group(0)

    return re.sub(r"\$(\d)", group, value)


def _aliased(path: str, alias: tuple[tuple[str, str], ...]) -> str:
    """``path`` through the alias table: first matching key, repeated until none does."""
    last = None
    for _ in range(len(alias) + 1):
        for key, value in alias:
            try:
                m = re.fullmatch(key, path)
            except re.error:
                continue
            if m and path != last:
                last, path = path, _substitute(value, m)
                break
        else:
            break
    return path


def file_url(route: str, site: Site) -> str:
    """The URL of the file ``route`` loads."""
    path = _aliased(route, site.alias)
    if not (path.endswith(site.ext) or names_a_file(path.rsplit("/", 1)[-1])):
        path = f"{path}README{site.ext}" if path.endswith("/") else f"{path}{site.ext}"
    if path == f"/README{site.ext}":
        path = site.homepage
    return path if _SCHEME_RE.match(path) else urljoin(site.root, path.lstrip("/"))


def route_dir(route: str) -> str:
    return route if route.endswith("/") else route.rsplit("/", 1)[0] + "/"


def route_of(target: str, base_dir: str = "/") -> tuple[str, str] | None:
    """(route, anchor) a link names; None for an external link or an in-page anchor."""
    if target.startswith("#/"):
        target = target[1:]
    elif target.startswith("#") or _SCHEME_RE.match(target):
        return None
    path, _, anchor = target.partition("#")
    path, _, query = path.partition("?")
    if not path:
        return None
    heading = re.search(r"(?:^|&)id=([^&]+)", query)
    anchor = anchor or (heading.group(1) if heading else "")
    path = re.sub(r"\.md$", "", path)
    joined = "/" + (path if path.startswith("/") else base_dir + path).lstrip("/")
    route = posixpath.normpath(joined)
    return (route + "/" if joined.endswith("/") and route != "/" else route), anchor


def link_url(target: str, route: str, site: Site) -> str:
    """A link on the page at ``route``, resolved to the file it routes to."""
    found = route_of(target, route_dir(route) if site.relative_links else "/")
    if found is None:
        return target
    path, anchor = found
    url = file_url(path, site)
    return f"{url}#{anchor}" if anchor else url


def asset_url(target: str, route: str, site: Site) -> str:
    """An image or embedded file on the page at ``route``: relative to its directory."""
    if _SCHEME_RE.match(target) or target.startswith("#"):
        return target
    if target.startswith("/"):
        return urljoin(site.root, target.lstrip("/"))
    return urljoin(site.root + route_dir(route).lstrip("/"), target)
