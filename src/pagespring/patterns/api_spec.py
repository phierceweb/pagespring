"""api_spec — OpenAPI/Swagger specs and Postman collections.

The one pattern that recognizes a *content shape* rather than a host: API
contract files. ``match`` claims spec-ish URLs/paths (a ``.json``/``.yaml``/
``.yml`` extension, or an ``openapi``/``swagger``/``postman`` token in the final
path segment); ``acquire`` fetches or reads the file, content-sniffs OpenAPI vs
Postman, and records the operation count; ``normalize`` renders it to ONE clean
markdown file.

Point it at a spec URL (``ingest https://…/openapi.json``) or a local file
(``ingest ./vendor-openapi.json``) — the latter unblocks specs hidden behind a
ReDoc/Swagger-UI "Download" button.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from pf_core.exceptions import InvalidInputError
from pf_core.log import get_logger

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns import _openapi_render, _postman_render

log = get_logger(__name__)

_SPEC_EXTS = (".json", ".yaml", ".yml")
_TOKENS = ("openapi", "swagger", "postman")


def _last_segment(src: str) -> str:
    """The lowercased final path segment of a URL or local path."""
    path = urlsplit(src).path if "://" in src else src
    return path.rstrip("/").rsplit("/", 1)[-1].lower()


def _load_raw(src: str) -> str:
    """Raw spec text from an http(s) URL, a ``file://`` URL, or a local path."""
    if src.startswith(("http://", "https://")):
        _final, text = http.fetch_text(src)
        return text
    path = src[7:] if src.startswith("file://") else src
    p = Path(path)
    if p.is_file():
        return p.read_text(encoding="utf-8")
    raise InvalidInputError(f"not a fetchable URL or existing file: {src}")


def _load_data(text: str) -> dict[str, Any]:
    """Parse spec text as JSON, falling back to YAML. Must be a JSON object."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        import yaml  # lazy: only YAML specs pull this in

        data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise InvalidInputError("spec did not parse to a JSON/YAML object")
    return data


def sniff_format(data: dict[str, Any]) -> str | None:
    """Classify a parsed spec: 'openapi', 'postman', or None (unrecognized)."""
    if "openapi" in data or "swagger" in data:
        return "openapi"
    info = data.get("info")
    if isinstance(info, dict) and ("_postman_id" in info or "schema" in info) and "item" in data:
        return "postman"
    return None


def _title_slug(data: dict[str, Any], fmt: str, src: str) -> tuple[str, str]:
    """Human title + output slug from ``info.title`` (OpenAPI) / ``info.name``
    (Postman), plus version for OpenAPI, falling back to the source's last path
    segment."""
    raw_info = data.get("info")
    info: dict[str, Any] = raw_info if isinstance(raw_info, dict) else {}
    title = str(info.get("title") or info.get("name") or "").strip()
    if fmt == "openapi" and info.get("version"):
        title = f"{title} {info['version']}".strip()
    if not title:
        title = _last_segment(src) or "api-spec"
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "api-spec"
    return title, slug


class ApiSpecPattern:
    name = "api_spec"
    convert_recipe = ["--split-sections"]

    def match(self, url: str) -> bool:
        seg = _last_segment(url)
        if seg.endswith(_SPEC_EXTS):
            return True
        return any(tok in seg for tok in _TOKENS)

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        text = _load_raw(url)
        data = _load_data(text)
        fmt = sniff_format(data)
        if fmt is None:
            raise InvalidInputError(
                f"{url} routed to api_spec but is not a recognizable "
                "OpenAPI/Swagger spec or Postman collection — point it at "
                "the raw spec file, a .json/.yaml URL or a local path."
            )
        title, slug = _title_slug(data, fmt, url)
        pages = (
            _openapi_render.count_operations(data)
            if fmt == "openapi"
            else _postman_render.count_requests(data)
        )
        if pages == 0:
            log.warning("api_spec.no_operations", url=url, fmt=fmt, slug=slug)

        raw_dir = workdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        ext = ".json" if text.lstrip()[:1] in "{[" else ".yaml"
        (raw_dir / f"spec{ext}").write_text(text, encoding="utf-8")

        log.info("api_spec.acquire", url=url, fmt=fmt, slug=slug, pages=pages, title=title)
        return AcquireResult(raw_dir=raw_dir, kind="markdown", slug=slug, pages=pages, title=title)

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        raw = next(iter(sorted(acq.raw_dir.glob("spec.*"))))
        data = _load_data(raw.read_text(encoding="utf-8"))
        title = acq.title or acq.slug
        fmt = sniff_format(data)
        if fmt == "postman":
            md = _postman_render.render(data, title)
        elif fmt == "openapi":
            md = _openapi_render.render(data, title)
        else:  # acquire validated this, but never silently mis-render if it didn't
            raise InvalidInputError(f"raw spec at {raw} is no longer a recognizable spec")
        out = workdir / f"{acq.slug}.md"
        out.write_text(md, encoding="utf-8")
        log.info("api_spec.normalize", slug=acq.slug, out=str(out))
        return out
