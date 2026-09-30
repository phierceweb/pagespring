"""ingest --batch: one ingest per URL line of a file, each line's failure isolated
(mocked run_ingest and patterns; no network)."""

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import batch, http, orchestrate
from pagespring.base import AcquireResult
from pagespring.orchestrate import AcquireError, EmptyOutputError, NoPatternError


@pytest.fixture(autouse=True)
def _incoming_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate.cfg, "INCOMING_DIR", str(tmp_path / "incoming"))


@pytest.fixture
def paces(monkeypatch):
    calls: list[None] = []
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: calls.append(None))
    return calls


def _result(url, **overrides):
    return {
        "pattern": "fake",
        "slug": url.rstrip("/").rsplit("/", 1)[-1],
        "kind": "html",
        "clean": "/x",
        "pages": 3,
        "bytes": 2048,
        "images": 0,
        "images_downloaded": 0,
        "changed": True,
        "duplicate_of": None,
        **overrides,
    }


def _batch_file(tmp_path, text):
    path = tmp_path / "urls.txt"
    path.write_text(text, encoding="utf-8")
    return path


def test_read_batch_skips_blank_and_comment_lines_and_keeps_line_numbers(tmp_path):
    path = _batch_file(
        tmp_path,
        "# manuals to stage\n\nhttps://a.example/docs\n   \n  https://b.example/guide  \n"
        "  # https://c.example/skipped\n/tmp/local.pdf\n",
    )

    assert batch.read_batch(path) == [
        (3, "https://a.example/docs"),
        (5, "https://b.example/guide"),
        (7, "/tmp/local.pdf"),
    ]


def test_read_batch_of_a_file_without_urls_is_refused(tmp_path):
    path = _batch_file(tmp_path, "# nothing yet\n\n   \n")

    with pytest.raises(InvalidInputError, match="no URLs"):
        batch.read_batch(path)


@pytest.mark.parametrize("make", ["missing", "directory", "not_utf8"])
def test_read_batch_of_an_unreadable_file_is_refused(tmp_path, make):
    path = tmp_path / "urls.txt"
    if make == "directory":
        path.mkdir()
    elif make == "not_utf8":
        path.write_bytes(b"https://a.example/\xff\xfe\n")

    with pytest.raises(InvalidInputError, match="urls.txt"):
        batch.read_batch(path)


def test_every_line_is_ingested_with_the_batch_options_and_its_own_slug(monkeypatch, paces):
    calls = []

    def fake_run_ingest(url, **kwargs):
        calls.append((url, kwargs))
        return _result(url)

    monkeypatch.setattr(batch, "run_ingest", fake_run_ingest)

    outcomes = list(
        batch.ingest_batch(
            [(1, "https://a.example/docs"), (4, "https://b.example/guide")],
            keep_raw=True,
            download_images=True,
            if_changed=True,
            replace=True,
        )
    )

    options = {
        "keep_raw": True,
        "download_images": True,
        "if_changed": True,
        "replace": True,
        "slug_override": None,
    }
    assert calls == [
        ("https://a.example/docs", {**options, "protected_slugs": frozenset()}),
        ("https://b.example/guide", {**options, "protected_slugs": frozenset({"docs"})}),
    ]
    assert [(o["line"], o["status"], o["result"]["slug"]) for o in outcomes] == [
        (1, "staged", "docs"),
        (4, "staged", "guide"),
    ]


def test_a_current_deliverable_under_if_changed_reports_unchanged(monkeypatch, paces):
    monkeypatch.setattr(batch, "run_ingest", lambda url, **kw: _result(url, changed=False))

    (outcome,) = batch.ingest_batch([(1, "https://a.example/docs")], if_changed=True)

    assert outcome["status"] == "unchanged"


@pytest.mark.parametrize(
    ("error", "detail"),
    [
        (AcquireError("https://dead.example", "HTTP Error 404: Not Found"), "HTTP Error 404"),
        (NoPatternError("https://dead.example"), "no pattern matched"),
        (EmptyOutputError("https://dead.example"), "empty"),
        (InvalidInputError("incoming/docs/ holds a different source"), "different source"),
        (RuntimeError("parser blew up"), "RuntimeError: parser blew up"),
    ],
)
def test_a_failed_line_is_reported_and_the_batch_carries_on(monkeypatch, paces, error, detail):
    def fake_run_ingest(url, **kwargs):
        if "dead" in url:
            raise error
        return _result(url)

    monkeypatch.setattr(batch, "run_ingest", fake_run_ingest)

    outcomes = list(
        batch.ingest_batch([(1, "https://dead.example"), (2, "https://a.example/docs")])
    )

    assert [o["status"] for o in outcomes] == ["failed", "staged"]
    assert detail.lower() in outcomes[0]["detail"].lower()
    assert outcomes[0]["result"] is None


@pytest.mark.parametrize("stop", [KeyboardInterrupt, SystemExit])
def test_a_stop_ends_the_batch_instead_of_failing_one_line(monkeypatch, paces, stop):
    calls = []

    def fake_run_ingest(url, **kwargs):
        calls.append(url)
        raise stop

    monkeypatch.setattr(batch, "run_ingest", fake_run_ingest)

    with pytest.raises(stop):
        list(batch.ingest_batch([(1, "https://a.example/"), (2, "https://b.example/")]))
    assert calls == ["https://a.example/"]


def test_a_repeated_url_is_skipped_not_crawled_twice(monkeypatch, paces):
    calls = []

    def fake_run_ingest(url, **kwargs):
        calls.append(url)
        return _result(url)

    monkeypatch.setattr(batch, "run_ingest", fake_run_ingest)

    outcomes = list(
        batch.ingest_batch(
            [
                (1, "https://a.example/docs"),
                (2, "https://b.example/x"),
                (5, "https://a.example/docs"),
            ]
        )
    )

    assert calls == ["https://a.example/docs", "https://b.example/x"]
    assert (outcomes[2]["status"], outcomes[2]["detail"]) == ("skipped", "repeats line 1")


def test_another_spelling_of_a_source_is_skipped_as_a_repeat(tmp_path, monkeypatch, paces):
    calls = []

    def fake_run_ingest(url, **kwargs):
        calls.append(url)
        return _result(url)

    monkeypatch.setattr(batch, "run_ingest", fake_run_ingest)
    (tmp_path / "a").mkdir()
    archive = tmp_path / "a" / "docs.zip"
    archive.write_bytes(b"PK")
    monkeypatch.chdir(tmp_path)

    outcomes = list(
        batch.ingest_batch(
            [
                (1, "https://a.example/docs"),
                (2, "https://a.example/docs/"),
                (3, "https://WWW.a.example/docs#intro"),
                (4, str(archive)),
                (5, "a/../a/docs.zip"),
            ]
        )
    )

    assert calls == ["https://a.example/docs", str(archive)]
    assert [(o["status"], o["detail"]) for o in outcomes] == [
        ("staged", ""),
        ("skipped", "repeats line 1"),
        ("skipped", "repeats line 1"),
        ("staged", ""),
        ("skipped", "repeats line 4"),
    ]


def test_consecutive_ingests_are_paced(monkeypatch, paces):
    monkeypatch.setattr(batch, "run_ingest", lambda url, **kw: _result(url))

    list(batch.ingest_batch([(1, "https://a.example/"), (2, "https://a.example/b")]))

    assert len(paces) == 1


class _Pattern:
    name = "fake"

    def match(self, url):
        return True

    def acquire(self, url, workdir):
        if "dead" in url:
            raise ConnectionRefusedError("connection refused")
        raw = workdir / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "page.html").write_text(f"<h1>{url}</h1>", encoding="utf-8")
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        return AcquireResult(raw_dir=raw, kind="html", slug=slug, pages=1)

    def normalize(self, acq, workdir):
        clean = workdir / f"{acq.slug}.html"
        clean.write_text((acq.raw_dir / "page.html").read_text(encoding="utf-8"), encoding="utf-8")
        return clean


def test_a_batch_stages_through_the_real_ingest(tmp_path, monkeypatch, paces):
    monkeypatch.setattr(orchestrate, "classify", lambda url: _Pattern())

    outcomes = list(
        batch.ingest_batch(
            [
                (1, "https://a.example/alpha"),
                (2, "https://dead.example/x"),
                (3, "https://a.example/beta"),
            ]
        )
    )

    assert [o["status"] for o in outcomes] == ["staged", "failed", "staged"]
    assert "connection refused" in outcomes[1]["detail"]
    incoming = tmp_path / "incoming"
    assert sorted(p.name for p in incoming.iterdir()) == ["alpha", "beta"]
    assert (incoming / "beta" / "beta.html").read_text(encoding="utf-8") == (
        "<h1>https://a.example/beta</h1>"
    )


@pytest.mark.parametrize("replace", [False, True])
def test_a_line_never_takes_over_a_slug_an_earlier_line_staged(
    tmp_path, monkeypatch, paces, replace
):
    """Two sources can derive one slug (docs.foo.com and help.foo.com are both "foo");
    --replace accepts displacing an older manual, not the one this batch just staged."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _Pattern())

    outcomes = list(
        batch.ingest_batch(
            [(1, "https://docs.foo.example/manual"), (2, "https://help.foo.example/manual")],
            replace=replace,
        )
    )

    assert [o["status"] for o in outcomes] == ["staged", "failed"]
    assert "earlier in this batch" in outcomes[1]["detail"]
    assert (tmp_path / "incoming" / "manual" / "manual.html").read_text(encoding="utf-8") == (
        "<h1>https://docs.foo.example/manual</h1>"
    )


def test_read_batch_ignores_a_byte_order_mark(tmp_path):
    path = tmp_path / "urls.txt"
    path.write_bytes("﻿# my manuals\nhttps://example.com/a.pdf\n".encode())

    assert batch.read_batch(path) == [batch.BatchLine(2, "https://example.com/a.pdf")]
