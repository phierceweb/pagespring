"""Ingest orchestration (mocked pattern; no network).

The clean download stages to ``incoming/<slug>/``; an autouse fixture points
that at a tmp dir so tests never write into the real repo. The pagespring's job
ends at ``incoming/`` — conversion into ``manuals/`` is a separate concern it
neither runs nor knows about.
"""

import re
import urllib.error

import pytest
from pf_core.exceptions import PreconditionError

from pagespring import http, manifest, orchestrate
from pagespring.base import AcquireResult
from pagespring.patterns.docs_probe import DocsProbePattern


@pytest.fixture(autouse=True)
def _incoming_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate.cfg, "INCOMING_DIR", str(tmp_path / "incoming"))


class _FakePattern:
    name = "fake"
    convert_recipe = ["--split-sections"]

    def match(self, url):
        return True

    def acquire(self, url, workdir):
        raw = workdir / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "welcome.html").write_text("<html></html>", encoding="utf-8")
        return AcquireResult(raw_dir=raw, kind="html", slug="fakeapp", pages=1)

    def normalize(self, acq, workdir):
        clean = workdir / "fakeapp.html"
        clean.write_text("<h1>Fake</h1>", encoding="utf-8")
        return clean


def test_stages_clean_file_into_incoming(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    res = orchestrate.run_ingest("https://x")

    assert res["pattern"] == "fake"
    assert res["slug"] == "fakeapp"
    assert res["kind"] == "html"
    assert res["images"] == 0
    # The download lands in incoming/<slug>/ (NOT /tmp, NOT manuals/).
    staged = tmp_path / "incoming" / "fakeapp" / "fakeapp.html"
    assert staged.read_text(encoding="utf-8") == "<h1>Fake</h1>"
    assert res["clean"] == str(staged)


def test_keep_raw_copies_the_crawl(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://x", keep_raw=True)

    raw_copy = tmp_path / "incoming" / "fakeapp" / "raw" / "welcome.html"
    assert raw_copy.read_text(encoding="utf-8") == "<html></html>"


def test_no_pattern_raises(monkeypatch):
    monkeypatch.setattr(orchestrate, "classify", lambda url: None)
    with pytest.raises(orchestrate.NoPatternError):
        orchestrate.run_ingest("https://unknown.example/x")


def test_reingest_replaces_stale_artifacts(tmp_path, monkeypatch):
    """A re-run leaves only the fresh deliverable — no orphaned clean files,
    no stale raw/ or images/ from a previous ingest."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    slug_dir = tmp_path / "incoming" / "fakeapp"
    (slug_dir / "raw").mkdir(parents=True)
    (slug_dir / "images").mkdir()
    (slug_dir / "fakeapp-old-name.html").write_text("orphan", encoding="utf-8")
    (slug_dir / "raw" / "stale.html").write_text("stale", encoding="utf-8")
    (slug_dir / "images" / "old.png").write_bytes(b"png")

    orchestrate.run_ingest("https://x", keep_raw=True)

    assert not (slug_dir / "fakeapp-old-name.html").exists()
    assert not (slug_dir / "images").exists()
    assert not (slug_dir / "raw" / "stale.html").exists()  # raw/ is fresh, not merged
    assert (slug_dir / "raw" / "welcome.html").exists()
    assert (slug_dir / "fakeapp.html").read_text(encoding="utf-8") == "<h1>Fake</h1>"


def test_result_reports_pages_and_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    res = orchestrate.run_ingest("https://x")
    assert res["pages"] == 1
    assert res["bytes"] == len("<h1>Fake</h1>")


class _FetchFailPattern(_FakePattern):
    def acquire(self, url, workdir):
        raise urllib.error.HTTPError(url, 404, "Not Found", None, None)


def test_acquire_network_failure_wrapped(monkeypatch):
    """A fetch that dies during acquire surfaces as AcquireError, not a raw
    urllib traceback (the CLI turns it into a friendly message)."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FetchFailPattern())
    with pytest.raises(orchestrate.AcquireError):
        orchestrate.run_ingest("https://docs.not-actually-gitbook.com")


class _EmptyPattern(_FakePattern):
    def normalize(self, acq, workdir):
        clean = workdir / "fakeapp.html"
        clean.write_text("", encoding="utf-8")
        return clean


def test_empty_output_fails_and_preserves_previous(tmp_path, monkeypatch):
    """A crawl that normalizes to nothing hard-fails — and does NOT clobber the
    previous good deliverable in incoming/<slug>/."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _EmptyPattern())
    slug_dir = tmp_path / "incoming" / "fakeapp"
    slug_dir.mkdir(parents=True)
    (slug_dir / "fakeapp.html").write_text("previous good", encoding="utf-8")

    with pytest.raises(orchestrate.EmptyOutputError):
        orchestrate.run_ingest("https://x")

    assert (slug_dir / "fakeapp.html").read_text(encoding="utf-8") == "previous good"


class _ZeroFragmentHtmlPattern(_FakePattern):
    """A docs_probe-shaped pattern whose crawl yields zero html fragments —
    real normalize() must produce a 0-byte file, not a hollow <!DOCTYPE> shell."""

    def acquire(self, url, workdir):
        raw = workdir / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        return AcquireResult(raw_dir=raw, kind="html", slug="fakeapp", pages=0)

    def normalize(self, acq, workdir):
        return DocsProbePattern().normalize(acq, workdir)


def test_zero_fragment_html_crawl_fails_and_preserves_previous(tmp_path, monkeypatch):
    """A zero-page html crawl through the real docs_probe normalize must raise
    EmptyOutputError before staging — a hollow shell must not clobber a prior
    good deliverable (the same invariant as test_empty_output_fails_and_preserves_previous,
    exercised through the real html branch instead of a fake that writes "")."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _ZeroFragmentHtmlPattern())
    slug_dir = tmp_path / "incoming" / "fakeapp"
    slug_dir.mkdir(parents=True)
    (slug_dir / "fakeapp.html").write_text("previous good", encoding="utf-8")

    with pytest.raises(orchestrate.EmptyOutputError):
        orchestrate.run_ingest("https://x")

    assert (slug_dir / "fakeapp.html").read_text(encoding="utf-8") == "previous good"


def test_writes_manifest_beside_deliverable(tmp_path, monkeypatch):
    """Every ingest drops a manifest.json next to the clean file, carrying the
    provenance + the downstream convert_recipe + a hash of the deliverable."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    res = orchestrate.run_ingest("https://docs.example.com/foo")

    assert res["changed"] is True
    slug_dir = tmp_path / "incoming" / "fakeapp"
    m = manifest.read_manifest(slug_dir)
    assert m is not None
    assert m["source_url"] == "https://docs.example.com/foo"
    assert m["pattern"] == "fake"
    assert m["slug"] == "fakeapp"
    assert m["kind"] == "html"
    assert m["deliverable"] == "fakeapp.html"
    assert m["convert_recipe"] == ["--split-sections"]
    assert m["pages"] == 1
    assert m["bytes"] == len("<h1>Fake</h1>")
    assert m["images"] == 0
    assert m["schema_version"] == manifest.SCHEMA_VERSION
    assert m["pagespring_version"]
    # Default (no --download-images): the manifest hash IS the on-disk file's hash.
    assert m["sha256"] == manifest.sha256_file(slug_dir / "fakeapp.html")
    # ISO-8601 UTC, e.g. 2026-06-14T17:23:01Z
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", m["ingested_at"])


class _BodyPattern(_FakePattern):
    """A fake whose normalized content can change between ingests."""

    def __init__(self, body: str):
        self.body = body

    def normalize(self, acq, workdir):
        clean = workdir / "fakeapp.html"
        clean.write_text(self.body, encoding="utf-8")
        return clean


def test_if_changed_first_ingest_stages(tmp_path, monkeypatch):
    """No prior manifest → --if-changed has nothing to compare, so it stages."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    res = orchestrate.run_ingest("https://x", if_changed=True)
    assert res["changed"] is True
    assert (tmp_path / "incoming" / "fakeapp" / "fakeapp.html").exists()
    assert (tmp_path / "incoming" / "fakeapp" / "manifest.json").exists()


def test_if_changed_skips_restage_when_identical(tmp_path, monkeypatch):
    """A re-fetch with byte-identical content leaves the slug dir untouched —
    a planted sentinel survives (the replace path would have rmtree'd it)."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://x")
    slug_dir = tmp_path / "incoming" / "fakeapp"
    (slug_dir / "sentinel.txt").write_text("keep me", encoding="utf-8")

    res = orchestrate.run_ingest("https://x", if_changed=True)

    assert res["changed"] is False
    assert res["clean"] == str(slug_dir / "fakeapp.html")
    assert (slug_dir / "sentinel.txt").read_text(encoding="utf-8") == "keep me"


def test_if_changed_restages_when_content_differs(tmp_path, monkeypatch):
    """Changed content → full replace: new deliverable, sentinel wiped."""
    p = _BodyPattern("<h1>One</h1>")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://x")
    slug_dir = tmp_path / "incoming" / "fakeapp"
    (slug_dir / "sentinel.txt").write_text("keep me", encoding="utf-8")

    p.body = "<h1>Two</h1>"
    res = orchestrate.run_ingest("https://x", if_changed=True)

    assert res["changed"] is True
    assert (slug_dir / "fakeapp.html").read_text(encoding="utf-8") == "<h1>Two</h1>"
    assert not (slug_dir / "sentinel.txt").exists()


def _write_manifest(slug_dir, **over):
    slug_dir.mkdir(parents=True, exist_ok=True)
    fields = {
        "source_url": "https://openstax.org/books/bk",
        "pattern": "openstax",
        "slug": slug_dir.name,
        "kind": "html",
        "deliverable": "bk.html",
        "convert_recipe": ["--split-sections"],
        "pages": 1,
        "size_bytes": 10,
        "sha256": "x",
        "images": 0,
        "ingested_at": "2026-06-17T00:00:00Z",
    }
    fields.update(over)
    manifest.write_manifest(slug_dir, manifest.build_manifest(**fields))


def test_localize_images_localizes_and_updates_manifest(tmp_path, monkeypatch):
    """localize_images grabs a staged deliverable's remote images (no re-crawl),
    re-points refs, and writes the new image count back to the manifest."""
    slug_dir = tmp_path / "incoming" / "bk"
    _write_manifest(slug_dir)
    (slug_dir / "bk.html").write_text('<img src="https://x.com/a.png">', encoding="utf-8")
    monkeypatch.setattr(http, "fetch_bytes", lambda u, **k: (u, b"\x89PNG\r\n\x1a\nx"))
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    res = orchestrate.localize_images("bk")

    assert res["localized"] == 1
    assert res["remaining"] == 0
    assert res["images_total"] == 1
    assert 'src="images/a.png"' in (slug_dir / "bk.html").read_text(encoding="utf-8")
    assert manifest.read_manifest(slug_dir)["images"] == 1


def test_localize_images_without_manifest_raises(tmp_path):
    """A slug with no manifest (never ingested) is a precondition failure."""
    (tmp_path / "incoming" / "bk").mkdir(parents=True)
    with pytest.raises(PreconditionError):
        orchestrate.localize_images("bk")
