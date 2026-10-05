"""Whether a URL names a file a single-file pattern owns, so a host-specific pattern declines it."""

from __future__ import annotations

from urllib.parse import urlparse

from pagespring.patterns.archive_download import ArchiveDownloadPattern
from pagespring.patterns.pdf_url import PdfUrlPattern

# Suffixes only: api_spec's name tokens would also claim a docs page like openapi.html.
_SPEC_SUFFIXES = (".json", ".yaml", ".yml")


def owned_by_file_pattern(url: str) -> bool:
    path = urlparse(url).path.lower()
    return (
        path.endswith(_SPEC_SUFFIXES)
        or ArchiveDownloadPattern().match(url)
        or PdfUrlPattern().match(url)
    )
