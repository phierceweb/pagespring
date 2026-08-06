"""Ingest orchestration (mocked pattern; no network).

The clean download stages to ``incoming/<slug>/``; an autouse fixture points
that at a tmp dir so tests never write into the real repo. The pagespring's job
ends at ``incoming/`` — conversion into ``manuals/`` is a separate concern it
neither runs nor knows about.
"""

import pathlib
import re
import urllib.error

import pytest
from pf_core.exceptions import ClientError, InvalidInputError, PreconditionError

from pagespring import http, manifest, orchestrate
from pagespring.base import AcquireResult
from pagespring.patterns.docs_probe import DocsProbePattern


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
    """A re-run leaves only the fresh deliverable — no orphaned clean files and a
    fresh raw/. The image cache is the deliberate exception (see
    test_reingest_preserves_images_and_sidecar): re-downloading every image on
    every refresh costs far more than leaving unreferenced files on disk."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    slug_dir = tmp_path / "incoming" / "fakeapp"
    (slug_dir / "raw").mkdir(parents=True)
    (slug_dir / "images").mkdir()
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
    """A body the fetch core refused to trust (corrupt gzip, over the size cap)
    leaves acquire as ClientError — it must reach the CLI as AcquireError too,
    not as an unhandled traceback."""
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
    monkeypatch.setattr(
        http,
        "fetch_bytes_meta",
        lambda u, **k: (u, b"\x89PNG\r\n\x1a\nx", {"etag": None, "last_modified": None}),
    )
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)

    res = orchestrate.localize_images("bk")

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

    res = orchestrate.localize_images("bk")

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
        orchestrate.localize_images("bk")


class _RawDrivenPattern(_FakePattern):
    """A fake whose normalize derives its output from raw/ contents — so a
    renormalize replay visibly reflects both the kept raw and the current
    normalize code (``prefix``)."""

    def __init__(self, prefix: str = "v1"):
        self.prefix = prefix

    def normalize(self, acq, workdir):
        body = (acq.raw_dir / "welcome.html").read_text(encoding="utf-8")
        clean = workdir / f"{acq.slug}.html"
        clean.write_text(f"{self.prefix}:{body}", encoding="utf-8")
        return clean


def test_renormalize_replays_from_kept_raw_without_network(tmp_path, monkeypatch):
    """renormalize re-runs the pattern's CURRENT normalize against the kept
    raw/ and re-stages the deliverable — no acquire, no re-crawl. The kept
    raw/ survives for the next replay."""
    p = _RawDrivenPattern(prefix="v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://x", keep_raw=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"
    assert (slug_dir / "fakeapp.html").read_text(encoding="utf-8") == "v1:<html></html>"

    p.prefix = "v2"  # the normalize code changed; raw did not

    def _no_acquire(url, workdir):  # pragma: no cover - proves replay skips acquire
        raise AssertionError("renormalize must not acquire")

    monkeypatch.setattr(p, "acquire", _no_acquire)
    monkeypatch.setattr(orchestrate, "pattern_by_name", lambda name: p if name == "fake" else None)

    res = orchestrate.run_renormalize("fakeapp")

    assert res["changed"] is True
    assert res["pattern"] == "fake"
    assert res["slug"] == "fakeapp"
    assert (slug_dir / "fakeapp.html").read_text(encoding="utf-8") == "v2:<html></html>"
    assert (slug_dir / "raw" / "welcome.html").exists()  # raw kept for the next replay


def test_renormalize_unchanged_output_leaves_slug_dir_untouched(tmp_path, monkeypatch):
    """A replay whose output is byte-identical to the staged deliverable
    reports changed=False and re-stages nothing — deliverable mtime and any
    localized images/ stay exactly as they were (the refactor-was-safe signal)."""
    p = _RawDrivenPattern(prefix="v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://x", keep_raw=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"
    deliverable = slug_dir / "fakeapp.html"
    before_mtime = deliverable.stat().st_mtime_ns
    (slug_dir / "images").mkdir()
    (slug_dir / "images" / "a.png").write_bytes(b"png")

    monkeypatch.setattr(orchestrate, "pattern_by_name", lambda name: p)
    res = orchestrate.run_renormalize("fakeapp")

    assert res["changed"] is False
    assert deliverable.stat().st_mtime_ns == before_mtime
    assert (slug_dir / "images" / "a.png").read_bytes() == b"png"


def test_renormalize_without_manifest_raises(tmp_path):
    """A slug never ingested (no manifest) is a precondition failure."""
    (tmp_path / "incoming" / "bk").mkdir(parents=True)
    with pytest.raises(PreconditionError, match="ingest it first"):
        orchestrate.run_renormalize("bk")


def test_renormalize_without_kept_raw_raises(tmp_path, monkeypatch):
    """An ingest without --keep-raw left no raw/ to replay — the error says how
    to enable the replay, and the staged deliverable is untouched."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://x")  # no keep_raw
    with pytest.raises(PreconditionError, match="--keep-raw"):
        orchestrate.run_renormalize("fakeapp")
    assert (tmp_path / "incoming" / "fakeapp" / "fakeapp.html").exists()


def test_renormalize_empty_output_fails_and_preserves_previous(tmp_path, monkeypatch):
    """A replay that normalizes to nothing hard-fails BEFORE staging — the
    staged deliverable and manifest survive (same invariant as ingest)."""
    p = _RawDrivenPattern(prefix="v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://x", keep_raw=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"

    monkeypatch.setattr(orchestrate, "pattern_by_name", lambda name: _EmptyPattern())
    with pytest.raises(orchestrate.EmptyOutputError):
        orchestrate.run_renormalize("fakeapp")

    assert (slug_dir / "fakeapp.html").read_text(encoding="utf-8") == "v1:<html></html>"
    assert manifest.read_manifest(slug_dir)["sha256"] == manifest.sha256_file(
        slug_dir / "fakeapp.html"
    )


def test_renormalize_unknown_pattern_raises(tmp_path, monkeypatch):
    """A manifest naming a pattern that is no longer registered fails with the
    pattern's name (renamed/removed since the ingest)."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://x", keep_raw=True)
    monkeypatch.setattr(orchestrate, "pattern_by_name", lambda name: None)
    with pytest.raises(PreconditionError, match="fake"):
        orchestrate.run_renormalize("fakeapp")


def test_renormalize_updates_manifest_and_resets_image_count(tmp_path, monkeypatch):
    """A changed replay refreshes the deliverable's facts (sha256, bytes) and
    resets the localized-image count — the new file's refs are absolute again.
    Provenance of the crawl (source_url, ingested_at, pattern, pages) is
    untouched."""
    p = _RawDrivenPattern(prefix="v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://docs.example.com/foo", keep_raw=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"
    before = manifest.read_manifest(slug_dir)

    p.prefix = "v2"
    monkeypatch.setattr(orchestrate, "pattern_by_name", lambda name: p)
    res = orchestrate.run_renormalize("fakeapp")

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
    """A changed replay removes images/ — its files were named for the OLD
    deliverable's refs, and localize seeds its collision set from the dir, so
    stale files would force every re-download onto a suffixed name and orphan
    the originals. Same principle as ingest's replace: no stale artifacts."""
    p = _RawDrivenPattern(prefix="v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://docs.example.com/foo", keep_raw=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"
    (slug_dir / "images").mkdir()
    (slug_dir / "images" / "a.png").write_bytes(b"png")

    p.prefix = "v2"
    monkeypatch.setattr(orchestrate, "pattern_by_name", lambda name: p)
    res = orchestrate.run_renormalize("fakeapp")

    assert res["changed"] is True
    assert not (slug_dir / "images").exists()
    assert (slug_dir / "raw" / "welcome.html").exists()  # raw untouched — it is the input


def test_ingest_warns_when_content_duplicates_another_slug(tmp_path, monkeypatch):
    """The same manual ingested from a second URL (different slug) is flagged:
    result carries duplicate_of naming the existing slug. Still staged — the
    duplicate might be deliberate; the warning is the product."""
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
    """A pattern that names its output during acquire (pdf_url writes
    raw/<url-slug>.pdf) can't know about --slug — staging renames centrally,
    so the documented incoming/<slug>/<slug>.<ext> shape always holds."""

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
    """The manifest records acquire's title, and a replay feeds it back into
    normalize — an unchanged pattern therefore reproduces byte-identical output
    (changed=False) instead of degrading the heading to the slug."""
    p = _TitledPattern()
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://x", keep_raw=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"
    assert manifest.read_manifest(slug_dir)["title"] == "Fake App Guide"

    monkeypatch.setattr(orchestrate, "pattern_by_name", lambda name: p)
    res = orchestrate.run_renormalize("fakeapp")

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

    monkeypatch.setattr(orchestrate, "pattern_by_name", lambda name: _MarkdownEmittingPattern())
    res = orchestrate.run_renormalize("fakeapp")

    assert res["changed"] is True
    assert not (slug_dir / "fakeapp.html").exists()
    assert (slug_dir / "fakeapp.md").read_text(encoding="utf-8") == "# now markdown"
    assert manifest.read_manifest(slug_dir)["deliverable"] == "fakeapp.md"


def test_localize_images_reuses_unchanged_before_downloading(tmp_path, monkeypatch):
    """On a refreshed deliverable the image URLs come back the same. An image the
    sidecar already holds, and the server reports unchanged, must be re-pointed
    locally instead of re-downloaded."""
    from pagespring import images

    slug_dir = tmp_path / "incoming" / "bk"
    _write_manifest(slug_dir)
    (slug_dir / "bk.html").write_text(
        '<img src="https://x.com/a.png"><img src="https://x.com/new.png">', encoding="utf-8"
    )
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

    res = orchestrate.localize_images("bk")

    assert res["reused"] == 1
    assert fetched == ["https://x.com/new.png"]  # the unchanged image never re-downloaded
    assert (imgs / "a.png").read_bytes() == b"\x89PNG\r\n\x1a\nold"  # original kept
    assert res["remaining"] == 0


def test_reingest_preserves_images_and_sidecar(tmp_path, monkeypatch):
    """A re-ingest replaces the deliverable but must KEEP images/ and images.json.

    Wiping them defeats the sidecar entirely: a refresh brings the same image
    URLs back, and with no local files or validators every image is re-downloaded.
    Stale clean files and raw/ are still cleared — only the image cache survives.
    """
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

    res = orchestrate.localize_images("bk")

    assert res["pruned"] == 1
    assert sorted(p.name for p in imgs.glob("*")) == ["keep.png"]
    assert res["images_total"] == 1  # manifest counts what survives, not orphans
    assert manifest.read_manifest(slug_dir)["images"] == 1


def test_manifest_records_whether_raw_was_kept(tmp_path, monkeypatch):
    """`renormalize` needs raw/, and nothing outside the directory listing says
    whether it is there — so a later image or normalize change can't be planned
    without guessing which slugs replay for free and which need a re-crawl."""
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
    """`pdf_url.normalize` hands back the downloaded file unchanged, so a replay
    can only ever produce identical bytes — raw/ would be a second copy of the
    deliverable. Across the corpus that is 358 MB duplicated for no capability."""

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
    """A PDF has no text refs to re-point, and reading it as UTF-8 raises —
    an exception the CLI's PreconditionError handler does not catch, so
    `localize --all` died on the first PDF. 71 of 99 corpus slugs are PDFs,
    and the alphabetically first one is, so the sweep never reached any HTML.
    """
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://x")
    slug_dir = tmp_path / "incoming" / "fakeapp"
    (slug_dir / "fakeapp.html").unlink()
    (slug_dir / "fakeapp.pdf").write_bytes(b"%PDF-1.7 \xe2\xe2 raw binary")
    m = manifest.read_manifest(slug_dir)
    assert m is not None
    m["kind"], m["deliverable"] = "pdf", "fakeapp.pdf"
    manifest.write_manifest(slug_dir, m)

    r = orchestrate.localize_images("fakeapp")

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
    """A second --download-images ingest of the same source must leave ONE file
    per image. Downloading without the reuse probe re-fetched every image onto a
    suffixed name (logo-2.png), stranding the previous run's copy — so the image
    set grew on every refresh."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _RemoteImagePattern())
    _mock_image_fetch(monkeypatch)

    orchestrate.run_ingest("https://x", download_images=True)
    res = orchestrate.run_ingest("https://x", download_images=True)

    slug_dir = tmp_path / "incoming" / "fakeapp"
    assert sorted(p.name for p in (slug_dir / "images").iterdir()) == ["logo.png"]
    assert res["images"] == 1
    # the deliverable points at the file that is actually there
    assert 'src="images/logo.png"' in (slug_dir / "fakeapp.html").read_text(encoding="utf-8")


def test_ingest_records_the_localized_sha(tmp_path, monkeypatch):
    """The image pass re-points refs, so the staged sha no longer describes the
    file on disk — without the post-pass hash the deliverable carries no
    integrity record at all."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _RemoteImagePattern())
    _mock_image_fetch(monkeypatch)

    orchestrate.run_ingest("https://x", download_images=True)

    slug_dir = tmp_path / "incoming" / "fakeapp"
    m = manifest.read_manifest(slug_dir)
    assert m["localized_sha256"] == manifest.sha256_file(slug_dir / "fakeapp.html")
    assert m["localized_sha256"] != m["sha256"]


def test_renormalize_clears_the_localized_sha(tmp_path, monkeypatch):
    """A replay re-stages a deliverable whose refs are absolute again, so the
    post-localize hash is reset alongside the image count — left standing, it
    would make audit report a permanent sha_mismatch."""
    p = _RemoteImagePattern(prefix="v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    _mock_image_fetch(monkeypatch)
    orchestrate.run_ingest("https://x", keep_raw=True, download_images=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"
    assert manifest.read_manifest(slug_dir)["localized_sha256"] is not None

    p.prefix = "v2"
    monkeypatch.setattr(orchestrate, "pattern_by_name", lambda name: p)
    res = orchestrate.run_renormalize("fakeapp")

    assert res["changed"] is True
    after = manifest.read_manifest(slug_dir)
    assert after["localized_sha256"] is None
    assert after["images"] == 0
    assert after["sha256"] == manifest.sha256_file(slug_dir / "fakeapp.html")


class _HostilePattern(_FakePattern):
    """A pattern whose slug escapes ``incoming/``.

    Not hypothetical: ``openstax._slug``, ``microsoft_support._slug`` and
    ``apple_help._parse_apple_url`` all return ``".."`` for a URL whose path
    carries a ``..`` segment.
    """

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
    """A pattern-derived slug is sanitized like ``--slug`` is.

    ``incoming/..`` is the repo root, and re-ingest wipes its target with
    ``shutil.rmtree`` — so an unsanitized slug turns one ingest into a
    recursive delete of everything outside the corpus.

    The invariant is not "every odd slug is refused": one that folds to a safe
    component (``a/../..`` -> ``a``) may proceed. It is that the run either
    refuses outright or writes strictly inside ``incoming/`` — and either way
    touches nothing above it.
    """
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

    monkeypatch.setattr(orchestrate, "_image_pass", die)

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


def test_a_reingest_keeps_the_old_manifest_until_the_new_one_replaces_it(tmp_path, monkeypatch):
    """The clear-before-restage must not take the manifest with it: an ingest that
    dies before writing leaves the previous record, which `refresh` can act on."""
    monkeypatch.setattr(orchestrate, "classify", lambda url: _FakePattern())
    orchestrate.run_ingest("https://first")

    slug_dir = tmp_path / "incoming" / "fakeapp"
    assert manifest.read_manifest(slug_dir)["source_url"] == "https://first"

    def die_after_clear(src, dst, *args, **kwargs):
        raise OSError("killed just after the clear")

    monkeypatch.setattr(orchestrate.shutil, "copy2", die_after_clear)
    with pytest.raises(OSError):
        orchestrate.run_ingest("https://second")

    survived = manifest.read_manifest(slug_dir)
    assert survived is not None, "the clear destroyed the manifest before restaging"
    assert survived["source_url"] == "https://first"
