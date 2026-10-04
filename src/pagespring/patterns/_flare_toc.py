"""Flare's TOC and chunk files read as data: ``define({...})`` JS literals, not JSON, holding the
tree (node ``i`` in chunk ``c``) and, per chunk, each key's placements and titles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pf_core.exceptions import InvalidInputError

_IDENT_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
_PLAIN_RE = {"'": re.compile(r"[^'\\]+"), '"': re.compile(r'[^"\\]+')}
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
_WORDS: dict[str, Any] = {"true": True, "false": False, "null": None}
_MAX_NESTING = 200
_MAX_CHUNKS = 1000


class _Reader:
    """A cursor over the JS-literal subset Flare writes."""

    def __init__(self, text: str, source: str) -> None:
        self.text = text
        self.source = source
        self.pos = 0
        self.nesting = 0

    def fail(self, what: str) -> InvalidInputError:
        return InvalidInputError(f"{self.source} is not a Flare TOC file: {what} at {self.pos}")

    def peek(self) -> str:
        while self.pos < len(self.text) and self.text[self.pos].isspace():
            self.pos += 1
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def take(self, token: str) -> None:
        self.peek()
        if not self.text.startswith(token, self.pos):
            raise self.fail(f"expected {token!r}")
        self.pos += len(token)

    def value(self) -> Any:
        ch = self.peek()
        if ch and ch in "{[":
            self.nesting += 1
            if self.nesting > _MAX_NESTING:
                raise self.fail("nesting too deep")
            found: Any = self.object() if ch == "{" else self.array()
            self.nesting -= 1
            return found
        if ch and ch in "'\"":
            return self.string()
        number = _NUMBER_RE.match(self.text, self.pos)
        if number:
            self.pos = number.end()
            return float(number.group()) if "." in number.group() else int(number.group())
        word = _IDENT_RE.match(self.text, self.pos)
        if word and word.group() in _WORDS:
            self.pos = word.end()
            return _WORDS[word.group()]
        raise self.fail("expected a value")

    def object(self) -> dict[str, Any]:
        self.take("{")
        out: dict[str, Any] = {}
        while self.peek() != "}":
            key = self.key()
            self.take(":")
            out[key] = self.value()
            if self.peek() != ",":
                break
            self.take(",")
        self.take("}")
        return out

    def key(self) -> str:
        if self.peek() and self.peek() in "'\"":
            return self.string()
        name = _IDENT_RE.match(self.text, self.pos) or _NUMBER_RE.match(self.text, self.pos)
        if not name:
            raise self.fail("expected a key")
        self.pos = name.end()
        return name.group()

    def array(self) -> list[Any]:
        self.take("[")
        out: list[Any] = []
        while self.peek() != "]":
            out.append(self.value())
            if self.peek() != ",":
                break
            self.take(",")
        self.take("]")
        return out

    def string(self) -> str:
        quote = self.text[self.pos]
        self.pos += 1
        parts: list[str] = []
        while True:
            plain = _PLAIN_RE[quote].match(self.text, self.pos)
            if plain:
                parts.append(plain.group())
                self.pos = plain.end()
            if self.pos >= len(self.text):
                raise self.fail("unterminated string")
            if self.text[self.pos] == quote:
                self.pos += 1
                return "".join(parts)
            parts.append(self.escape())

    def escape(self) -> str:
        code = self.text[self.pos + 1 : self.pos + 2]
        self.pos += 2
        if not code:
            raise self.fail("unterminated string")
        if code in "ux":
            width = 4 if code == "u" else 2
            digits = self.text[self.pos : self.pos + width]
            if len(digits) != width or not all(c in "0123456789abcdefABCDEF" for c in digits):
                raise self.fail(f"bad \\{code} escape")
            self.pos += width
            return chr(int(digits, 16))
        if code == "\n":
            return ""
        return _ESCAPES.get(code, code)


def _read_define(text: str, source: str) -> Any:
    reader = _Reader(text.lstrip("﻿"), source)
    reader.take("define")
    reader.take("(")
    found = reader.value()
    reader.take(")")
    if reader.peek() == ";":
        reader.pos += 1
    if reader.peek():
        raise reader.fail("trailing text")
    return found


@dataclass(frozen=True)
class Toc:
    """The tree's top-level nodes and the chunk files that title them."""

    chunk_files: list[str]
    tree: list[Any]


def read_toc(text: str, *, source: str) -> Toc:
    """The TOC file at ``source``.

    Raises:
        InvalidInputError: it is not a ``define()`` of ``numchunks``, ``prefix`` and ``tree``.
    """
    data = _read_define(text, source)
    fields = data if isinstance(data, dict) else {}
    count, prefix, tree = fields.get("numchunks"), fields.get("prefix"), fields.get("tree")
    nodes = tree.get("n") if isinstance(tree, dict) else None
    if (
        type(count) is not int
        or not 0 <= count <= _MAX_CHUNKS
        or not isinstance(prefix, str)
        or not prefix
        or not isinstance(nodes, list)
    ):
        raise InvalidInputError(f"{source} is not a Flare TOC file: no numchunks, prefix and tree")
    return Toc([f"{prefix}{c}.js" for c in range(count)], nodes)


def read_chunk(text: str, chunk: int, *, source: str) -> dict[tuple[int, int], tuple[str, str]]:
    """(chunk, node index) to (key, title) for every placement the chunk file lists.

    Raises:
        InvalidInputError: it is not a ``define()`` of keys each holding parallel ``i`` and ``t``.
    """
    data = _read_define(text, source)
    if not isinstance(data, dict):
        raise InvalidInputError(f"{source} is not a Flare TOC chunk: no keyed entries")
    placements: dict[tuple[int, int], tuple[str, str]] = {}
    for key, entry in data.items():
        ids = entry.get("i") if isinstance(entry, dict) else None
        titles = entry.get("t") if isinstance(entry, dict) else None
        if not isinstance(ids, list) or not isinstance(titles, list) or len(ids) != len(titles):
            raise InvalidInputError(f"{source} is not a Flare TOC chunk: {key!r} has no titles")
        for index, title in zip(ids, titles, strict=True):
            if type(index) is not int or not isinstance(title, str):
                raise InvalidInputError(f"{source} is not a Flare TOC chunk: {key!r} is malformed")
            placements[(chunk, index)] = (key, title)
    return placements


_PAGE_RE = re.compile(r"\.html?$", re.I)
_LINK_TAIL_RE = re.compile(r"[?#].*$", re.S)
_NO_LINK = "___"


@dataclass(frozen=True)
class TocEntry:
    depth: int
    title: str
    path: str | None  # the page relative to the help-system root; None for a book or a link out


def _page_path(key: str | None) -> str | None:
    if key is None or key == _NO_LINK:
        return None
    page = _LINK_TAIL_RE.sub("", key)  # a link to a bookmark names its topic
    if not page.startswith("/") or not _PAGE_RE.search(page):
        return None
    return page[1:]


def toc_entries(toc: Toc, placements: dict[tuple[int, int], tuple[str, str]]) -> list[TocEntry]:
    """The tree in reading order; a book or out-link stays only to head the pages under it."""
    plain = {key for key, _ in placements.values() if not _LINK_TAIL_RE.search(key)}

    def walk(nodes: list[Any], depth: int) -> list[TocEntry]:
        out: list[TocEntry] = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            chunk, index = node.get("c"), node.get("i")
            found = placements.get((chunk, index)) if type(chunk) is type(index) is int else None
            key, title = found if found is not None else (None, "")
            kids = node.get("n")
            below = walk(kids, depth + 1) if isinstance(kids, list) else []
            path = _page_path(key)
            # A bookmark names its topic only where the tree never links the topic itself.
            if path is not None and key not in plain and f"/{path}" in plain:
                path = None
            if path is not None or (title and any(e.path is not None for e in below)):
                out.append(TocEntry(depth, title, path))
            out.extend(below)
        return out

    return walk(toc.tree, 0)
