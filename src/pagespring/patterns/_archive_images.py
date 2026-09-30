"""Archive members a deliverable references, bundled beside it.

An EPUB or doc zip names its figures by paths inside the extracted archive, which
is discarded after normalize. ``MemberImages`` copies each referenced member into
``IMAGES_DIR`` beside the normalized file and re-points the ref at the copy.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from urllib.parse import unquote

from bs4 import Tag

from pagespring.base import IMAGES_DIR
from pagespring.patterns._md_code import outside_code

_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_MD_IMAGE_RE = re.compile(r"(!\[[^\]]*\]\()([^)\s]+)")
_INLINE_IMG_RE = re.compile(r"""(<img\b[^>]*?\bsrc=["'])([^"']+)""", re.IGNORECASE)
_RST_IMAGE_RE = re.compile(r"^([ \t]*\.\. (?:image|figure)::[ \t]+)(\S+)", re.MULTILINE)
_HTML_REFS = (("img", "src"), ("image", "href"), ("image", "xlink:href"))


def member_path(target: str, member: Path, root: Path) -> Path | None:
    """The resolved path ``target`` names as written in ``member``, or None for a URL
    or a bare fragment. The path may lie outside ``root`` or not exist."""
    target = target.strip()
    path = unquote(target.split("#", 1)[0].split("?", 1)[0])
    if not path or target.startswith("//") or _SCHEME_RE.match(target):
        return None
    base = root if path.startswith("/") else member.parent
    return (base / path.lstrip("/")).resolve()


class MemberImages:
    """Names and copies the archive members one deliverable references."""

    def __init__(self, raw_dir: Path, out_dir: Path) -> None:
        self._root = raw_dir.resolve()
        self._dest = out_dir / IMAGES_DIR
        self._names: dict[Path, str] = {}
        self._taken: set[str] = set()
        self.copied = 0

    def ref(self, target: str, member: Path, *, only_present: bool = False) -> str | None:
        """The ``images/<name>`` ref for ``target`` as written in ``member``, or None to
        leave it (a URL, a fragment, or with ``only_present`` a path the archive lacks).

        A path the archive lacks or escapes is re-pointed uncopied, so audit reports it."""
        found = member_path(target, member, self._root)
        if found is None:
            return None
        present = self._root in found.parents and found.is_file()
        if only_present and not present:
            return None
        name = self._names.get(found)
        if name is None:
            name = self._names[found] = self._claim(found.name)
            if present:
                self._dest.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(found, self._dest / name)
                self.copied += 1
        return f"{IMAGES_DIR}/{name}"

    def rewrite_html(self, soup: Tag, member: Path) -> None:
        """Re-point ``soup``'s ``<img>`` and SVG ``<image>`` refs into the bundle.

        A re-pointed ``<img>`` loses its ``srcset``: a renderer prefers it to ``src``,
        and its candidates name archive paths."""
        for tag, attr in _HTML_REFS:
            for el in soup.find_all(tag):
                value = el.get(attr)
                if isinstance(value, str) and (new := self.ref(value, member)):
                    el[attr] = new
                    for responsive in ("srcset", "sizes"):
                        el.attrs.pop(responsive, None)

    def rewrite_markdown(self, text: str, member: Path) -> str:
        """Re-point the image refs outside ``text``'s code, as ``rewrite_html`` does.
        A ref in code is a sample and stays as written."""

        def repl(m: re.Match[str]) -> str:
            new = self.ref(m.group(2), member)
            return f"{m.group(1)}{new}" if new else m.group(0)

        return outside_code(text, lambda s: _INLINE_IMG_RE.sub(repl, _MD_IMAGE_RE.sub(repl, s)))

    def rewrite_rst(self, text: str, member: Path) -> str:
        """Re-point the ``image`` and ``figure`` targets that name a member the archive
        holds. rst marks code only by indentation, so any other target stays as written."""

        def repl(m: re.Match[str]) -> str:
            new = self.ref(m.group(2), member, only_present=True)
            return f"{m.group(1)}{new}" if new else m.group(0)

        return _RST_IMAGE_RE.sub(repl, text)

    def _claim(self, filename: str) -> str:
        """A lowercase, filesystem-safe name no other member of the bundle holds."""
        stem, dot, ext = filename.rpartition(".")
        if not dot:
            stem, ext = filename, ""
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.").lower() or "image"
        ext = re.sub(r"[^a-z0-9]+", "", ext.lower())
        suffix = f".{ext}" if ext else ""
        name, n = f"{stem}{suffix}", 1
        while name in self._taken:
            n += 1
            name = f"{stem}-{n}{suffix}"
        self._taken.add(name)
        return name
