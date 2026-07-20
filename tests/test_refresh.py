"""refresh — the corpus-maintenance sweep over incoming/ (mocked patterns; no
network). Re-ingests each slug from its manifest's source_url with --if-changed
semantics; single-fetch patterns with stored validators get a conditional-GET
fast path."""

import pytest

from pagespring import manifest, orchestrate, refresh
from pagespring.base import AcquireResult


@pytest.fixture(autouse=True)
def _incoming_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate.cfg, "INCOMING_DIR", str(tmp_path / "incoming"))


class _BodyPattern:
    """Fake whose normalized content varies with ``body`` between ingests."""

    name = "fake"
    convert_recipe = ["--split-sections"]

    def __init__(self, body: str = "v1"):
        self.body = body

    def match(self, url):
        return True

    def acquire(self, url, workdir):
        raw = workdir / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "welcome.html").write_text("<html></html>", encoding="utf-8")
        return AcquireResult(raw_dir=raw, kind="html", slug="fakeapp", pages=1)

    def normalize(self, acq, workdir):
        clean = workdir / f"{acq.slug}.html"
        clean.write_text(self.body, encoding="utf-8")
        return clean


def test_refresh_slug_reingests_from_manifest_source_url(tmp_path, monkeypatch):
    """refresh re-crawls the slug's recorded source_url; changed content is
    re-staged and reported as changed."""
    p = _BodyPattern("v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://docs.example.com/foo")
    slug_dir = tmp_path / "incoming" / "fakeapp"

    p.body = "v2"  # the source changed since the ingest
    seen: list[str] = []
    real_classify = lambda url: (seen.append(url), p)[1]  # noqa: E731
    monkeypatch.setattr(orchestrate, "classify", real_classify)

    out = refresh.refresh_slug("fakeapp")

    assert out["status"] == "changed"
    assert out["slug"] == "fakeapp"
    assert seen == ["https://docs.example.com/foo"]  # crawled the RECORDED url
    assert (slug_dir / "fakeapp.html").read_text(encoding="utf-8") == "v2"


def test_refresh_preserves_kept_raw_property(tmp_path, monkeypatch):
    """A slug ingested with --keep-raw keeps that property across a changed
    refresh: the NEW crawl's raw/ is kept (renormalize stays possible)."""
    p = _BodyPattern("v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://x", keep_raw=True)
    slug_dir = tmp_path / "incoming" / "fakeapp"
    assert (slug_dir / "raw").is_dir()

    p.body = "v2"
    out = refresh.refresh_slug("fakeapp")

    assert out["status"] == "changed"
    assert (slug_dir / "raw" / "welcome.html").exists()  # new crawl's raw kept


def test_refresh_slug_without_manifest_is_skipped(tmp_path):
    """A legacy dir with no manifest can't be refreshed — skipped, not fatal."""
    (tmp_path / "incoming" / "legacy").mkdir(parents=True)
    out = refresh.refresh_slug("legacy")
    assert out["status"] == "skipped"
    assert "manifest" in out["detail"]


class _DeadSourcePattern(_BodyPattern):
    def acquire(self, url, workdir):
        import urllib.error

        raise urllib.error.URLError("connection refused")


def test_refresh_dead_source_fails_softly_and_preserves_deliverable(tmp_path, monkeypatch):
    """A source that no longer answers is a failed outcome, not an exception —
    and the staged deliverable survives untouched."""
    p = _BodyPattern("v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://x")
    slug_dir = tmp_path / "incoming" / "fakeapp"

    monkeypatch.setattr(orchestrate, "classify", lambda url: _DeadSourcePattern())
    out = refresh.refresh_slug("fakeapp")

    assert out["status"] == "failed"
    assert "connection refused" in out["detail"]
    assert (slug_dir / "fakeapp.html").read_text(encoding="utf-8") == "v1"


class _RenamedSlugPattern(_BodyPattern):
    def acquire(self, url, workdir):
        raw = workdir / "raw"
        raw.mkdir(parents=True, exist_ok=True)
        (raw / "welcome.html").write_text("<html></html>", encoding="utf-8")
        return AcquireResult(raw_dir=raw, kind="html", slug="fakeapp-v2", pages=1)

    def normalize(self, acq, workdir):
        clean = workdir / f"{acq.slug}.html"
        clean.write_text("retitled body", encoding="utf-8")
        return clean


def test_refresh_pins_the_recorded_slug(tmp_path, monkeypatch):
    """A refresh re-ingests INTO the recorded slug even when acquire now
    derives a different one (source retitled, or the slug was a --slug
    override) — identity stays stable, no duplicate dir appears."""
    p = _BodyPattern("v1")
    monkeypatch.setattr(orchestrate, "classify", lambda url: p)
    orchestrate.run_ingest("https://x")

    monkeypatch.setattr(orchestrate, "classify", lambda url: _RenamedSlugPattern())
    out = refresh.refresh_slug("fakeapp")

    assert out["status"] == "changed"
    assert (tmp_path / "incoming" / "fakeapp" / "fakeapp.html").read_text(
        encoding="utf-8"
    ) == "retitled body"
    assert not (tmp_path / "incoming" / "fakeapp-v2").exists()


class _SingleFetchPattern(_BodyPattern):
    single_fetch = True


def _seed_validator_manifest(tmp_path, slug="fakeapp"):
    d = tmp_path / "incoming" / slug
    d.mkdir(parents=True)
    (d / f"{slug}.pdf").write_bytes(b"%PDF")
    manifest.write_manifest(
        d,
        manifest.build_manifest(
            source_url="https://x/manual.pdf",
            pattern="fake",
            slug=slug,
            kind="pdf",
            deliverable=f"{slug}.pdf",
            convert_recipe=[],
            pages=None,
            size_bytes=4,
            sha256=manifest.sha256_file(d / f"{slug}.pdf"),
            images=0,
            ingested_at="2026-07-01T00:00:00Z",
            etag='"abc123"',
            last_modified="Sat, 18 Jul 2026 10:00:00 GMT",
        ),
    )
    return d


def test_refresh_fast_path_probes_validators_instead_of_recrawling(tmp_path, monkeypatch):
    """A single-fetch pattern with stored validators probes with one
    conditional GET; a 304 means unchanged with NO re-download at all."""
    _seed_validator_manifest(tmp_path)
    probed: list = []
    monkeypatch.setattr(refresh, "pattern_by_name", lambda name: _SingleFetchPattern())
    monkeypatch.setattr(
        refresh.http,
        "not_modified",
        lambda url, *, etag, last_modified: (probed.append((url, etag, last_modified)), True)[1],
    )
    monkeypatch.setattr(
        refresh, "run_ingest", lambda *a, **k: pytest.fail("fast path must not re-ingest")
    )

    out = refresh.refresh_slug("fakeapp")

    assert out["status"] == "unchanged"
    assert "not modified" in out["detail"]
    assert probed == [("https://x/manual.pdf", '"abc123"', "Sat, 18 Jul 2026 10:00:00 GMT")]


def test_refresh_fast_path_miss_falls_through_to_full_reingest(tmp_path, monkeypatch):
    """A failed probe (changed content, error, whatever) falls through to the
    normal re-ingest path — the probe is an optimization, never a gate."""
    _seed_validator_manifest(tmp_path)
    monkeypatch.setattr(refresh, "pattern_by_name", lambda name: _SingleFetchPattern())
    monkeypatch.setattr(refresh.http, "not_modified", lambda url, **k: False)
    monkeypatch.setattr(
        refresh,
        "run_ingest",
        lambda url, **k: {
            "pattern": "fake",
            "slug": "fakeapp",
            "kind": "pdf",
            "clean": "x",
            "pages": None,
            "bytes": 4,
            "images": 0,
            "changed": True,
        },
    )

    out = refresh.refresh_slug("fakeapp")
    assert out["status"] == "changed"


def test_refresh_crawl_pattern_never_probes_validators(tmp_path, monkeypatch):
    """A crawl-shaped pattern (no single_fetch) ignores stored validators —
    an entry page's 304 proves nothing about the rest of the site."""
    _seed_validator_manifest(tmp_path)
    monkeypatch.setattr(refresh, "pattern_by_name", lambda name: _BodyPattern())  # no single_fetch
    monkeypatch.setattr(
        refresh.http,
        "not_modified",
        lambda url, **k: pytest.fail("crawl patterns must not probe"),
    )
    monkeypatch.setattr(
        refresh,
        "run_ingest",
        lambda url, **k: {
            "pattern": "fake",
            "slug": "fakeapp",
            "kind": "pdf",
            "clean": "x",
            "pages": None,
            "bytes": 4,
            "images": 0,
            "changed": False,
        },
    )

    out = refresh.refresh_slug("fakeapp")
    assert out["status"] == "unchanged"


def test_refresh_all_sweeps_every_slug_and_isolates_failures(tmp_path, monkeypatch):
    """refresh_all covers every incoming/<slug>/ in sorted order; one dead
    source doesn't stop the sweep (the healthy slug after it still refreshes)."""

    def _seed(slug, url, body):
        d = tmp_path / "incoming" / slug
        d.mkdir(parents=True)
        (d / f"{slug}.html").write_text(body, encoding="utf-8")
        manifest.write_manifest(
            d,
            manifest.build_manifest(
                source_url=url,
                pattern="fake",
                slug=slug,
                kind="html",
                deliverable=f"{slug}.html",
                convert_recipe=[],
                pages=1,
                size_bytes=len(body),
                sha256=manifest.sha256_file(d / f"{slug}.html"),
                images=0,
                ingested_at="2026-07-01T00:00:00Z",
            ),
        )

    _seed("aaa-dead", "https://dead.example.com", "old")
    _seed("bbb-alive", "https://alive.example.com", "old")

    class _PerUrlPattern(_BodyPattern):
        def acquire(self, url, workdir):
            if "dead" in url:
                import urllib.error

                raise urllib.error.URLError("gone")
            raw = workdir / "raw"
            raw.mkdir(parents=True, exist_ok=True)
            return AcquireResult(raw_dir=raw, kind="html", slug="bbb-alive", pages=1)

        def normalize(self, acq, workdir):
            clean = workdir / f"{acq.slug}.html"
            clean.write_text("new", encoding="utf-8")
            return clean

    monkeypatch.setattr(orchestrate, "classify", lambda url: _PerUrlPattern())
    outcomes = refresh.refresh_all()

    assert [o["slug"] for o in outcomes] == ["aaa-dead", "bbb-alive"]
    assert outcomes[0]["status"] == "failed"
    assert outcomes[1]["status"] == "changed"
    assert (tmp_path / "incoming" / "bbb-alive" / "bbb-alive.html").read_text(
        encoding="utf-8"
    ) == "new"
