"""salesforce_knowledge: Knowledge articles on a Salesforce Experience Cloud help site, through the
guest Aura API its pages load them with — a topic's articles in title order, or one article."""

from __future__ import annotations

import html as _html
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from pf_core.log import get_logger
from pf_core.utils.slugify import slugify
from pf_core.utils.url_parse import domain_of

from pagespring import http
from pagespring.base import AcquireResult
from pagespring.patterns import _aura
from pagespring.patterns._file_url import owned_by_file_pattern
from pagespring.patterns._site import (
    absolutize_refs,
    flatten_responsive_images,
    seat_headings,
    strip_scripts,
)

log = get_logger(__name__)

_PAGE_SIZE = 100  # the most one topic listing call returns
_MAX_ARTICLES = 2000  # safety cap

_ARTICLE_RE = re.compile(r"/s/article/([^/?#]+)")
_TOPIC_RE = re.compile(r"/s/topic/(0TO[A-Za-z0-9]{12,15})(?:/([^/?#]+))?")
_VENDOR_PREFIXES = ("www.", "community.", "support.", "help.")

_LIST = (
    "serviceComponent://ui.self.service.components.controller."
    "TopicArticleListDataProviderController/ACTION$loadMoreArticles"
)
_VERSION_ID = (
    "serviceComponent://ui.comm.runtime.components.aura.components.siteforce."
    "recordservicecomponent.RecordServiceComponentController/ACTION$getArticleVersionId"
)
_RECORD = (
    "serviceComponent://ui.force.components.controllers.recordGlobalValueProvider."
    "RecordGvpController/ACTION$getRecord"
)
_TOPIC_NAME_FIELDS = ".undefined.null.null.null.Name.VIEW.false.null.null.null"
_ARTICLE_FIELDS = ".undefined.FULL.null.null.Summary.VIEW.true.null.null.null"
# The record JSON lists fields alphabetically, which puts an answer before its question.
_FRAMING_RE = re.compile(r"question|issue|problem|symptom", re.IGNORECASE)


def _vendor(url: str) -> str:
    host = domain_of(url)
    for prefix in _VENDOR_PREFIXES:
        host = host.removeprefix(prefix)
    return slugify(host.split(".")[0]) or "help"


def _body_html(fields: dict[str, Any]) -> str:
    """The article's custom rich-text fields, a question or issue ahead of its answer."""
    custom = sorted(name for name in fields if name.endswith("__c"))
    # Fields carry their record type as a prefix (question_answer_Answer__c), so match past it.
    shared = os.path.commonprefix(custom) if len(custom) > 1 else ""
    shared = shared[: shared.rfind("_") + 1]
    body = [n for n in custom if isinstance(fields[n], str) and fields[n].strip()]
    body.sort(key=lambda name: (not _FRAMING_RE.search(name[len(shared) :]), name))
    return "\n".join(fields[name] for name in body)


def _clean(body: str, page_url: str) -> str:
    soup = BeautifulSoup(body, "html.parser")
    strip_scripts(soup)
    seat_headings(soup, depth=2, name="")
    flatten_responsive_images(soup)
    absolutize_refs(soup, page_url)
    return str(soup)


class SalesforceKnowledgePattern:
    name = "salesforce_knowledge"

    def match(self, url: str) -> bool:
        path = urlparse(url).path
        if owned_by_file_pattern(url):
            return False
        return bool(_ARTICLE_RE.search(path) or _TOPIC_RE.search(path))

    def _topic_articles(
        self, site: _aura.AuraSite, topic_id: str, page_uri: str
    ) -> tuple[list[dict[str, Any]], bool]:
        rows: list[dict[str, Any]] = []
        while len(rows) < _MAX_ARTICLES:
            params = {"limit": _PAGE_SIZE, "offset": len(rows), "topicIds": topic_id}
            page = _aura.return_value(_aura.call(site, _LIST, params, page_uri=page_uri)) or []
            rows.extend(row["article"] for row in page)
            http.polite_sleep()
            if len(page) < _PAGE_SIZE:
                return rows, False
        log.warning("salesforce_knowledge.capped", topic=topic_id, cap=_MAX_ARTICLES)
        return rows[:_MAX_ARTICLES], True

    def acquire(self, url: str, workdir: Path) -> AcquireResult:
        final, shell = http.fetch_text(url)
        site = _aura.site_from_shell(final, shell)
        p = urlparse(final)
        page_uri = p.path + (f"?{p.query}" if p.query else "")
        language = parse_qs(p.query).get("language", ["en_US"])[0]
        origin = f"{p.scheme}://{p.netloc}{p.path.split('/s/', 1)[0]}"
        vendor = _vendor(final)

        topic = _TOPIC_RE.search(p.path)
        truncated = False
        if topic:
            articles, truncated = self._topic_articles(site, topic.group(1), page_uri)
            name_resp = _aura.call(
                site,
                _RECORD,
                {"recordDescriptor": f"{topic.group(1)}{_TOPIC_NAME_FIELDS}"},
                page_uri=page_uri,
            )
            topic_name = _aura.record_fields(name_resp, topic.group(1)).get("Name") or ""
            title: str | None = f"{topic_name} Help" if topic_name else None
            segment = slugify(topic.group(2) or topic_name) or topic.group(1).lower()
            slug = segment if segment.startswith(f"{vendor}-") else f"{vendor}-{segment}"
        else:
            url_name = _ARTICLE_RE.search(p.path).group(1)  # type: ignore[union-attr]
            params = {"urlName": url_name, "language": language}
            article_id = _aura.return_value(
                _aura.call(site, _VERSION_ID, params, page_uri=page_uri)
            )
            articles = [{"id": article_id, "urlName": url_name, "title": ""}]
            title = None
            slug = slugify(f"{vendor}-{url_name}")
        articles.sort(key=lambda a: (str(a.get("title", "")).casefold(), a.get("urlName", "")))

        raw_dir = workdir / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        saved = lost = 0
        for art in articles:
            article_id, url_name = art["id"], art["urlName"]
            src = f"{origin}/s/article/{url_name}?language={language}"
            descriptor = f"{article_id}{_ARTICLE_FIELDS}"
            try:
                resp = _aura.call(
                    site, _RECORD, {"recordDescriptor": descriptor}, page_uri=page_uri
                )
            except Exception as exc:
                lost += 1
                log.warning("salesforce_knowledge.fetch_error", article=url_name, error=str(exc))
                continue
            finally:
                http.polite_sleep()
            fields = _aura.record_fields(resp, article_id)
            body = _body_html(fields)
            if not body:
                lost += 1
                log.warning("salesforce_knowledge.empty_article", article=url_name)
                continue
            heading = str(fields.get("Title") or art.get("title") or url_name)
            if not topic:
                title = heading
            (raw_dir / f"{saved:04d}.html").write_text(
                f"<!-- source: {src} -->\n<section>\n<h2>{_html.escape(heading)}</h2>\n"
                f"{_clean(body, src)}\n</section>\n",
                encoding="utf-8",
            )
            saved += 1

        log.info(
            "salesforce_knowledge.acquire",
            endpoint=site.endpoint,
            articles=saved,
            lost=lost,
            slug=slug,
            truncated=truncated,
        )
        return AcquireResult(
            raw_dir=raw_dir,
            kind="html",
            slug=slug,
            pages=saved,
            title=title,
            truncated=truncated,
            single_document=not topic,
            lost=lost,
        )

    def normalize(self, acq: AcquireResult, workdir: Path) -> Path:
        title = acq.title or f"{acq.slug.replace('-', ' ').title()} Help"
        parts = [p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html"))]
        doc = (
            '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
            f"<title>{_html.escape(title)}</title></head>\n<body>\n"
            f"<h1>{_html.escape(title)}</h1>\n" + "\n".join(parts) + "\n</body></html>\n"
        )
        out = workdir / f"{acq.slug}.html"
        out.write_text(doc, encoding="utf-8")
        log.info("salesforce_knowledge.normalize", slug=acq.slug, out=str(out), articles=len(parts))
        return out
