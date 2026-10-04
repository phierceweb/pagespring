"""Pattern registry: ``classify`` returns the first pattern whose ``match`` accepts a URL. Order:
host-specific, then extension and content, then gitbook, then ``docs_probe`` last."""

from __future__ import annotations

from pagespring.base import Pattern
from pagespring.patterns.adobe_helpx import AdobeHelpxPattern
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
from pagespring.patterns.sitemap_crawl import SitemapCrawlPattern
from pagespring.patterns.zendesk_help import ZendeskHelpPattern

PATTERNS: list[Pattern] = [
    AppleHelpPattern(),
    LlmsTxtPattern(),
    ReadTheDocsPattern(),
    GitHubMarkdownPattern(),
    ZendeskHelpPattern(),
    MicrosoftSupportPattern(),
    AdobeHelpxPattern(),
    OpenStaxPattern(),
    ApiSpecPattern(),
    PdfUrlPattern(),
    ArchiveDownloadPattern(),
    SitemapCrawlPattern(),
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
