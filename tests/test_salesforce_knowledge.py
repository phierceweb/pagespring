"""salesforce_knowledge — match, topic and article acquire through the guest Aura API, normalize
(mocked http)."""

from __future__ import annotations

import json

import pytest
from pf_core.exceptions import ClientError
from test_aura import shell

from pagespring import http
from pagespring.patterns import salesforce_knowledge
from pagespring.patterns.salesforce_knowledge import SalesforceKnowledgePattern
from pagespring.registry import classify

ORIGIN = "https://community.acme.example"
TOPIC_URL = f"{ORIGIN}/s/topic/0TO3b000000gYW1GAM/acme-player?language=en_US"
ARTICLE_URL = f"{ORIGIN}/s/article/Player-Batch-Exporter?language=en_US"


def _record(record_id: str, obj: str, fields: dict[str, str | None]) -> dict:
    return {record_id: {obj: {"record": {"fields": {k: {"value": v} for k, v in fields.items()}}}}}


class FakeSite:
    """Serves the shell on GET and answers Aura actions by descriptor."""

    def __init__(self, articles: dict[str, dict], topic_name: str = "Acme Player") -> None:
        self.articles = articles  # id -> {"urlName", "Title", **body fields}
        self.topic_name = topic_name
        self.listed: list[tuple[int, int]] = []
        self.fail: set[str] = set()

    def fetch_text(self, url, **kw):
        return url, shell()

    def post_form(self, url, fields, **kw):
        action = json.loads(fields["message"])["actions"][0]
        name, params = action["descriptor"].rsplit("$", 1)[1], action["params"]
        records: dict = {}
        value = None
        if name == "loadMoreArticles":
            self.listed.append((params["offset"], params["limit"]))
            rows = [
                {"article": {"id": aid, "title": a["Title"], "urlName": a["urlName"]}}
                for aid, a in self.articles.items()
            ]
            value = rows[params["offset"] : params["offset"] + params["limit"]]
        elif name == "getArticleVersionId":
            value = next(i for i, a in self.articles.items() if a["urlName"] == params["urlName"])
        elif name == "getRecord":
            rid = params["recordDescriptor"].split(".")[0]
            if rid in self.fail:
                raise ClientError("upstream 500")
            if rid.startswith("0TO"):
                records = _record(rid, "Topic", {"Name": self.topic_name})
            else:
                records = _record(rid, "Knowledge__kav", self.articles[rid])
        body = {
            "actions": [{"state": "SUCCESS", "returnValue": value}],
            "context": {
                "globalValueProviders": [{"type": "$Record", "values": {"records": records}}]
            },
        }
        return url, json.dumps(body)


@pytest.fixture()
def site(monkeypatch):
    def install(articles, **kw):
        fake = FakeSite(articles, **kw)
        monkeypatch.setattr(http, "fetch_text", fake.fetch_text)
        monkeypatch.setattr(http, "post_form", fake.post_form)
        monkeypatch.setattr(http, "polite_sleep", lambda *a: None)
        return fake

    return install


def _staged(acq) -> str:
    return "".join(p.read_text(encoding="utf-8") for p in sorted(acq.raw_dir.glob("*.html")))


def _block(url_name: str, title: str, body: str) -> dict:
    return {"urlName": url_name, "Title": title, "Summary": "s", "block_Message__c": body}


class TestMatch:
    @pytest.mark.parametrize("url", [TOPIC_URL, ARTICLE_URL, f"{ORIGIN}/help/s/article/X"])
    def test_claims_topic_and_article_urls(self, url):
        assert SalesforceKnowledgePattern().match(url)
        assert classify(url).name == "salesforce_knowledge"

    @pytest.mark.parametrize(
        ("url", "owner"),
        [
            (f"{ORIGIN}/s/article/manual.pdf", "pdf_url"),
            (f"{ORIGIN}/files/s/article/guide.zip", "archive_download"),
            (f"{ORIGIN}/s/article/spec.yaml", "api_spec"),
        ],
    )
    def test_leaves_a_file_url_to_the_pattern_that_owns_it(self, url, owner):
        assert not SalesforceKnowledgePattern().match(url)
        assert classify(url).name == owner

    @pytest.mark.parametrize(
        "url",
        [f"{ORIGIN}/s/topic/not-an-id", f"{ORIGIN}/s/", "https://docs.acme.example/article/x"],
    )
    def test_declines_other_paths(self, url):
        assert not SalesforceKnowledgePattern().match(url)


class TestTopic:
    def test_every_article_is_staged_in_title_order(self, site, tmp_path):
        site(
            {
                "ka01": _block("Zoom", "Player: Zoom", "<p>zoom body</p>"),
                "ka02": _block("Batch", "Player: Batch Exporter", "<p>batch body</p>"),
            }
        )
        acq = SalesforceKnowledgePattern().acquire(TOPIC_URL, tmp_path)
        text = _staged(acq)
        assert text.index("Player: Batch Exporter") < text.index("Player: Zoom")
        assert "<p>batch body</p>" in text and "<p>zoom body</p>" in text
        assert f"<!-- source: {ORIGIN}/s/article/Batch?language=en_US -->" in text
        assert (acq.pages, acq.lost, acq.truncated, acq.single_document) == (2, 0, False, False)
        assert acq.title == "Acme Player Help"

    def test_listing_pages_until_a_short_page(self, site, tmp_path, monkeypatch):
        monkeypatch.setattr(salesforce_knowledge, "_PAGE_SIZE", 2)
        fake = site({f"ka0{i}": _block(f"A{i}", f"T{i}", "<p>x</p>") for i in range(5)})
        acq = SalesforceKnowledgePattern().acquire(TOPIC_URL, tmp_path)
        assert fake.listed == [(0, 2), (2, 2), (4, 2)]
        assert acq.pages == 5

    def test_the_article_cap_truncates(self, site, tmp_path, monkeypatch):
        monkeypatch.setattr(salesforce_knowledge, "_PAGE_SIZE", 2)
        monkeypatch.setattr(salesforce_knowledge, "_MAX_ARTICLES", 3)
        site({f"ka0{i}": _block(f"A{i}", f"T{i}", "<p>x</p>") for i in range(5)})
        acq = SalesforceKnowledgePattern().acquire(TOPIC_URL, tmp_path)
        assert (acq.pages, acq.truncated) == (3, True)

    def test_slug_is_vendor_plus_topic_without_repeating_the_vendor(self, site, tmp_path):
        site({"ka01": _block("A", "A", "<p>x</p>")})
        player = SalesforceKnowledgePattern().acquire(TOPIC_URL, tmp_path / "a")
        quik = SalesforceKnowledgePattern().acquire(
            f"{ORIGIN}/s/topic/0TO3b000000gYW0GAM/quik", tmp_path / "b"
        )
        assert (player.slug, quik.slug) == ("acme-player", "acme-quik")


class TestArticleBody:
    def test_framing_field_comes_before_its_answer(self, site, tmp_path):
        site(
            {
                "ka01": {
                    "urlName": "Crash",
                    "Title": "Crash",
                    "Standard_How_to_fix_it__c": "<p>FIX</p>",
                    "Standard_What_s_the_issue__c": "ISSUE",
                },
                "ka02": {
                    "urlName": "Faq",
                    "Title": "Faq",
                    "question_answer_Answer__c": "<p>ANSWER</p>",
                    "question_answer_Question__c": "<p>QUESTION</p>",
                },
            }
        )
        text = _staged(SalesforceKnowledgePattern().acquire(TOPIC_URL, tmp_path))
        assert text.index("ISSUE") < text.index("FIX")
        assert text.index("QUESTION") < text.index("ANSWER")

    def test_body_headings_nest_under_the_article_title(self, site, tmp_path):
        site({"ka01": _block("A", "Bones", "<h2>Specs</h2><p>x</p><h3>Weight</h3><p>y</p>")})
        text = _staged(SalesforceKnowledgePattern().acquire(TOPIC_URL, tmp_path))
        assert "<h2>Bones</h2>" in text
        assert "<h3>Specs</h3>" in text and "<h4>Weight</h4>" in text

    def test_summary_and_system_fields_are_not_body(self, site, tmp_path):
        site({"ka01": _block("A", "Title A", "<p>body</p>") | {"Summary": "SUMMARY"}})
        assert "SUMMARY" not in _staged(SalesforceKnowledgePattern().acquire(TOPIC_URL, tmp_path))

    def test_scripts_go_and_images_resolve_against_the_site(self, site, tmp_path):
        body = '<p>see</p><img src="/servlet/rtaImage?eid=ka0&amp;refid=0EM1"><script>x()</script>'
        site({"ka01": _block("A", "A", body)})
        text = _staged(SalesforceKnowledgePattern().acquire(TOPIC_URL, tmp_path))
        assert "<script" not in text
        assert f'src="{ORIGIN}/servlet/rtaImage?eid=ka0&amp;refid=0EM1"' in text

    def test_an_article_with_no_body_is_lost_not_staged(self, site, tmp_path):
        site({"ka01": _block("A", "Empty", ""), "ka02": _block("B", "Full", "<p>x</p>")})
        acq = SalesforceKnowledgePattern().acquire(TOPIC_URL, tmp_path)
        assert (acq.pages, acq.lost) == (1, 1)
        assert "Empty" not in _staged(acq)

    def test_a_failed_record_fetch_is_lost_and_the_rest_continue(self, site, tmp_path):
        fake = site({"ka01": _block("A", "A", "<p>a</p>"), "ka02": _block("B", "B", "<p>b</p>")})
        fake.fail.add("ka01")
        acq = SalesforceKnowledgePattern().acquire(TOPIC_URL, tmp_path)
        assert (acq.pages, acq.lost) == (1, 1)


class TestSingleArticle:
    def test_an_article_url_stages_that_article(self, site, tmp_path):
        site(
            {
                "ka01": _block("Other", "Other", "<p>other</p>"),
                "ka02": _block("Player-Batch-Exporter", "Batch Exporter", "<p>batch</p>"),
            }
        )
        acq = SalesforceKnowledgePattern().acquire(ARTICLE_URL, tmp_path)
        text = _staged(acq)
        assert "<p>batch</p>" in text and "other" not in text
        assert (acq.pages, acq.single_document, acq.title) == (1, True, "Batch Exporter")
        assert acq.slug == "acme-player-batch-exporter"


def test_normalize_writes_one_titled_document(site, tmp_path):
    site({"ka01": _block("A", "Alpha", "<p>alpha</p>"), "ka02": _block("B", "Beta", "<p>b</p>")})
    pattern = SalesforceKnowledgePattern()
    out = pattern.normalize(pattern.acquire(TOPIC_URL, tmp_path), tmp_path)
    doc = out.read_text(encoding="utf-8")
    assert out.name == "acme-player.html"
    assert "<title>Acme Player Help</title>" in doc and "<h1>Acme Player Help</h1>" in doc
    assert doc.index("<h2>Alpha</h2>") < doc.index("<h2>Beta</h2>")
