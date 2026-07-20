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
    """A changed replay refreshes the deliverable's facts (sha256, bytes,
    convert_recipe from the CURRENT pattern) and resets the localized-image
    count — the new file's refs are absolute again. Provenance of the crawl
    (source_url, ingested_at, pattern, pages) is untouched."""
    p = _RawDrivenPattern(prefix="v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://docs.example.com/foo", keep_raw=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"
    before = manifest.read_manifest(slug_dir)

    p.prefix = "v2"
    p.convert_recipe = ["--split-sections", "--normalize-headings"]
    monkeypatch.setattr(orchestrate, "pattern_by_name", lambda name: p)
    res = orchestrate.run_renormalize("fakeapp")

    assert res["changed"] is True
    after = manifest.read_manifest(slug_dir)
    assert after["sha256"] == manifest.sha256_file(slug_dir / "fakeapp.html")
    assert after["sha256"] != before["sha256"]
    assert after["bytes"] == len("v2:<html></html>")
    assert after["convert_recipe"] == ["--split-sections", "--normalize-headings"]
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
