"""Links between an HTML archive's members, re-pointed inside the one deliverable.

A link naming a member file becomes an in-document ``#fragment``. Whether its target
made it into the deliverable, and where that member starts, is known only once every
member is read, so links are marked member by member and resolved over the joined text.
Members often repeat an id (a Sphinx ``examples`` section on every page), so an id an
earlier member holds is renamed, and the links to it follow.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from urllib.parse import unquote

from bs4.element import NavigableString, Tag

from pagespring.patterns._archive_images import member_path

_MARK = "pagespring-member:"
_MARK_RE = re.compile(rf'\shref="{_MARK}(\d+)"')


def _start_anchor(root: Tag) -> str | None:
    """The first id in ``root`` that no text precedes, else None."""
    for node in root.descendants:
        if isinstance(node, Tag):
            anchor = node.get("id")
            if isinstance(anchor, str) and anchor:
                return anchor
        elif type(node) is NavigableString and node.strip():
            return None
    return None


def _renamed(renames: dict[str, str], fragment: str) -> str:
    """``fragment`` as the id it names now; written percent-encoded or not."""
    return renames.get(fragment) or renames.get(unquote(fragment)) or fragment


class MemberLinks:
    """Re-points the ``<a href>`` links that name one of the archive's HTML members."""

    def __init__(self, raw_dir: Path, exts: tuple[str, ...]) -> None:
        self._root = raw_dir.resolve()
        self._members = {
            p.resolve() for p in raw_dir.rglob("*") if p.suffix.lower() in exts and p.is_file()
        }
        self._starts: dict[Path, str | None] = {}
        self._renames: dict[Path, dict[str, str]] = {}
        self._ids: set[str] = set()  # every id the members included so far hold
        self._marks: list[tuple[Path, str]] = []

    def include(self, root: Tag, member: Path) -> None:
        """Record ``member`` as part of the deliverable, rename the ids an earlier member
        holds, and mark the member links in it."""
        key = member.resolve()
        renames = self._renames[key] = self._claim_ids(root)
        self._starts[key] = _start_anchor(root)
        for a in root.find_all("a"):
            href = a.get("href")
            if not isinstance(href, str):
                continue
            if href.startswith("#"):
                if renames:
                    a["href"] = f"#{_renamed(renames, href[1:])}"
                continue
            target = member_path(href, member, self._root)
            if target is not None and target in self._members:
                a["href"] = f"{_MARK}{len(self._marks)}"
                self._marks.append((target, href.partition("#")[2]))

    def _claim_ids(self, root: Tag) -> dict[str, str]:
        """Give each id in ``root`` an earlier member holds a free ``<id>-<n>`` name;
        returns old -> new. A repeat within the member keeps sharing one name."""
        tags = [t for t in root.find_all(id=True) if isinstance(t.get("id"), str)]
        own = {str(t["id"]) for t in tags}
        renames: dict[str, str] = {}
        for tag in tags:
            old = str(tag["id"])
            if old not in self._ids:
                continue
            if old not in renames:
                n = 2
                while (new := f"{old}-{n}") in self._ids or new in own:
                    n += 1
                renames[old] = new
                own.add(new)
            tag["id"] = renames[old]
        self._ids |= own
        return renames

    def resolve(self, text: str) -> str:
        """``text`` with each marked link pointing at its anchor, or stripped of its
        href when the target member is not in the deliverable or starts without one."""

        def repl(m: re.Match[str]) -> str:
            target, fragment = self._marks[int(m.group(1))]
            if target not in self._starts:
                return ""
            anchor = _renamed(self._renames[target], fragment) if fragment else None
            anchor = anchor or self._starts[target]
            return f' href="#{html.escape(anchor)}"' if anchor else ""

        return _MARK_RE.sub(repl, text)
