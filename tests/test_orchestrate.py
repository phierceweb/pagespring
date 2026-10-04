"""Ingest orchestration with a mocked pattern; an autouse fixture stages into a tmp dir."""

import pathlib
import re
import urllib.error

import pytest
from pf_core.exceptions import ClientError, InvalidInputError, PreconditionError

from pagespring import _staging, http, localize, manifest, orchestrate, renormalize
from pagespring.base import AcquireResult
from pagespring.patterns.docs_probe import DocsProbePattern
from pagespring.patterns.microsoft_support import MicrosoftSupportPattern


@pytest.fixture(autouse=True)
def _incoming_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate.cfg, "INCOMING_DIR", str(tmp_path / "incoming"))


class _FakePattern:
    name = "fake"

    def match(self, url):
        return True

    def acquire(self, url, workdir):
        raw = workdir / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "welcome.html").write_text("<html></html>", encoding="utf-8")
        return AcquireResult(raw_dir=raw, kind="html", slug="fakeapp", pages=1)

    def normalize(self, acq, workdir):
        clean = workdir / f"{acq.slug}.html"
        clean.write_text("<h1>Fake</h1>", encoding="utf-8")
        return clean


def test_stages_clean_file_into_incoming(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    res = orchestrate.run_ingest("https://x")

    assert res["pattern"] == "fake"
    assert res["slug"] == "fakeapp"
    assert res["kind"] == "html"
    assert res["images"] == 0
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
    """Only the image cache survives a re-run: re-downloading it costs more than stray files."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://x", keep_raw=True)

    slug_dir = tmp_path / "incoming" / "fakeapp"
    (slug_dir / "images").mkdir(exist_ok=True)
    (slug_dir / "fakeapp-old-name.html").write_text("orphan", encoding="utf-8")
    (slug_dir / "raw" / "stale.html").write_text("stale", encoding="utf-8")
    (slug_dir / "images" / "old.png").write_bytes(b"png")

    orchestrate.run_ingest("https://x", keep_raw=True)

    assert not (slug_dir / "fakeapp-old-name.html").exists()
    assert (slug_dir / "images" / "old.png").exists()  # image cache survives
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


class _UntrustedBodyPattern(_FakePattern):
    def acquire(self, url, workdir):
        raise ClientError("gzip decompression failed: truncated stream")


def test_acquire_client_error_wrapped(monkeypatch):
    """A body the fetch core refused (bad gzip, over the cap) must reach the CLI as AcquireError."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _UntrustedBodyPattern())
    with pytest.raises(orchestrate.AcquireError):
        orchestrate.run_ingest("https://docs.example.com/manual")


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
    """The empty-output invariant through the real docs_probe html branch."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _ZeroFragmentHtmlPattern())
    slug_dir = tmp_path / "incoming" / "fakeapp"
    slug_dir.mkdir(parents=True)
    (slug_dir / "fakeapp.html").write_text("previous good", encoding="utf-8")

    with pytest.raises(orchestrate.EmptyOutputError):
        orchestrate.run_ingest("https://x")

    assert (slug_dir / "fakeapp.html").read_text(encoding="utf-8") == "previous good"


class _ZeroArticleMicrosoftPattern(_FakePattern):
    """A support.microsoft.com crawl that captured no article — the hub retemplated,
    or the site quota-blocked every fetch."""

    def acquire(self, url, workdir):
        raw = workdir / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        return AcquireResult(raw_dir=raw, kind="html", slug="fakeapp", pages=0)

    def normalize(self, acq, workdir):
        return MicrosoftSupportPattern().normalize(acq, workdir)


def test_zero_article_microsoft_crawl_fails_and_preserves_previous(tmp_path, monkeypatch):
    """A titled shell around zero articles is non-empty, so normalize must refuse before the clear."""
    url = "https://support.microsoft.com/en-us/fakeapp"
    monkeypatch.setattr(orchestrate, "classify", lambda u: _ZeroArticleMicrosoftPattern())
    slug_dir = tmp_path / "incoming" / "fakeapp"
    # A same-source manifest, so the takeover guard passes and the run reaches the
    # staging clear: without it the refusal is untested — any raise preserves the dir.
    _write_manifest(
        slug_dir, source_url=url, pattern="microsoft_support", deliverable="fakeapp.html"
    )
    (slug_dir / "fakeapp.html").write_text("previous good", encoding="utf-8")

    with pytest.raises(InvalidInputError, match="no article"):
        orchestrate.run_ingest(url)

    assert (slug_dir / "fakeapp.html").read_text(encoding="utf-8") == "previous good"


def test_writes_manifest_beside_deliverable(tmp_path, monkeypatch):
    """Every ingest drops a manifest.json next to the clean file, carrying the
    provenance + a hash of the deliverable."""
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
    assert m["pages"] == 1
    assert m["bytes"] == len("<h1>Fake</h1>")
    assert m["images"] == 0
    assert m["schema_version"] == manifest.SCHEMA_VERSION
    assert m["pagespring_version"]
    # Default (no --download-images): the manifest hash IS the on-disk file's hash.
    assert m["sha256"] == manifest.sha256_file(slug_dir / "fakeapp.html")
    # ISO-8601 UTC, e.g. 2026-06-14T17:23:01Z
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", m["ingested_at"])


class _ValidatorPattern(_FakePattern):
    """Single-fetch fake whose acquire captured response cache validators."""

    def acquire(self, url, workdir):
        raw = workdir / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "m.pdf").write_bytes(b"%PDF")
        return AcquireResult(
            raw_dir=raw,
            kind="pdf",
            slug="fakeapp",
            pages=None,
            etag='"abc123"',
            last_modified="Sat, 18 Jul 2026 10:00:00 GMT",
        )


def test_manifest_records_acquire_validators(tmp_path, monkeypatch):
    """ETag/Last-Modified captured at acquire land in the manifest — the
    refresh fast path probes with them instead of re-downloading."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _ValidatorPattern())
    orchestrate.run_ingest("https://x/manual.pdf")

    m = manifest.read_manifest(tmp_path / "incoming" / "fakeapp")
    assert m["etag"] == '"abc123"'
    assert m["last_modified"] == "Sat, 18 Jul 2026 10:00:00 GMT"


class _RestampedPattern(_ValidatorPattern):
    """The same bytes, served under new validators."""

    def acquire(self, url, workdir):
        acq = super().acquire(url, workdir)
        acq.etag, acq.last_modified = '"def456"', "Tue, 22 Sep 2026 10:00:00 GMT"
        return acq


def test_an_unchanged_refetch_records_the_validators_it_was_served(tmp_path, monkeypatch):
    """The refresh probe sends the stored validators. Left stale, a source that
    re-stamps an identical file never answers 304 again."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _ValidatorPattern())
    deliverable = pathlib.Path(orchestrate.run_ingest("https://x/manual.pdf")["clean"])
    staged = deliverable.stat().st_mtime_ns

    monkeypatch.setattr(orchestrate, "classify", lambda url: _RestampedPattern())
    res = orchestrate.run_ingest("https://x/manual.pdf", if_changed=True)

    assert res["changed"] is False
    m = manifest.read_manifest(deliverable.parent)
    assert (m["etag"], m["last_modified"]) == ('"def456"', "Tue, 22 Sep 2026 10:00:00 GMT")
    assert deliverable.stat().st_mtime_ns == staged


def test_an_unchanged_refetch_leaves_a_record_without_validator_fields_alone(tmp_path, monkeypatch):
    """A record older than the validator fields keeps its shape and version stamps."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _ValidatorPattern())
    orchestrate.run_ingest("https://x/manual.pdf")
    slug_dir = tmp_path / "incoming" / "fakeapp"
    m = manifest.read_manifest(slug_dir)
    for key in ("etag", "last_modified"):
        del m[key]
    m["schema_version"] = 1
    manifest.write_manifest(slug_dir, m)
    before = (slug_dir / "manifest.json").read_bytes()

    orchestrate.run_ingest("https://x/manual.pdf", if_changed=True)

    assert (slug_dir / "manifest.json").read_bytes() == before


def test_an_unchanged_refetch_under_the_same_validators_leaves_the_manifest_alone(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(orchestrate, "classify", lambda url: _ValidatorPattern())
    orchestrate.run_ingest("https://x/manual.pdf")
    record = tmp_path / "incoming" / "fakeapp" / "manifest.json"
    before = record.stat().st_ino, record.stat().st_mtime_ns

    orchestrate.run_ingest("https://x/manual.pdf", if_changed=True)

    assert (record.stat().st_ino, record.stat().st_mtime_ns) == before


class _PartialCrawlPattern(_FakePattern):
    """A fake that discovered more pages than it staged, and is one document."""

    def acquire(self, url, workdir):
        raw = workdir / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "0000-index.html").write_text("<h1>T</h1>", encoding="utf-8")
        return AcquireResult(
            raw_dir=raw,
            kind="html",
            slug="fakeapp",
            pages=1,
            lost=3,
            single_document=True,
        )


def test_manifest_records_lost_and_single_document(tmp_path, monkeypatch):
    """Both fields drive an audit check, so the seam from AcquireResult into the
    manifest is what makes them real — each half passing proves nothing."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _PartialCrawlPattern())
    orchestrate.run_ingest("https://x")

    m = manifest.read_manifest(tmp_path / "incoming" / "fakeapp")
    assert m["lost"] == 3
    assert m["single_document"] is True


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


def test_if_changed_restages_when_the_deliverable_is_gone(tmp_path, monkeypatch):
    """Trusting the sha alone, a deleted deliverable would stay "unchanged" and never heal."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://x")
    slug_dir = tmp_path / "incoming" / "fakeapp"
    (slug_dir / "fakeapp.html").unlink()

    res = orchestrate.run_ingest("https://x", if_changed=True)

    assert res["changed"] is True, "a missing deliverable cannot be 'unchanged'"
    assert (slug_dir / "fakeapp.html").exists(), "the deliverable was not restored"


def test_if_changed_restages_a_truncated_deliverable(tmp_path, monkeypatch):
    """A file cut short keeps its name and a non-zero size, so only its hash tells."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://x")
    deliverable = tmp_path / "incoming" / "fakeapp" / "fakeapp.html"
    deliverable.write_text("<h1>Fa", encoding="utf-8")

    res = orchestrate.run_ingest("https://x", if_changed=True)

    assert res["changed"] is True
    assert deliverable.read_text(encoding="utf-8") == "<h1>Fake</h1>"


def test_if_changed_leaves_an_intact_localized_deliverable_alone(tmp_path, monkeypatch):
    """A localized file hashes to its localized_sha256, never to the staged sha256."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _RemoteImagePattern())
    _mock_image_fetch(monkeypatch)
    orchestrate.run_ingest("https://x", download_images=True)
    deliverable = tmp_path / "incoming" / "fakeapp" / "fakeapp.html"
    localized = deliverable.read_text(encoding="utf-8")

    res = orchestrate.run_ingest("https://x", if_changed=True)

    assert res["changed"] is False
    assert deliverable.read_text(encoding="utf-8") == localized


def test_if_changed_keeps_a_localized_deliverable_that_recorded_no_localized_sha(
    tmp_path, monkeypatch
):
    """A pass that recorded no localized_sha256 left nothing to compare against;
    re-staging would put back remote refs whose tokens may have expired."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _RemoteImagePattern())
    _mock_image_fetch(monkeypatch)
    orchestrate.run_ingest("https://x", download_images=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"
    m = manifest.read_manifest(slug_dir)
    m["localized_sha256"] = None
    manifest.write_manifest(slug_dir, m)
    deliverable = slug_dir / "fakeapp.html"
    localized = deliverable.read_text(encoding="utf-8")

    res = orchestrate.run_ingest("https://x", if_changed=True)

    assert res["changed"] is False
    assert deliverable.read_text(encoding="utf-8") == localized


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


@pytest.mark.parametrize("body", ['{"pages": 3}', "[1, 2, 3]", '"a string"'])
def test_if_changed_refuses_an_unreadable_manifest_instead_of_crashing(tmp_path, monkeypatch, body):
    """A parseable non-manifest must reach the exit-2 refusal; exit 1 means real audit findings."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    slug_dir = tmp_path / "incoming" / "fakeapp"
    slug_dir.mkdir(parents=True)
    (slug_dir / "fakeapp.html").write_text("<h1>Held</h1>", encoding="utf-8")
    (slug_dir / manifest.MANIFEST_NAME).write_text(body, encoding="utf-8")

    with pytest.raises(InvalidInputError) as exc:
        orchestrate.run_ingest("https://x", if_changed=True)

    assert "no readable manifest" in str(exc.value)
    assert (slug_dir / "fakeapp.html").read_text(encoding="utf-8") == "<h1>Held</h1>"


def test_if_changed_does_not_report_a_different_source_unchanged(tmp_path, monkeypatch):
    """Identical bytes from another URL are still a collision, not ``unchanged``."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://vendor-a.example/manual")
    slug_dir = tmp_path / "incoming" / "fakeapp"

    with pytest.raises(InvalidInputError):
        orchestrate.run_ingest("https://vendor-b.example/manual", if_changed=True)

    assert manifest.read_manifest(slug_dir)["source_url"] == "https://vendor-a.example/manual"

    res = orchestrate.run_ingest("https://vendor-b.example/manual", if_changed=True, replace=True)

    assert res["changed"] is True
    assert manifest.read_manifest(slug_dir)["source_url"] == "https://vendor-b.example/manual"


def _write_manifest(slug_dir, **over):
    slug_dir.mkdir(parents=True, exist_ok=True)
    fields = {
        "source_url": "https://openstax.org/books/bk",
        "pattern": "openstax",
        "slug": slug_dir.name,
        "kind": "html",
        "deliverable": "bk.html",
        "pages": 1,
        "size_bytes": 10,
        "sha256": "x",
        "images": 0,
        "ingested_at": "2026-06-17T00:00:00Z",
    }
    fields.update(over)
    manifest.write_manifest(slug_dir, manifest.build_manifest(**fields))


def _seal(slug_dir):
    """Record the deliverable written after `_write_manifest` as the staged content."""
    m = manifest.read_manifest(slug_dir)
    m["sha256"] = manifest.sha256_file(slug_dir / m["deliverable"])
    manifest.write_manifest(slug_dir, m)


def test_localize_images_localizes_and_updates_manifest(tmp_path, monkeypatch):
    """localize_images grabs a staged deliverable's remote images (no re-crawl),
    re-points refs, and writes the new image count back to the manifest."""
    slug_dir = tmp_path / "incoming" / "bk"
    _write_manifest(slug_dir)
    (slug_dir / "bk.html").write_text('<img src="https://x.com/a.png">', encoding="utf-8")
    _seal(slug_dir)
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda u, **k: (u, b"\x89PNG\r\n\x1a\nx", {"etag": None, "last_modified": None}),
    )
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    res = localize.localize_images("bk")

    assert res["localized"] == 1
    assert res["remaining"] == 0
    assert res["images_total"] == 1
    assert 'src="images/a.png"' in (slug_dir / "bk.html").read_text(encoding="utf-8")
    assert manifest.read_manifest(slug_dir)["images"] == 1


def test_localize_heals_a_mixed_case_image_cache(tmp_path, monkeypatch):
    """The case-healing pass has to be wired into the image pass, not merely
    exist — an unhooked one leaves the ref pointing at a name prune then deletes."""
    slug_dir = tmp_path / "incoming" / "bk"
    _write_manifest(slug_dir)
    (slug_dir / "images").mkdir(parents=True)
    (slug_dir / "images" / "MG_0757.JPG").write_bytes(b"\xff\xd8\xffx")
    (slug_dir / "bk.html").write_text('<img src="images/MG_0757.JPG">', encoding="utf-8")
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    res = localize.localize_images("bk")

    # iterdir, not is_file(): a case-insensitive filesystem resolves either
    # spelling, so only the real on-disk name proves the rename happened.
    assert [p.name for p in (slug_dir / "images").iterdir()] == ["mg_0757.jpg"]
    assert 'src="images/mg_0757.jpg"' in (slug_dir / "bk.html").read_text(encoding="utf-8")
    assert res["pruned"] == 0
    assert res["images_total"] == 1


def test_localize_images_without_manifest_raises(tmp_path):
    """A slug with no manifest (never ingested) is a precondition failure."""
    (tmp_path / "incoming" / "bk").mkdir(parents=True)
    with pytest.raises(PreconditionError):
        localize.localize_images("bk")


def test_localize_images_refuses_a_manifest_missing_fields(tmp_path):
    slug_dir = tmp_path / "incoming" / "bk"
    slug_dir.mkdir(parents=True)
    (slug_dir / manifest.MANIFEST_NAME).write_text('{"pages": 3}\n', encoding="utf-8")

    with pytest.raises(PreconditionError, match="missing required fields"):
        localize.localize_images("bk")


@pytest.mark.parametrize("localized", [True, False], ids=["localized", "never-localized"])
def test_localize_refuses_to_stamp_a_damaged_deliverable_as_verified(
    tmp_path, monkeypatch, localized
):
    """Recording the damaged bytes' hash would turn audit's sha_mismatch into ok."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _RemoteImagePattern())
    _mock_image_fetch(monkeypatch)
    orchestrate.run_ingest("https://x", download_images=localized)
    slug_dir = tmp_path / "incoming" / "fakeapp"
    before = manifest.read_manifest(slug_dir)
    deliverable = slug_dir / "fakeapp.html"
    deliverable.write_text("<h1>v1</h1><img src=", encoding="utf-8")

    with pytest.raises(PreconditionError, match="re-ingest"):
        localize.localize_images("fakeapp")

    assert manifest.read_manifest(slug_dir) == before
    assert deliverable.read_text(encoding="utf-8") == "<h1>v1</h1><img src="


def test_localize_proceeds_on_a_deliverable_localized_without_a_record(tmp_path, monkeypatch):
    """An image pass that recorded no localized_sha256 left nothing to check against;
    localize warns and records one, as audit's sha_unverified advice expects."""
    from structlog.testing import capture_logs

    slug_dir = tmp_path / "incoming" / "bk"
    _write_manifest(slug_dir, images=1)
    (slug_dir / "images").mkdir()
    (slug_dir / "images" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\nx")
    (slug_dir / "bk.html").write_text('<img src="images/a.png">', encoding="utf-8")
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    with capture_logs() as logs:
        localize.localize_images("bk")

    assert ("localize.unverifiable", "warning") in [(e["event"], e["log_level"]) for e in logs]
    m = manifest.read_manifest(slug_dir)
    assert m["localized_sha256"] == manifest.sha256_file(slug_dir / "bk.html")


def test_a_localize_killed_mid_pass_can_be_resumed(tmp_path, monkeypatch):
    """The pass checkpoints the deliverable as images land, so a kill leaves bytes the
    manifest never recorded; the re-run must resume rather than refuse."""
    from pagespring import images

    monkeypatch.setattr(orchestrate, "classify", lambda url: _RemoteImagePattern())
    _mock_image_fetch(monkeypatch)
    orchestrate.run_ingest("https://x", download_images=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"
    deliverable = slug_dir / "fakeapp.html"
    deliverable.write_text(
        deliverable.read_text(encoding="utf-8") + '<img src="https://img.example/new.png">',
        encoding="utf-8",
    )
    m = manifest.read_manifest(slug_dir)
    m["localized_sha256"] = manifest.sha256_file(deliverable)
    manifest.write_manifest(slug_dir, m)
    real_download = images.download_images

    def killed_after_a_checkpoint(doc_path, images_dir, **kwargs):
        (images_dir / "new.png").write_bytes(b"\x89PNG\r\n\x1a\nx")
        text = doc_path.read_text(encoding="utf-8")
        doc_path.write_text(
            text.replace("https://img.example/new.png", "images/new.png"), encoding="utf-8"
        )
        raise KeyboardInterrupt

    monkeypatch.setattr(images, "download_images", killed_after_a_checkpoint)
    with pytest.raises(KeyboardInterrupt):
        localize.localize_images("fakeapp")
    monkeypatch.setattr(images, "download_images", real_download)

    res = localize.localize_images("fakeapp")

    assert res["remaining"] == 0
    after = manifest.read_manifest(slug_dir)
    assert after["localized_sha256"] == manifest.sha256_file(deliverable)


class _RawDrivenPattern(_FakePattern):
    """A fake whose output derives from raw/ and ``prefix``, so a replay shows both the kept raw and
    the current normalize."""

    def __init__(self, prefix: str = "v1"):
        self.prefix = prefix

    def normalize(self, acq, workdir):
        body = (acq.raw_dir / "welcome.html").read_text(encoding="utf-8")
        clean = workdir / f"{acq.slug}.html"
        clean.write_text(f"{self.prefix}:{body}", encoding="utf-8")
        return clean


def test_renormalize_replays_from_kept_raw_without_network(tmp_path, monkeypatch):
    p = _RawDrivenPattern(prefix="v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://x", keep_raw=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"
    assert (slug_dir / "fakeapp.html").read_text(encoding="utf-8") == "v1:<html></html>"

    p.prefix = "v2"  # the normalize code changed; raw did not

    def _no_acquire(url, workdir):  # pragma: no cover - proves replay skips acquire
        raise AssertionError("renormalize must not acquire")

    monkeypatch.setattr(p, "acquire", _no_acquire)
    monkeypatch.setattr(renormalize, "pattern_by_name", lambda name: p if name == "fake" else None)

    res = renormalize.run_renormalize("fakeapp")

    assert res["changed"] is True
    assert res["pattern"] == "fake"
    assert res["slug"] == "fakeapp"
    assert (slug_dir / "fakeapp.html").read_text(encoding="utf-8") == "v2:<html></html>"
    assert (slug_dir / "raw" / "welcome.html").exists()  # raw kept for the next replay


def test_renormalize_unchanged_output_leaves_slug_dir_untouched(tmp_path, monkeypatch):
    """An identical replay leaves mtime and images/ alone: the refactor-was-safe signal."""
    p = _RawDrivenPattern(prefix="v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://x", keep_raw=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"
    deliverable = slug_dir / "fakeapp.html"
    before_mtime = deliverable.stat().st_mtime_ns
    (slug_dir / "images").mkdir()
    (slug_dir / "images" / "a.png").write_bytes(b"png")

    monkeypatch.setattr(renormalize, "pattern_by_name", lambda name: p)
    res = renormalize.run_renormalize("fakeapp")

    assert res["changed"] is False
    assert deliverable.stat().st_mtime_ns == before_mtime
    assert (slug_dir / "images" / "a.png").read_bytes() == b"png"


@pytest.mark.parametrize("damage", ["deleted", "truncated"])
def test_renormalize_restages_an_identical_replay_over_a_damaged_file(
    tmp_path, monkeypatch, damage
):
    """The recorded sha matching the replay says nothing about the file on disk."""
    p = _RawDrivenPattern(prefix="v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://x", keep_raw=True)
    deliverable = tmp_path / "incoming" / "fakeapp" / "fakeapp.html"
    if damage == "deleted":
        deliverable.unlink()
    else:
        deliverable.write_text("v1:<ht", encoding="utf-8")
    monkeypatch.setattr(renormalize, "pattern_by_name", lambda name: p)

    res = renormalize.run_renormalize("fakeapp")

    assert res["changed"] is True
    assert deliverable.read_text(encoding="utf-8") == "v1:<html></html>"


def test_renormalize_restaging_closes_a_cut_short_image_pass(tmp_path, monkeypatch):
    from pagespring import _integrity, audit

    p = _RawDrivenPattern(prefix="v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://x", keep_raw=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"
    _integrity.open_for_image_pass(slug_dir, manifest.read_manifest(slug_dir))
    p.prefix = "v2"
    monkeypatch.setattr(renormalize, "pattern_by_name", lambda name: p)

    assert renormalize.run_renormalize("fakeapp")["changed"] is True

    assert audit.audit_slug("fakeapp") == []


class _LossCountingPattern(_RawDrivenPattern):
    """Normalize derives page loss on top of what acquire recorded."""

    def __init__(self, prefix: str = "v1", drops: int = 0):
        super().__init__(prefix)
        self.drops = drops
        self.seen_lost: list[int] = []

    def normalize(self, acq, workdir):
        self.seen_lost.append(acq.lost)
        acq.lost = 3 + self.drops
        return super().normalize(acq, workdir)


@pytest.mark.parametrize("prefix", ["v1", "v2"], ids=["identical-replay", "changed-replay"])
def test_renormalize_records_the_lost_count_normalize_derives(tmp_path, monkeypatch, prefix):
    p = _LossCountingPattern()
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://x", keep_raw=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"
    assert manifest.read_manifest(slug_dir)["lost"] == 3

    p.prefix, p.drops = prefix, 2
    monkeypatch.setattr(renormalize, "pattern_by_name", lambda name: p)
    renormalize.run_renormalize("fakeapp")

    assert p.seen_lost[-1] == 3, "the replay must start from the recorded loss"
    assert manifest.read_manifest(slug_dir)["lost"] == 5


class _PageCountingPattern(_RawDrivenPattern):
    """Normalize recounts the pages acquire reported (as archive_download does)."""

    def __init__(self, prefix: str = "v1", pages: int = 4):
        super().__init__(prefix)
        self.pages = pages

    def normalize(self, acq, workdir):
        acq.pages = self.pages
        return super().normalize(acq, workdir)


@pytest.mark.parametrize("prefix", ["v1", "v2"], ids=["identical-replay", "changed-replay"])
def test_renormalize_records_the_page_count_normalize_derives(tmp_path, monkeypatch, prefix):
    p = _PageCountingPattern()
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://x", keep_raw=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"
    assert manifest.read_manifest(slug_dir)["pages"] == 4

    p.prefix, p.pages = prefix, 2
    monkeypatch.setattr(renormalize, "pattern_by_name", lambda name: p)
    res = renormalize.run_renormalize("fakeapp")

    assert res["pages"] == 2
    assert manifest.read_manifest(slug_dir)["pages"] == 2


def test_renormalize_without_manifest_raises(tmp_path):
    """A slug never ingested (no manifest) is a precondition failure."""
    (tmp_path / "incoming" / "bk").mkdir(parents=True)
    with pytest.raises(PreconditionError, match="ingest it first"):
        renormalize.run_renormalize("bk")


def test_renormalize_without_kept_raw_raises(tmp_path, monkeypatch):
    """An ingest without --keep-raw left no raw/ to replay — the error says how
    to enable the replay, and the staged deliverable is untouched."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://x")  # no keep_raw
    with pytest.raises(PreconditionError, match="--keep-raw"):
        renormalize.run_renormalize("fakeapp")
    assert (tmp_path / "incoming" / "fakeapp" / "fakeapp.html").exists()


def test_renormalize_empty_output_fails_and_preserves_previous(tmp_path, monkeypatch):
    """A replay that normalizes to nothing hard-fails BEFORE staging — the
    staged deliverable and manifest survive (same invariant as ingest)."""
    p = _RawDrivenPattern(prefix="v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://x", keep_raw=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"

    monkeypatch.setattr(renormalize, "pattern_by_name", lambda name: _EmptyPattern())
    with pytest.raises(orchestrate.EmptyOutputError):
        renormalize.run_renormalize("fakeapp")

    assert (slug_dir / "fakeapp.html").read_text(encoding="utf-8") == "v1:<html></html>"
    assert manifest.read_manifest(slug_dir)["sha256"] == manifest.sha256_file(
        slug_dir / "fakeapp.html"
    )


def test_renormalize_unknown_pattern_raises(tmp_path, monkeypatch):
    """A manifest naming an unregistered pattern (renamed or removed since the
    ingest) fails with the pattern's name."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://x", keep_raw=True)
    monkeypatch.setattr(renormalize, "pattern_by_name", lambda name: None)
    with pytest.raises(PreconditionError, match="fake"):
        renormalize.run_renormalize("fakeapp")


def test_renormalize_updates_manifest_and_resets_image_count(tmp_path, monkeypatch):
    """A changed replay refreshes the file facts and resets the image count; crawl provenance stays."""
    p = _RawDrivenPattern(prefix="v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://docs.example.com/foo", keep_raw=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"
    before = manifest.read_manifest(slug_dir)

    p.prefix = "v2"
    monkeypatch.setattr(renormalize, "pattern_by_name", lambda name: p)
    res = renormalize.run_renormalize("fakeapp")

    assert res["changed"] is True
    after = manifest.read_manifest(slug_dir)
    assert after["sha256"] == manifest.sha256_file(slug_dir / "fakeapp.html")
    assert after["sha256"] != before["sha256"]
    assert after["bytes"] == len("v2:<html></html>")
    assert after["images"] == 0
    assert after["source_url"] == before["source_url"]
    assert after["ingested_at"] == before["ingested_at"]
    assert after["pattern"] == before["pattern"]
    assert after["pages"] == before["pages"]


def test_renormalize_changed_clears_stale_localized_images(tmp_path, monkeypatch):
    """Stale images, named for the old refs, would push every re-download onto a suffixed name."""
    p = _RawDrivenPattern(prefix="v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://docs.example.com/foo", keep_raw=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"
    (slug_dir / "images").mkdir()
    (slug_dir / "images" / "a.png").write_bytes(b"png")

    p.prefix = "v2"
    monkeypatch.setattr(renormalize, "pattern_by_name", lambda name: p)
    res = renormalize.run_renormalize("fakeapp")

    assert res["changed"] is True
    assert not (slug_dir / "images").exists()
    assert (slug_dir / "raw" / "welcome.html").exists()  # raw untouched — it is the input


def test_ingest_warns_when_content_duplicates_another_slug(tmp_path, monkeypatch):
    """Still staged, since the duplicate might be deliberate; the warning is the product."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://vendor-a.example/manual")

    class _SameContentOtherSlug(_FakePattern):
        def acquire(self, url, workdir):
            raw = workdir / "raw"
            raw.mkdir(parents=True, exist_ok=True)
            (raw / "welcome.html").write_text("<html></html>", encoding="utf-8")
            return AcquireResult(raw_dir=raw, kind="html", slug="fakeapp-alias", pages=1)

    monkeypatch.setattr(orchestrate, "classify", lambda url: _SameContentOtherSlug())
    res = orchestrate.run_ingest("https://vendor-b.example/manual")

    assert res["duplicate_of"] == "fakeapp"
    assert (tmp_path / "incoming" / "fakeapp-alias" / "fakeapp-alias.html").exists()


def test_ingest_unique_content_has_no_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    res = orchestrate.run_ingest("https://x")
    assert res["duplicate_of"] is None


def test_ingest_slug_override_controls_naming(tmp_path, monkeypatch):
    """--slug renames the staged identity end to end: dir, manifest slug, and
    the deliverable filename the pattern's normalize produces."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    res = orchestrate.run_ingest("https://x", slug_override="Sennheiser EW IEM G4!")

    assert res["slug"] == "sennheiser-ew-iem-g4"  # folded via slugify
    slug_dir = tmp_path / "incoming" / "sennheiser-ew-iem-g4"
    assert (slug_dir / "sennheiser-ew-iem-g4.html").exists()
    m = manifest.read_manifest(slug_dir)
    assert m["slug"] == "sennheiser-ew-iem-g4"
    assert m["deliverable"] == "sennheiser-ew-iem-g4.html"


def test_ingest_stages_deliverable_under_final_slug_name(tmp_path, monkeypatch):
    """pdf_url names its file at acquire, before ``--slug`` is known, so staging renames centrally."""

    class _MisnamedOutputPattern(_FakePattern):
        def normalize(self, acq, workdir):
            clean = workdir / "whatever-acquire-called-it.html"
            clean.write_text("<h1>x</h1>", encoding="utf-8")
            return clean

    monkeypatch.setattr(orchestrate, "classify", lambda url: _MisnamedOutputPattern())
    res = orchestrate.run_ingest("https://x", slug_override="tidy-name")

    assert res["clean"].endswith("incoming/tidy-name/tidy-name.html")
    assert (tmp_path / "incoming" / "tidy-name" / "tidy-name.html").exists()
    assert manifest.read_manifest(tmp_path / "incoming" / "tidy-name")["deliverable"] == (
        "tidy-name.html"
    )


def test_ingest_slug_override_that_folds_to_nothing_rejected(monkeypatch):
    from pf_core.exceptions import InvalidInputError

    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    with pytest.raises(InvalidInputError):
        orchestrate.run_ingest("https://x", slug_override="!!!")


class _TitledPattern(_FakePattern):
    """Normalize renders the acquire-time title — the field a replay can only
    know if the manifest recorded it."""

    def acquire(self, url, workdir):
        raw = workdir / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "welcome.html").write_text("<html></html>", encoding="utf-8")
        return AcquireResult(
            raw_dir=raw, kind="html", slug="fakeapp", pages=1, title="Fake App Guide"
        )

    def normalize(self, acq, workdir):
        clean = workdir / f"{acq.slug}.html"
        clean.write_text(f"<h1>{acq.title or acq.slug}</h1>", encoding="utf-8")
        return clean


def test_renormalize_reconstructs_title_from_manifest(tmp_path, monkeypatch):
    """Fed the recorded title, an unchanged pattern replays byte-identical instead of falling back
    to the slug."""
    p = _TitledPattern()
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://x", keep_raw=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"
    assert manifest.read_manifest(slug_dir)["title"] == "Fake App Guide"

    monkeypatch.setattr(renormalize, "pattern_by_name", lambda name: p)
    res = renormalize.run_renormalize("fakeapp")

    assert res["changed"] is False
    assert (slug_dir / "fakeapp.html").read_text(encoding="utf-8") == "<h1>Fake App Guide</h1>"


class _MarkdownEmittingPattern(_FakePattern):
    """The current normalize emits .md where the staged deliverable was .html."""

    def normalize(self, acq, workdir):
        clean = workdir / f"{acq.slug}.md"
        clean.write_text("# now markdown", encoding="utf-8")
        return clean


def test_renormalize_replaces_deliverable_when_name_changes(tmp_path, monkeypatch):
    """A normalize whose output filename changed (e.g. html → md) replaces the
    old deliverable instead of leaving both, and the manifest tracks the new name."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://x", keep_raw=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"

    monkeypatch.setattr(renormalize, "pattern_by_name", lambda name: _MarkdownEmittingPattern())
    res = renormalize.run_renormalize("fakeapp")

    assert res["changed"] is True
    assert not (slug_dir / "fakeapp.html").exists()
    assert (slug_dir / "fakeapp.md").read_text(encoding="utf-8") == "# now markdown"
    assert manifest.read_manifest(slug_dir)["deliverable"] == "fakeapp.md"


def test_localize_images_reuses_unchanged_before_downloading(tmp_path, monkeypatch):
    from pagespring import images

    slug_dir = tmp_path / "incoming" / "bk"
    _write_manifest(slug_dir)
    (slug_dir / "bk.html").write_text(
        '<img src="https://x.com/a.png"><img src="https://x.com/new.png">', encoding="utf-8"
    )
    _seal(slug_dir)
    imgs = slug_dir / "images"
    imgs.mkdir()
    (imgs / "a.png").write_bytes(b"\x89PNG\r\n\x1a\nold")
    images.write_sidecar(
        slug_dir,
        [
            {
                "local": "a.png",
                "source_url": "https://x.com/a.png",
                "etag": '"a"',
                "last_modified": None,
                "sha256": "unused",
                "bytes": 11,
            }
        ],
    )

    fetched: list[str] = []

    def fetch(url, **kwargs):
        fetched.append(url)
        return url, b"\x89PNG\r\n\x1a\nnew", {"etag": None, "last_modified": None}

    monkeypatch.setattr(http, "not_modified", lambda u, **k: u == "https://x.com/a.png")
    monkeypatch.setattr(http, "fetch_bytes_meta", fetch)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    res = localize.localize_images("bk")

    assert res["reused"] == 1
    assert fetched == ["https://x.com/new.png"]  # the unchanged image never re-downloaded
    assert (imgs / "a.png").read_bytes() == b"\x89PNG\r\n\x1a\nold"  # original kept
    assert res["remaining"] == 0


def test_localize_during_an_outage_keeps_every_cached_image(tmp_path, monkeypatch):
    """Probe and download both fail: the cached file and its provenance must survive
    the pass, not be deleted before a replacement is in hand."""
    from pagespring import images

    slug_dir = tmp_path / "incoming" / "bk"
    _write_manifest(slug_dir)
    (slug_dir / "bk.html").write_text('<img src="https://x.com/a.png?token=t">', encoding="utf-8")
    _seal(slug_dir)
    imgs = slug_dir / "images"
    imgs.mkdir()
    cached = b"\x89PNG\r\n\x1a\nold"
    (imgs / "a.png").write_bytes(cached)
    record = {
        "local": "a.png",
        "source_url": "https://x.com/a.png?token=t",
        "etag": '"a"',
        "last_modified": None,
        "sha256": "unused",
        "bytes": len(cached),
    }
    images.write_sidecar(slug_dir, [record])

    def down(url, **kwargs):
        raise urllib.error.URLError("network is unreachable")

    monkeypatch.setattr(http, "not_modified", lambda u, **k: False)
    monkeypatch.setattr(http, "fetch_bytes_meta", down)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    res = localize.localize_images("bk")

    assert (imgs / "a.png").read_bytes() == cached
    assert images.read_sidecar(slug_dir) == [record]
    assert (res["remaining"], res["pruned"], res["images_total"]) == (1, 0, 1)


def test_reingest_preserves_images_and_sidecar(tmp_path, monkeypatch):
    """Wiping the image cache re-downloads every image on refresh; stale files and raw/ still go."""
    from pagespring import images

    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://x")
    slug_dir = tmp_path / "incoming" / "fakeapp"

    imgs = slug_dir / "images"
    imgs.mkdir(exist_ok=True)
    (imgs / "kept.png").write_bytes(b"\x89PNG\r\n\x1a\nx")
    images.write_sidecar(
        slug_dir,
        [
            {
                "local": "kept.png",
                "source_url": "https://x.com/kept.png",
                "etag": '"k"',
                "last_modified": None,
                "sha256": "abc",
                "bytes": 11,
            }
        ],
    )
    (slug_dir / "orphan-clean.html").write_text("stale", encoding="utf-8")

    orchestrate.run_ingest("https://x")

    assert (imgs / "kept.png").read_bytes() == b"\x89PNG\r\n\x1a\nx"
    assert [r["source_url"] for r in images.read_sidecar(slug_dir)] == ["https://x.com/kept.png"]
    assert not (slug_dir / "orphan-clean.html").exists()  # stale output still cleared


def test_localize_prunes_orphans_once_fully_localized(tmp_path, monkeypatch):
    """After a refresh drops an image, its file must not linger in images/."""
    from pagespring import images

    slug_dir = tmp_path / "incoming" / "bk"
    _write_manifest(slug_dir)
    (slug_dir / "bk.html").write_text('<img src="https://x.com/keep.png">', encoding="utf-8")
    _seal(slug_dir)
    imgs = slug_dir / "images"
    imgs.mkdir()
    (imgs / "dropped.png").write_bytes(b"\x89PNG\r\n\x1a\nold")
    images.write_sidecar(
        slug_dir,
        [
            {
                "local": "dropped.png",
                "source_url": "https://x.com/dropped.png",
                "etag": None,
                "last_modified": None,
                "sha256": "x",
                "bytes": 3,
            }
        ],
    )
    monkeypatch.setattr(http, "not_modified", lambda u, **k: False)
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda u, **k: (u, b"\x89PNG\r\n\x1a\nnew", {"etag": None, "last_modified": None}),
    )
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    res = localize.localize_images("bk")

    assert res["pruned"] == 1
    assert sorted(p.name for p in imgs.glob("*")) == ["keep.png"]
    assert res["images_total"] == 1  # manifest counts what survives, not orphans
    assert manifest.read_manifest(slug_dir)["images"] == 1


def test_manifest_records_whether_raw_was_kept(tmp_path, monkeypatch):
    """Only the manifest says which slugs replay for free without listing every directory."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())

    orchestrate.run_ingest("https://x", keep_raw=True)
    kept = manifest.read_manifest(tmp_path / "incoming" / "fakeapp")
    assert kept is not None and kept["kept_raw"] is True

    orchestrate.run_ingest("https://x")
    plain = manifest.read_manifest(tmp_path / "incoming" / "fakeapp")
    assert plain is not None and plain["kept_raw"] is False


def test_kept_raw_reflects_the_directory_not_the_flag(tmp_path, monkeypatch):
    """Recorded from what is actually on disk after staging, so the manifest
    cannot claim a replay that isn't possible."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://x", keep_raw=True)

    slug_dir = tmp_path / "incoming" / "fakeapp"
    m = manifest.read_manifest(slug_dir)
    assert m is not None
    assert m["kept_raw"] == (slug_dir / "raw").is_dir()


def test_keep_raw_is_ignored_for_pdf_deliverables(tmp_path, monkeypatch):
    """A PDF normalize is a passthrough, so raw/ would be a second copy of the deliverable."""

    class _PdfPattern(_FakePattern):
        def acquire(self, url, workdir):
            acq = super().acquire(url, workdir)
            pdf = acq.raw_dir / "fakeapp.pdf"
            pdf.write_bytes(b"%PDF-1.7 body")
            return AcquireResult(raw_dir=acq.raw_dir, kind="pdf", slug="fakeapp", pages=1)

        def normalize(self, acq, workdir):
            return next(acq.raw_dir.glob("*.pdf"))

    monkeypatch.setattr(orchestrate, "classify", lambda url: _PdfPattern())
    orchestrate.run_ingest("https://x/m.pdf", keep_raw=True)

    slug_dir = tmp_path / "incoming" / "fakeapp"
    assert not (slug_dir / "raw").exists()
    m = manifest.read_manifest(slug_dir)
    assert m is not None and m["kept_raw"] is False


def test_localize_is_a_no_op_for_pdf_deliverables(tmp_path, monkeypatch):
    """Reading a PDF as UTF-8 raises an error the CLI doesn't catch, stopping ``localize --all``."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://x")
    slug_dir = tmp_path / "incoming" / "fakeapp"
    (slug_dir / "fakeapp.html").unlink()
    (slug_dir / "fakeapp.pdf").write_bytes(b"%PDF-1.7 \xe2\xe2 raw binary")
    m = manifest.read_manifest(slug_dir)
    assert m is not None
    m["kind"], m["deliverable"] = "pdf", "fakeapp.pdf"
    manifest.write_manifest(slug_dir, m)

    r = localize.localize_images("fakeapp")

    assert r["localized"] == 0 and r["remaining"] == 0


class _RemoteImagePattern(_FakePattern):
    """A fake whose deliverable carries one remote image ref — the same URL on
    every crawl, as a refreshed source serves. ``prefix`` varies the bytes."""

    def __init__(self, prefix: str = "v1"):
        self.prefix = prefix

    def normalize(self, acq, workdir):
        clean = workdir / f"{acq.slug}.html"
        clean.write_text(
            f'<h1>{self.prefix}</h1><img src="https://img.example/logo.png">', encoding="utf-8"
        )
        return clean


def _mock_image_fetch(monkeypatch, *, unchanged=True):
    monkeypatch.setattr(http, "not_modified", lambda u, **k: unchanged)
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda u, **k: (u, b"\x89PNG\r\n\x1a\nx", {"etag": None, "last_modified": None}),
    )
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)


def test_reingest_with_images_keeps_one_copy_of_each_image(tmp_path, monkeypatch):
    """Without the reuse probe each image re-downloads onto a suffixed name, growing every refresh."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _RemoteImagePattern())
    _mock_image_fetch(monkeypatch)

    orchestrate.run_ingest("https://x", download_images=True)
    res = orchestrate.run_ingest("https://x", download_images=True)

    slug_dir = tmp_path / "incoming" / "fakeapp"
    assert sorted(p.name for p in (slug_dir / "images").iterdir()) == ["logo.png"]
    assert res["images"] == 1
    assert res["images_downloaded"] == 0, "a reused image is not a download"
    # the deliverable points at the file that is actually there
    assert 'src="images/logo.png"' in (slug_dir / "fakeapp.html").read_text(encoding="utf-8")


def test_ingest_records_the_localized_sha(tmp_path, monkeypatch):
    """The image pass re-points refs, so only the post-pass hash describes the file on disk."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _RemoteImagePattern())
    _mock_image_fetch(monkeypatch)

    orchestrate.run_ingest("https://x", download_images=True)

    slug_dir = tmp_path / "incoming" / "fakeapp"
    m = manifest.read_manifest(slug_dir)
    assert m["localized_sha256"] == manifest.sha256_file(slug_dir / "fakeapp.html")
    assert m["localized_sha256"] != m["sha256"]


def test_renormalize_clears_the_localized_sha(tmp_path, monkeypatch):
    """Left standing, the post-localize hash would make audit report a permanent sha_mismatch."""
    p = _RemoteImagePattern(prefix="v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    _mock_image_fetch(monkeypatch)
    orchestrate.run_ingest("https://x", keep_raw=True, download_images=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"
    assert manifest.read_manifest(slug_dir)["localized_sha256"] is not None

    p.prefix = "v2"
    monkeypatch.setattr(renormalize, "pattern_by_name", lambda name: p)
    res = renormalize.run_renormalize("fakeapp")

    assert res["changed"] is True
    after = manifest.read_manifest(slug_dir)
    assert after["localized_sha256"] is None
    assert after["images"] == 0
    assert after["sha256"] == manifest.sha256_file(slug_dir / "fakeapp.html")


class _HostilePattern(_FakePattern):
    """A pattern whose slug escapes ``incoming/``; a ``..`` URL segment can produce one."""

    def __init__(self, slug):
        self._slug = slug

    def acquire(self, url, workdir):
        acq = super().acquire(url, workdir)
        acq.slug = self._slug
        return acq

    def normalize(self, acq, workdir):
        clean = workdir / "out.html"
        clean.write_text("<h1>Fake</h1>", encoding="utf-8")
        return clean


@pytest.mark.parametrize(
    "slug",
    ["..", ".", "", "../..", "/", "./..", "a/../..", "a/../../b", "\\", "....//", "  ..  "],
)
def test_pattern_slug_cannot_escape_incoming(tmp_path, monkeypatch, slug):
    """``incoming/..`` is the repo root and re-ingest rmtrees its target: the run must refuse or
    write strictly inside ``incoming/`` (``a/../..`` may fold to ``a``)."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _HostilePattern(slug))
    # A file that MUST survive: it sits where the traversal would land.
    sentinel = tmp_path / "DO_NOT_DELETE.txt"
    sentinel.write_text("preserved", encoding="utf-8")
    incoming = tmp_path / "incoming"
    incoming.mkdir(parents=True, exist_ok=True)

    try:
        res = orchestrate.run_ingest("https://x")
    except InvalidInputError:
        pass  # folded to empty — refused outright
    else:
        written = pathlib.Path(res["clean"]).resolve()
        assert incoming.resolve() in written.parents, f"escaped incoming/: {written}"

    assert sentinel.read_text(encoding="utf-8") == "preserved"
    assert sentinel.parent.resolve() == tmp_path.resolve()


def test_pattern_slug_is_folded_like_slug_override(tmp_path, monkeypatch):
    """Benign oddities fold rather than fail, matching ``--slug`` behavior."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _HostilePattern("Azure Docs/v2"))
    res = orchestrate.run_ingest("https://x")

    assert res["slug"] == "azure-docs-v2"
    assert (tmp_path / "incoming" / "azure-docs-v2").is_dir()


def test_an_ingest_killed_during_the_image_pass_still_leaves_provenance(tmp_path, monkeypatch):
    """The manifest is written before the image pass, which is minutes of network."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())

    def die(*args, **kwargs):
        raise OSError("killed during the image pass")

    monkeypatch.setattr(localize, "_image_pass", die)

    with pytest.raises(OSError):
        orchestrate.run_ingest("https://x", download_images=True)

    slug_dir = tmp_path / "incoming" / "fakeapp"
    m = manifest.read_manifest(slug_dir)
    assert m is not None, "no provenance left behind — the slug is unrecoverable"
    assert m["source_url"] == "https://x"
    assert m["deliverable"] == "fakeapp.html"
    # It describes the un-localized deliverable, which is exactly what is on disk.
    assert m["images"] == 0
    assert m["localized_sha256"] is None
    assert m["sha256"] == manifest.sha256_file(slug_dir / "fakeapp.html")


def _die_writing_the_deliverable(mp, exc):
    """Fail the deliverable write partway, the way a full disk or a kill does."""

    def die_writing(path, data, **kwargs):
        path.with_name(f".{path.name}.k1ll3d.tmp").write_bytes(data[:3])
        raise exc

    mp.setattr(_staging, "atomic_write_bytes", die_writing)


@pytest.mark.parametrize(
    "exc",
    [OSError(28, "No space left on device"), KeyboardInterrupt()],
    ids=["disk-full", "killed"],
)
def test_a_reingest_whose_write_fails_keeps_the_previous_deliverable(tmp_path, monkeypatch, exc):
    p = _BodyPattern("<h1>Good</h1>")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://first")
    slug_dir = tmp_path / "incoming" / "fakeapp"
    (slug_dir / "stale.html").write_text("stale", encoding="utf-8")

    p.body = "<h1>Newer</h1>"
    with pytest.MonkeyPatch.context() as mp, pytest.raises(type(exc)):
        _die_writing_the_deliverable(mp, exc)
        orchestrate.run_ingest("https://first")

    assert (slug_dir / "fakeapp.html").read_text(encoding="utf-8") == "<h1>Good</h1>"
    assert (slug_dir / "stale.html").exists(), "cleared before the new deliverable landed"
    survived = manifest.read_manifest(slug_dir)
    assert survived is not None and survived["source_url"] == "https://first"


def test_a_partial_write_is_swept_by_the_next_ingest(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://x")
    slug_dir = tmp_path / "incoming" / "fakeapp"
    (slug_dir / ".fakeapp.html.k1ll3d.tmp").write_text("<h1>Fa", encoding="utf-8")

    orchestrate.run_ingest("https://x")

    assert sorted(p.name for p in slug_dir.iterdir()) == ["fakeapp.html", "manifest.json"]


def test_renormalize_whose_write_fails_keeps_the_staged_deliverable(tmp_path, monkeypatch):
    p = _RawDrivenPattern(prefix="v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://x", keep_raw=True)
    deliverable = tmp_path / "incoming" / "fakeapp" / "fakeapp.html"

    p.prefix = "v2"
    monkeypatch.setattr(renormalize, "pattern_by_name", lambda name: p)
    with pytest.MonkeyPatch.context() as mp, pytest.raises(OSError):
        _die_writing_the_deliverable(mp, OSError(28, "No space left on device"))
        renormalize.run_renormalize("fakeapp")

    assert deliverable.read_text(encoding="utf-8") == "v1:<html></html>"


def test_a_takeover_killed_before_its_provenance_leaves_the_displaced_manual_whole(
    tmp_path, monkeypatch
):
    """Nothing of the displaced manual is removed before the new one lands, so its
    manifest still describes files that are there, and taking over needs --replace again."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://vendor-a.example/manual")
    slug_dir = tmp_path / "incoming" / "fakeapp"
    before = (slug_dir / "fakeapp.html").read_bytes()

    def die(*args, **kwargs):
        raise OSError("killed before the new manifest was written")

    with pytest.MonkeyPatch.context() as mp, pytest.raises(OSError):
        mp.setattr(orchestrate.manifest, "write_manifest", die)
        orchestrate.run_ingest("https://vendor-b.example/manual", replace=True)

    assert manifest.read_manifest(slug_dir)["source_url"] == "https://vendor-a.example/manual"
    assert (slug_dir / "fakeapp.html").read_bytes() == before
    with pytest.raises(InvalidInputError):
        orchestrate.run_ingest("https://vendor-b.example/manual")


def test_a_takeover_whose_deliverable_write_dies_keeps_the_displaced_record(tmp_path, monkeypatch):
    """The displaced manual's file is still on disk, so its record must still name it —
    the new source's record would describe bytes that never landed."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://vendor-a.example/manual")
    slug_dir = tmp_path / "incoming" / "fakeapp"

    with pytest.MonkeyPatch.context() as mp, pytest.raises(KeyboardInterrupt):
        _die_writing_the_deliverable(mp, KeyboardInterrupt())
        orchestrate.run_ingest("https://vendor-b.example/manual", replace=True)

    assert manifest.read_manifest(slug_dir)["source_url"] == "https://vendor-a.example/manual"
    with pytest.raises(InvalidInputError):
        orchestrate.run_ingest("https://vendor-b.example/manual")
    orchestrate.run_ingest("https://vendor-b.example/manual", replace=True)
    assert manifest.read_manifest(slug_dir)["source_url"] == "https://vendor-b.example/manual"


def test_an_ingest_killed_before_the_deliverable_lands_can_be_rerun(tmp_path, monkeypatch):
    """A kill between the mkdir and the copies leaves content with no manifest; the
    next run of the SAME url must not read it as an unidentified foreign manual."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    real_copytree = orchestrate.shutil.copytree
    crawl_copies = []

    def die_on_the_first_crawl_copy(src, dst, *args, **kwargs):
        crawl_copies.append(dst)
        if len(crawl_copies) == 1:
            raise KeyboardInterrupt("killed mid-copy")
        return real_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(orchestrate.shutil, "copytree", die_on_the_first_crawl_copy)
    with pytest.raises(KeyboardInterrupt):
        orchestrate.run_ingest("https://x/manual", keep_raw=True)

    slug_dir = tmp_path / "incoming" / "fakeapp"
    assert (slug_dir / "fakeapp.html").exists(), "nothing was staged — window not reproduced"

    res = orchestrate.run_ingest("https://x/manual", keep_raw=True)  # same url, no --replace

    assert res["changed"] is True
    assert manifest.read_manifest(slug_dir)["kept_raw"] is True


def test_reingest_of_a_different_source_refuses_to_take_the_slug_over(tmp_path, monkeypatch):
    """incoming/ is gitignored, so a colliding second manual must not delete or overwrite the first."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://vendor-a.example/files/manual.pdf")

    slug_dir = tmp_path / "incoming" / "fakeapp"
    before = (slug_dir / "fakeapp.html").read_text(encoding="utf-8")

    with pytest.raises(InvalidInputError) as exc:
        orchestrate.run_ingest("https://vendor-b.example/docs/manual.pdf")

    assert "vendor-a.example" in str(exc.value), "the refusal must name what it protected"
    assert (slug_dir / "fakeapp.html").read_text(encoding="utf-8") == before
    m = manifest.read_manifest(slug_dir)
    assert m["source_url"] == "https://vendor-a.example/files/manual.pdf"


def test_reingest_of_the_same_source_is_allowed_across_url_spellings(tmp_path, monkeypatch):
    """The guard compares canonical URLs, so a trailing slash or scheme change is
    the same source — refusing those would break every legitimate re-ingest."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://x.example/docs/guide/")

    orchestrate.run_ingest("http://X.example/docs/guide")  # same source, respelled

    m = manifest.read_manifest(tmp_path / "incoming" / "fakeapp")
    assert m["source_url"] == "http://X.example/docs/guide"


def test_replace_takes_the_slug_over_and_drops_the_prior_image_cache(tmp_path, monkeypatch):
    """A takeover's inherited images belong to the displaced manual; prune sweeps them only by luck."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://vendor-a.example/manual.pdf")

    slug_dir = tmp_path / "incoming" / "fakeapp"
    imgs = slug_dir / "images"
    imgs.mkdir(exist_ok=True)
    (imgs / "vendor-a-diagram.png").write_bytes(b"\x89PNG vendor a")

    orchestrate.run_ingest("https://vendor-b.example/manual.pdf", replace=True)

    assert manifest.read_manifest(slug_dir)["source_url"] == "https://vendor-b.example/manual.pdf"
    assert not (imgs / "vendor-a-diagram.png").exists(), "stale image cache carried over"


def test_replace_on_the_same_source_keeps_the_image_cache(tmp_path, monkeypatch):
    """Keyed on takeover, not ``--replace``: a same-source replace is still a refresh."""
    from pagespring import images

    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://x.example/manual")

    slug_dir = tmp_path / "incoming" / "fakeapp"
    imgs = slug_dir / "images"
    imgs.mkdir(exist_ok=True)
    (imgs / "kept.png").write_bytes(b"\x89PNG\r\n\x1a\nx")
    images.write_sidecar(
        slug_dir,
        [
            {
                "local": "kept.png",
                "source_url": "https://x.example/kept.png",
                "etag": '"k"',
                "last_modified": None,
                "sha256": "abc",
                "bytes": 11,
            }
        ],
    )

    orchestrate.run_ingest("https://x.example/manual", replace=True)

    assert (imgs / "kept.png").exists(), "--replace discarded the same manual's image cache"
    assert [r["local"] for r in images.read_sidecar(slug_dir)] == ["kept.png"]


def test_reingest_of_a_different_local_file_refuses_to_take_the_slug_over(tmp_path, monkeypatch):
    """``canonical_url`` is "" off http, and api_spec slugs every vendor's openapi.json alike."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("/specs/vendor-a/openapi.json")

    slug_dir = tmp_path / "incoming" / "fakeapp"
    before = (slug_dir / "fakeapp.html").read_text(encoding="utf-8")

    with pytest.raises(InvalidInputError) as exc:
        orchestrate.run_ingest("/specs/vendor-b/openapi.json")

    assert "vendor-a" in str(exc.value), "the refusal must name what it protected"
    assert (slug_dir / "fakeapp.html").read_text(encoding="utf-8") == before
    assert manifest.read_manifest(slug_dir)["source_url"] == "/specs/vendor-a/openapi.json"


def test_reingest_of_the_same_local_file_is_allowed(tmp_path, monkeypatch):
    """The local-source compare must not refuse a legitimate re-ingest of the same file."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("file:///specs/openapi.json")

    orchestrate.run_ingest("file:///specs/openapi.json")

    m = manifest.read_manifest(tmp_path / "incoming" / "fakeapp")
    assert m["source_url"] == "file:///specs/openapi.json"


def test_reingest_of_the_same_local_file_respelled_is_allowed(tmp_path, monkeypatch):
    """Shell completion drops ``./`` from ``./openapi.json``; that must not need ``--replace``."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    spec = tmp_path / "openapi.json"
    spec.write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    orchestrate.run_ingest("./openapi.json")

    orchestrate.run_ingest("openapi.json")
    orchestrate.run_ingest(str(spec))
    orchestrate.run_ingest(spec.as_uri())

    m = manifest.read_manifest(tmp_path / "incoming" / "fakeapp")
    assert m["source_url"] == spec.as_uri()


def test_reingest_refuses_a_slug_dir_holding_content_with_no_manifest(tmp_path, monkeypatch):
    """A manifest-less slug dir, which status, refresh and audit accept, may be the only copy."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    slug_dir = tmp_path / "incoming" / "fakeapp"
    slug_dir.mkdir(parents=True)
    (slug_dir / "legacy-manual.html").write_text("<h1>Legacy</h1>", encoding="utf-8")

    with pytest.raises(InvalidInputError) as exc:
        orchestrate.run_ingest("https://x")

    assert "no readable manifest" in str(exc.value)
    assert (slug_dir / "legacy-manual.html").exists(), "legacy deliverable deleted"


def test_reingest_refuses_a_slug_dir_whose_manifest_is_corrupt(tmp_path, monkeypatch):
    """`read_manifest` is tolerant by design and answers None for unparseable JSON
    too, so the guard cannot read that as 'nothing here'."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    slug_dir = tmp_path / "incoming" / "fakeapp"
    slug_dir.mkdir(parents=True)
    (slug_dir / "fakeapp.html").write_text("<h1>Held</h1>", encoding="utf-8")
    (slug_dir / manifest.MANIFEST_NAME).write_text('{"source_url": "https://a', encoding="utf-8")

    with pytest.raises(InvalidInputError):
        orchestrate.run_ingest("https://x")

    assert (slug_dir / "fakeapp.html").read_text(encoding="utf-8") == "<h1>Held</h1>"


def test_reingest_into_an_empty_leftover_slug_dir_proceeds(tmp_path, monkeypatch):
    """An empty dir holds no manual; refusing it would be a false alarm."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    (tmp_path / "incoming" / "fakeapp").mkdir(parents=True)

    res = orchestrate.run_ingest("https://x")

    assert res["slug"] == "fakeapp"
    assert (tmp_path / "incoming" / "fakeapp" / "fakeapp.html").exists()


def test_the_refusal_never_tells_the_user_to_pass_the_slug_they_passed(tmp_path, monkeypatch):
    """`ingest --slug foo` onto an occupied `foo` is the commonest deliberate
    collision; the advice must not name the flag the user just used."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://vendor-a.example/manual")  # slug from the pattern
    orchestrate.run_ingest("https://vendor-a.example/manual", slug_override="shared")

    with pytest.raises(InvalidInputError) as passed_slug:
        orchestrate.run_ingest("https://vendor-b.example/manual", slug_override="shared")

    assert "give this source its own directory" not in str(passed_slug.value)
    assert "--replace" in str(passed_slug.value)

    with pytest.raises(InvalidInputError) as derived_slug:
        orchestrate.run_ingest("https://vendor-b.example/manual")

    assert "give this source its own directory" in str(derived_slug.value)


def test_replace_takes_over_a_slug_dir_with_no_readable_manifest(tmp_path, monkeypatch):
    """The escape hatch has to cover the manifest-less case, or a slug without a
    manifest can never be re-ingested."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    slug_dir = tmp_path / "incoming" / "fakeapp"
    slug_dir.mkdir(parents=True)
    (slug_dir / "legacy-manual.html").write_text("<h1>Legacy</h1>", encoding="utf-8")

    orchestrate.run_ingest("https://x", replace=True)

    assert not (slug_dir / "legacy-manual.html").exists()
    assert manifest.read_manifest(slug_dir)["source_url"] == "https://x"


@pytest.mark.parametrize(
    ("held", "new", "same"),
    [
        ("https://a.com/m", "https://a.com/m", True),
        ("https://a.com/m", "http://www.a.com/m/#x", True),  # respelled remote
        ("https://a.com/m", "https://b.com/m", False),
        ("file:///u/a/spec.json", "file:///u/a/spec.json", True),
        ("file:///u/a/spec.json", "file:///u/b/spec.json", False),
        ("./a.json", "./b.json", False),
        ("https://a.com/m", "./b.json", False),  # remote vs local
        ("/u/a/spec.json", "file:///u/a/spec.json", True),  # one file, two spellings
        ("./a.json", "a.json", True),
        ("mailto:a@b.example", "mailto:c@d.example", False),  # neither remote nor local
    ],
)
def test_same_source_truth_table(held, new, same):
    """Local sources compare by resolved path: canonically every spec matches, and as raw strings
    one file differs from itself."""
    assert _staging._same_source(held, new) is same


def test_reingest_refuses_a_manifest_missing_its_source_url(tmp_path, monkeypatch):
    """Valid JSON without the key refuses rather than crashing the guard, which runs
    before the --replace check — a crash leaves the slug un-reingestable."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    slug_dir = tmp_path / "incoming" / "fakeapp"
    slug_dir.mkdir(parents=True)
    (slug_dir / "fakeapp.html").write_text("<h1>Held</h1>", encoding="utf-8")
    (slug_dir / manifest.MANIFEST_NAME).write_text('{"pages": 3}', encoding="utf-8")

    with pytest.raises(InvalidInputError) as exc:
        orchestrate.run_ingest("https://x")
    assert "no readable manifest" in str(exc.value)

    orchestrate.run_ingest("https://x", replace=True)  # the escape hatch must work
    assert manifest.read_manifest(slug_dir)["source_url"] == "https://x"


def test_reingest_refuses_a_manifest_that_is_not_a_dict(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    slug_dir = tmp_path / "incoming" / "fakeapp"
    slug_dir.mkdir(parents=True)
    (slug_dir / "fakeapp.html").write_text("<h1>Held</h1>", encoding="utf-8")
    (slug_dir / manifest.MANIFEST_NAME).write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(InvalidInputError):
        orchestrate.run_ingest("https://x")
    assert (slug_dir / "fakeapp.html").exists()


def test_reingest_refuses_a_manifest_whose_source_url_is_not_a_string(tmp_path, monkeypatch):
    """Nothing validates a hand-edited manifest, and the compare must refuse the
    junk it finds rather than crash the ingest."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    slug_dir = tmp_path / "incoming" / "fakeapp"
    slug_dir.mkdir(parents=True)
    (slug_dir / "fakeapp.html").write_text("<h1>Held</h1>", encoding="utf-8")
    (slug_dir / manifest.MANIFEST_NAME).write_text('{"source_url": 123}', encoding="utf-8")

    with pytest.raises(InvalidInputError):
        orchestrate.run_ingest("https://x")

    assert (slug_dir / "fakeapp.html").exists()


def test_reingest_refuses_a_held_url_with_an_invalid_port(tmp_path, monkeypatch):
    """An unparseable held URL (canonical "") refuses rather than reading as no source;
    ``--replace`` gets through."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    slug_dir = tmp_path / "incoming" / "fakeapp"
    slug_dir.mkdir(parents=True)
    (slug_dir / "fakeapp.html").write_text("<h1>Held</h1>", encoding="utf-8")
    orchestrate.run_ingest("https://x", replace=True)
    m = manifest.read_manifest(slug_dir)
    m["source_url"] = "https://example.com:99999/x"
    manifest.write_manifest(slug_dir, m)

    with pytest.raises(InvalidInputError):
        orchestrate.run_ingest("https://y")

    orchestrate.run_ingest("https://y", replace=True)
    assert manifest.read_manifest(slug_dir)["source_url"] == "https://y"


def test_a_relative_local_source_is_recorded_absolutely(tmp_path, monkeypatch):
    """A relative source_url would name a different file wherever refresh runs."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    vendor_a = tmp_path / "vendor-a"
    vendor_a.mkdir()
    (vendor_a / "openapi.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(vendor_a)

    orchestrate.run_ingest("./openapi.json")

    recorded = manifest.read_manifest(tmp_path / "incoming" / "fakeapp")["source_url"]
    assert recorded == str(vendor_a / "openapi.json"), f"stored CWD-relative: {recorded!r}"

    # A same-named spec in another directory is a DIFFERENT manual and must refuse.
    vendor_b = tmp_path / "vendor-b"
    vendor_b.mkdir()
    (vendor_b / "openapi.json").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(vendor_b)

    with pytest.raises(InvalidInputError) as exc:
        orchestrate.run_ingest("./openapi.json")
    assert "vendor-a" in str(exc.value), "the refusal must name what it protected"


class _SizedPattern(_FakePattern):
    """Fake whose crawl size and normalized body change between ingests."""

    def __init__(self, pages, body):
        self.pages = pages
        self.body = body

    def acquire(self, url, workdir):
        acq = super().acquire(url, workdir)
        acq.pages = self.pages
        return acq

    def normalize(self, acq, workdir):
        clean = workdir / f"{acq.slug}.html"
        clean.write_text(self.body, encoding="utf-8")
        return clean


def test_a_collapsed_recrawl_is_refused_and_keeps_the_staged_manual(tmp_path, monkeypatch):
    """A source that changed shape still normalizes to a non-empty shell; staging it
    clears the manual it replaces."""
    p = _SizedPattern(pages=200, body="full guide")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://docs.example.com/")
    p.pages, p.body = 2, "welcome only"

    with pytest.raises(InvalidInputError, match="found 2 of the 200 pages"):
        orchestrate.run_ingest("https://docs.example.com/")

    slug_dir = tmp_path / "incoming" / "fakeapp"
    assert (slug_dir / "fakeapp.html").read_text(encoding="utf-8") == "full guide"
    assert manifest.read_manifest(slug_dir)["pages"] == 200


def test_a_collapsed_single_fetch_is_refused_as_a_document_not_a_crawl(tmp_path, monkeypatch):
    """A stub PDF replacing a manual is a real collapse, but nothing was crawled."""
    p = _SizedPattern(pages=200, body="full guide")
    p.single_fetch = True
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://docs.example.com/manual.pdf")
    p.pages, p.body = 2, "this manual has moved"

    with pytest.raises(InvalidInputError) as exc:
        orchestrate.run_ingest("https://docs.example.com/manual.pdf")

    message = str(exc.value)
    assert "crawl" not in message
    assert "2" in message and "200" in message
    assert "--slug fakeapp --replace" in message


@pytest.mark.parametrize("keep_raw", [True, False])
def test_the_collapse_hint_keeps_raw_only_when_the_slug_holds_it(tmp_path, monkeypatch, keep_raw):
    """A --replace re-ingest without --keep-raw deletes the raw/ the slug holds."""
    p = _SizedPattern(pages=200, body="full guide")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://docs.example.com/", keep_raw=keep_raw)
    p.pages, p.body = 2, "welcome only"

    with pytest.raises(InvalidInputError) as exc:
        orchestrate.run_ingest("https://docs.example.com/")

    assert ("--replace --keep-raw" in str(exc.value)) is keep_raw


def test_replace_accepts_a_collapsed_recrawl(tmp_path, monkeypatch):
    p = _SizedPattern(pages=200, body="full guide")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://docs.example.com/")
    p.pages, p.body = 2, "smaller edition"

    res = orchestrate.run_ingest("https://docs.example.com/", replace=True)

    assert res["pages"] == 2
    staged = tmp_path / "incoming" / "fakeapp" / "fakeapp.html"
    assert staged.read_text(encoding="utf-8") == "smaller edition"


@pytest.mark.parametrize(
    ("before", "after"),
    [(9, 1), (200, 100), (200, None), (None, 1)],
    ids=["small-manual", "half-kept", "unknown-now", "unknown-before"],
)
def test_the_guard_leaves_small_modest_and_unknown_counts_alone(
    tmp_path, monkeypatch, before, after
):
    p = _SizedPattern(pages=before, body="v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://docs.example.com/")
    p.pages, p.body = after, "v2"

    assert orchestrate.run_ingest("https://docs.example.com/")["changed"] is True


def test_a_zero_keep_pct_disables_the_guard(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate.cfg, "COLLAPSE_KEEP_PCT", 0)
    p = _SizedPattern(pages=200, body="v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://docs.example.com/")
    p.pages, p.body = 1, "v2"

    assert orchestrate.run_ingest("https://docs.example.com/")["changed"] is True


def test_a_capped_recrawl_does_not_replace_a_complete_larger_manual(tmp_path, monkeypatch):
    """A crawl that stopped at its page cap proves nothing about the source shrinking,
    even when it kept more than COLLAPSE_KEEP_PCT of the staged pages."""
    p = _SizedPattern(pages=1500, body="all 1500 pages")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://docs.example.com/")

    def capped(url, workdir):
        acq = _SizedPattern.acquire(p, url, workdir)
        acq.truncated = True
        return acq

    p.pages, p.body = 1000, "first 1000 pages"
    monkeypatch.setattr(p, "acquire", capped)

    with pytest.raises(InvalidInputError, match="page cap with 1000 pages") as exc:
        orchestrate.run_ingest("https://docs.example.com/")

    assert "--slug fakeapp --replace" in str(exc.value)
    staged = tmp_path / "incoming" / "fakeapp" / "fakeapp.html"
    assert staged.read_text(encoding="utf-8") == "all 1500 pages"


def test_a_capped_recrawl_may_replace_a_manual_that_was_capped_too(tmp_path, monkeypatch):
    p = _SizedPattern(pages=1000, body="v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://docs.example.com/")
    slug_dir = tmp_path / "incoming" / "fakeapp"
    m = manifest.read_manifest(slug_dir)
    m["truncated"] = True
    manifest.write_manifest(slug_dir, m)

    def capped(url, workdir):
        acq = _SizedPattern.acquire(p, url, workdir)
        acq.truncated = True
        return acq

    p.body = "v2"
    monkeypatch.setattr(p, "acquire", capped)

    assert orchestrate.run_ingest("https://docs.example.com/")["changed"] is True


def _localized_slug(tmp_path, monkeypatch):
    p = _RemoteImagePattern("v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    _mock_image_fetch(monkeypatch)
    orchestrate.run_ingest("https://x", download_images=True)
    return p, tmp_path / "incoming" / "fakeapp"


@pytest.mark.parametrize(
    "exc",
    [OSError(28, "No space left on device"), KeyboardInterrupt()],
    ids=["disk-full", "killed"],
)
def test_a_failed_restage_never_labels_the_old_file_with_the_new_record(tmp_path, monkeypatch, exc):
    """The old localized file must not survive under a manifest describing content that
    never landed — the next refresh would call it unchanged."""
    p, slug_dir = _localized_slug(tmp_path, monkeypatch)
    p.prefix = "v2"
    with pytest.MonkeyPatch.context() as mp, pytest.raises(type(exc)):
        _die_writing_the_deliverable(mp, exc)
        orchestrate.run_ingest("https://x", if_changed=True)

    res = orchestrate.run_ingest("https://x", if_changed=True)

    assert res["changed"] is True
    assert "<h1>v2</h1>" in (slug_dir / "fakeapp.html").read_text(encoding="utf-8")


def test_a_restage_killed_before_its_record_is_restaged_again(tmp_path, monkeypatch):
    p, slug_dir = _localized_slug(tmp_path, monkeypatch)
    p.prefix = "v2"
    real_stage = orchestrate._stage_file

    def stage_then_die(src, dst):
        real_stage(src, dst)
        raise KeyboardInterrupt

    with pytest.MonkeyPatch.context() as mp, pytest.raises(KeyboardInterrupt):
        mp.setattr(orchestrate, "_stage_file", stage_then_die)
        orchestrate.run_ingest("https://x", if_changed=True)
    p.prefix = "v1"

    res = orchestrate.run_ingest("https://x", if_changed=True)

    assert res["changed"] is True
    assert "<h1>v1</h1>" in (slug_dir / "fakeapp.html").read_text(encoding="utf-8")


def test_an_interrupted_localize_keeps_the_integrity_record(tmp_path, monkeypatch):
    from pagespring import _image_cache, audit

    _p, slug_dir = _localized_slug(tmp_path, monkeypatch)
    recorded = manifest.read_manifest(slug_dir)["localized_sha256"]

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    with pytest.MonkeyPatch.context() as mp, pytest.raises(KeyboardInterrupt):
        mp.setattr(_image_cache, "reuse_unchanged", interrupted)
        localize.localize_images("fakeapp")

    assert manifest.read_manifest(slug_dir)["localized_sha256"] == recorded
    deliverable = slug_dir / "fakeapp.html"
    deliverable.write_text(deliverable.read_text(encoding="utf-8")[:12], encoding="utf-8")
    assert ("sha_mismatch", "error") in [
        (f["check"], f["level"]) for f in audit.audit_slug("fakeapp")
    ]
    with pytest.raises(PreconditionError):
        localize.localize_images("fakeapp")


def test_renormalize_restoring_a_damaged_file_keeps_the_image_cache(tmp_path, monkeypatch):
    """A byte-identical replay names the same image URLs the cache was fetched from;
    those files may be the only copies of images behind expired tokens."""
    p = _RawDrivenPattern(prefix="v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://x", keep_raw=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"
    (slug_dir / "images").mkdir()
    (slug_dir / "images" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\ncached")
    (slug_dir / "fakeapp.html").write_text("v1:<ht", encoding="utf-8")
    monkeypatch.setattr(renormalize, "pattern_by_name", lambda name: p)

    res = renormalize.run_renormalize("fakeapp")

    assert res["changed"] is True
    assert (slug_dir / "images" / "a.png").read_bytes() == b"\x89PNG\r\n\x1a\ncached"
