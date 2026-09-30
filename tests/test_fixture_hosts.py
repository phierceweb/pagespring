"""Fixture hosts: tests name reserved hosts, not the sites that were ingested.

The sdist ships ``tests/``, so a real vendor host or product name in a fixture
publishes what was ingested. A host must be reserved (RFC 2606/6761) or listed
below for a stated reason.
"""

from __future__ import annotations

import re
from pathlib import Path

_RESERVED_TLDS = {"example", "test", "invalid", "localhost"}
_RESERVED_DOMAINS = {"example.com", "example.org", "example.net"}

# Hosts a pattern routes or detects by name, the CDNs and embeds a page shell
# carries, and the standards bodies whose namespace URIs fixtures quote.
_PUBLIC = {
    "adobe.com", "apple.com", "aspose.com", "buildwithfern.com", "claude.com",
    "docker.com", "getpostman.com", "gitbook.io", "github.com", "github.io",
    "githubusercontent.com", "gitlab.com", "gutenberg.org", "idpf.org", "jsdelivr.net",
    "microsoft.com", "mkdocs.org", "office.net", "openstax.org", "purl.org", "python.org",
    "readthedocs.io", "redoc.ly", "schema.de", "sitemaps.org", "standardebooks.org",
    "swagger.io", "tableplus.com", "tiktok.com", "twitter.com", "w3.org", "wikipedia.org",
    "youtube.com", "zendesk.com",
}  # fmt: skip
# Made-up names already in the suite. A new fixture uses ``.example``.
_PLACEHOLDERS = {
    "a.com", "acme.com", "b.com", "elsewhere.com", "ex.com", "ex.dev", "ex.io", "ex.org",
    "foo.com", "not-actually-gitbook.com", "other.com", "v.com", "vendor.com",
    "widgetpro.com", "x.com", "y.com",
}  # fmt: skip
_LISTED = _PUBLIC | _PLACEHOLDERS

_HOST_RE = re.compile(r"https?://([A-Za-z0-9.-]+)")


def _hosts() -> dict[str, set[str]]:
    """Each dotted, non-numeric host a test file names, with the files naming it."""
    found: dict[str, set[str]] = {}
    for path in sorted(Path(__file__).parent.glob("*.py")):
        for host in _HOST_RE.findall(path.read_text(encoding="utf-8")):
            host = host.lower().strip(".-")
            if "." in host and not host.replace(".", "").isdigit():
                found.setdefault(host, set()).add(path.name)
    return found


def _domain(host: str) -> str:
    return ".".join(host.split(".")[-2:])


def _reserved(host: str) -> bool:
    return host.rsplit(".", 1)[-1] in _RESERVED_TLDS or _domain(host) in _RESERVED_DOMAINS


def test_fixture_hosts_are_reserved_or_listed():
    unlisted = {
        host: sorted(files)
        for host, files in _hosts().items()
        if not _reserved(host) and _domain(host) not in _LISTED
    }
    assert not unlisted, f"use a .example host, or list the domain with a reason: {unlisted}"


def test_every_listed_domain_is_still_named():
    named = {_domain(host) for host in _hosts()}
    assert not _LISTED - named, f"listed but no test names it: {sorted(_LISTED - named)}"
