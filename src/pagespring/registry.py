"""Pattern registry + URL classification.

``classify(url)`` walks PATTERNS in order and returns the first whose ``match``
accepts the URL, or None when no pattern recognizes the source (only reachable
for a non-web argument — a local file path or ``file://`` URL — since
``docs_probe`` claims every remaining http(s) URL).

Order matters — first match wins:
  - host-specific patterns first (apple_help, llms_txt, readthedocs, github_markdown),
  - then extension/content patterns (api_spec, pdf_url, archive_download) so a
    `.json`/`.yaml` spec or `.pdf` on a `docs.*` host routes correctly rather
    than falling through to the broader patterns below; api_spec also claims
    URLs whose last segment contains an ``openapi``/``swagger``/``postman``
    token,
  - gitbook next, narrowed to `*.gitbook.io` (its own hosting, not custom domains),
  - docs_probe LAST — a content-probing catch-all that claims any remaining
    http(s) URL and sniffs the generator (MkDocs/Docusaurus/Sphinx/GitBook-via-
    llms.txt) at acquire time. It must stay last: everything above it is a
    cheaper, more specific match.
"""

from __future__ import annotations

from pagespring.base import Pattern
from pagespring.patterns.api_spec import ApiSpecPattern
from pagespring.patterns.apple_help import AppleHelpPattern
from pagespring.patterns.archive_download import ArchiveDownloadPattern
from pagespring.patterns.docs_probe import DocsProbePattern
from pagespring.patterns.gitbook import GitBookPattern
from pagespring.patterns.github_markdown import GitHubMarkdownPattern
from pagespring.patterns.llms_txt import LlmsTxtPattern
from pagespring.patterns.microsoft_support import MicrosoftSupportPattern
from pagespring.patterns.openstax import OpenStaxPattern
from pagespring.patterns.pdf_url import PdfUrlPattern
from pagespring.patterns.readthedocs import ReadTheDocsPattern
from pagespring.patterns.zendesk_help import ZendeskHelpPattern

PATTERNS: list[Pattern] = [
    AppleHelpPattern(),
    LlmsTxtPattern(),
    ReadTheDocsPattern(),
    GitHubMarkdownPattern(),
    ZendeskHelpPattern(),
    MicrosoftSupportPattern(),
    OpenStaxPattern(),
    ApiSpecPattern(),
    PdfUrlPattern(),
    ArchiveDownloadPattern(),
    GitBookPattern(),
    DocsProbePattern(),
]


def classify(url: str) -> Pattern | None:
    """Return the first pattern that matches ``url``, or None."""
    for pattern in PATTERNS:
        if pattern.match(url):
            return pattern
    return None


def pattern_by_name(name: str) -> Pattern | None:
    """Return the registered pattern named ``name``, or None (renamed/removed)."""
    for pattern in PATTERNS:
        if pattern.name == name:
            return pattern
    return None
