"""audit — $0 deterministic deliverable checks (no network, no LLM).

Findings-based: a healthy slug audits to an empty list; each defect is one
(check, level, detail) finding. Error-level = the deliverable can't be
trusted; warning-level = real but survivable RAG noise.
"""

import pytest

from pagespring import audit, manifest, orchestrate


@pytest.fixture(autouse=True)
def _incoming_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate.cfg, "INCOMING_DIR", str(tmp_path / "incoming"))


def _stage(
    tmp_path,
    slug="fakeapp",
    body="# Title\n\ntext\n",
    *,
    kind="markdown",
    pages=2,
    images=0,
    pattern="fake",
):
    """Stage a slug the way a real ingest would: deliverable + matching manifest."""
    d = tmp_path / "incoming" / slug
    d.mkdir(parents=True)
    ext = {"markdown": "md", "html": "html", "pdf": "pdf"}[kind]
    f = d / f"{slug}.{ext}"
    f.write_text(body, encoding="utf-8") if kind != "pdf" else f.write_bytes(body.encode())
    manifest.write_manifest(
        d,
        manifest.build_manifest(
            source_url=f"https://x/docs/{slug}",  # per-slug: a shared URL is itself a finding
            pattern=pattern,
            slug=slug,
            kind=kind,
            deliverable=f.name,
            pages=pages,
            size_bytes=f.stat().st_size,
            sha256=manifest.sha256_file(f),
            images=images,
            ingested_at="2026-07-01T00:00:00Z",
        ),
    )
    return d


def test_healthy_slug_has_no_findings(tmp_path):
    _stage(tmp_path, body="# Title\n\ntext\n\n## Section\n\nmore\n")
    assert audit.audit_slug("fakeapp") == []


def _checks(findings):
    return [(f["check"], f["level"]) for f in findings]


def test_no_manifest_is_an_error(tmp_path):
    (tmp_path / "incoming" / "legacy").mkdir(parents=True)
    assert _checks(audit.audit_slug("legacy")) == [("manifest_missing", "error")]


def test_missing_deliverable_is_an_error(tmp_path):
    d = _stage(tmp_path)
    (d / "fakeapp.md").unlink()
    assert _checks(audit.audit_slug("fakeapp")) == [("deliverable_missing", "error")]


def test_empty_deliverable_is_an_error(tmp_path):
    d = _stage(tmp_path)
    (d / "fakeapp.md").write_text("", encoding="utf-8")
    findings = audit.audit_slug("fakeapp")
    assert ("deliverable_empty", "error") in _checks(findings)


def test_sha_mismatch_is_an_error_when_unlocalized(tmp_path):
    """images==0 ⇒ the on-disk file must hash to the manifest's sha256 —
    a mismatch means hand-edited or corrupted since staging."""
    d = _stage(tmp_path, body="# Title\n\ntext\n")
    (d / "fakeapp.md").write_text("# Title\n\ntampered\n", encoding="utf-8")
    assert ("sha_mismatch", "error") in _checks(audit.audit_slug("fakeapp"))


def test_sha_mismatch_not_flagged_after_localize(tmp_path):
    """images>0 ⇒ localize re-pointed refs, so on-disk bytes legitimately
    differ from the manifest's pre-localization content hash."""
    d = _stage(tmp_path, body="![a](images/a.png)\n\n# Title\n", images=1)
    (d / "images").mkdir()
    (d / "images" / "a.png").write_bytes(b"png")
    # The staged body already differs from any pre-localization sha; rewrite
    # the sha to something wrong to prove the check is skipped, not passing.
    m = manifest.read_manifest(d)
    m["sha256"] = "0" * 64
    manifest.write_manifest(d, m)
    assert "sha_mismatch" not in [c for c, _l in _checks(audit.audit_slug("fakeapp"))]


def test_unfinished_localize_is_a_warning(tmp_path):
    """images recorded but remote refs remain ⇒ a localize pass was cut short."""
    d = _stage(tmp_path, body="![a](images/a.png)\n![b](https://x/b.png)\n\n# T\n", images=1)
    (d / "images").mkdir()
    (d / "images" / "a.png").write_bytes(b"png")
    assert ("localize_incomplete", "warning") in _checks(audit.audit_slug("fakeapp"))


def test_multipage_deliverable_with_no_headings_is_a_warning(tmp_path):
    """A 40-page crawl that normalized to heading-less soup will split into
    nothing downstream — the classic half-lost-crawl signature."""
    _stage(tmp_path, body="just a wall of text\n" * 50, pages=40)
    assert ("no_headings", "warning") in _checks(audit.audit_slug("fakeapp"))


def test_single_page_without_headings_is_fine(tmp_path):
    _stage(tmp_path, body="a one-pager needs no headings\n", pages=1)
    assert audit.audit_slug("fakeapp") == []


def test_pdf_kind_skips_content_checks(tmp_path):
    """PDFs get existence/size/sha checks only — heading heuristics are for
    text deliverables."""
    _stage(tmp_path, body="%PDF-1.7 binary-ish", kind="pdf", pages=200)
    assert audit.audit_slug("fakeapp") == []


def test_single_page_from_crawl_pattern_is_an_error(tmp_path):
    """A crawl pattern that yielded one page means the seed URL was too specific.

    This is the code-claude-com collapse: seeding a single doc page made
    llms_txt fetch that page's .md twin instead of walking the index — 1 page
    where 172 were expected, and the ingest still exited 0.
    """
    _stage(tmp_path, pattern="llms_txt", pages=1)
    assert ("single_page_crawl", "error") in _checks(audit.audit_slug("fakeapp"))


def test_single_page_from_single_fetch_pattern_is_fine(tmp_path):
    """pdf_url fetches exactly one URL by design — the check must not fire."""
    _stage(tmp_path, pattern="pdf_url", body="%PDF-1.7 x", kind="pdf", pages=1)
    assert audit.audit_slug("fakeapp") == []


def test_single_page_pdf_deliverable_is_fine(tmp_path):
    """A PDF is one file whatever fetched it — readthedocs serves a PDF build,
    so a crawl pattern landing on `pages: 1` with kind=pdf is correct, not collapsed."""
    _stage(tmp_path, pattern="readthedocs", body="%PDF-1.5 x", kind="pdf", pages=1)
    assert audit.audit_slug("fakeapp") == []


def test_multi_page_crawl_is_fine(tmp_path):
    _stage(tmp_path, pattern="llms_txt", pages=172)
    assert audit.audit_slug("fakeapp") == []


def test_unknown_pattern_does_not_fire_single_page_crawl(tmp_path):
    """An unregistered pattern name can't be classified — don't guess."""
    _stage(tmp_path, pattern="not-a-real-pattern", pages=1)
    assert audit.audit_slug("fakeapp") == []


def test_audit_all_sweeps_sorted_and_reports_per_slug(tmp_path):
    _stage(tmp_path, slug="bbb-broken", body="# T\n")
    (tmp_path / "incoming" / "bbb-broken" / "bbb-broken.md").unlink()
    _stage(tmp_path, slug="aaa-clean", body="# Title\n\ntext\n")

    results = audit.audit_all()

    assert [slug for slug, _f in results] == ["aaa-clean", "bbb-broken"]
    assert results[0][1] == []
    assert _checks(results[1][1]) == [("deliverable_missing", "error")]


def test_truncated_crawl_is_an_error(tmp_path):
    """The Logic Pro failure: a page cap stopped the crawl at 1500 of 3935 and the
    deliverable passed every content check, because the guide had grown since the
    prior version — truncation hid inside the growth. Only the manifest knows."""
    d = _stage(tmp_path, pattern="apple_help", pages=1500)
    m = manifest.read_manifest(d)
    m["truncated"] = True
    manifest.write_manifest(d, m)

    assert ("crawl_truncated", "error") in _checks(audit.audit_slug("fakeapp"))


def test_untruncated_crawl_is_fine(tmp_path):
    _stage(tmp_path, pattern="apple_help", pages=1500)
    assert audit.audit_slug("fakeapp") == []


def test_pre_v4_manifest_without_truncated_is_fine(tmp_path):
    """Older manifests have no `truncated` key — absence must not read as True."""
    d = _stage(tmp_path, pattern="apple_help", pages=1500)
    m = manifest.read_manifest(d)
    del m["truncated"]
    manifest.write_manifest(d, m)

    assert audit.audit_slug("fakeapp") == []


# --- corpus-level checks: findings that no single slug can see ---


def test_duplicate_content_across_slugs_is_reported(tmp_path):
    """Two slugs holding byte-identical deliverables.

    This is the failure that per-slug auditing structurally cannot catch: ten
    independent verifiers each passed the same file because none of them saw
    more than one slug. `run_ingest` DOES detect it via find_by_sha — but only
    logs it, so the signal is gone by the time anyone audits.
    """
    _stage(tmp_path, slug="mic-manual", body="# Same\n\ntext\n")
    _stage(tmp_path, slug="mic-spec", body="# Same\n\ntext\n")

    results = dict(audit.audit_all())

    assert ("duplicate_content", "warning") in _checks(results["mic-spec"])
    assert "mic-manual" in " ".join(f["detail"] for f in results["mic-spec"])


def test_duplicate_source_url_across_slugs_is_an_error(tmp_path):
    """Two slugs pointing at the same URL — one of them is almost certainly the
    wrong document, even if the bytes later diverge.

    Error, not warning: a downstream converter names its output after the source
    file, so a second claim on the same URL isn't noise — it deadlocks the
    hand-off, and both slugs get skipped.
    """
    _stage(tmp_path, slug="a-manual", body="# A\n\naaa\n")
    _stage(tmp_path, slug="b-manual", body="# B\n\nbbb\n")
    for slug in ("a-manual", "b-manual"):
        d = tmp_path / "incoming" / slug
        m = manifest.read_manifest(d)
        m["source_url"] = "https://vendor/same.pdf"
        manifest.write_manifest(d, m)

    results = dict(audit.audit_all())

    assert ("duplicate_source_url", "error") in _checks(results["b-manual"])


def test_distinct_slugs_produce_no_corpus_findings(tmp_path):
    _stage(tmp_path, slug="one", body="# One\n\naaa\n")
    _stage(tmp_path, slug="two", body="# Two\n\nbbb\n")
    results = dict(audit.audit_all())
    assert results["one"] == [] and results["two"] == []


def test_corpus_findings_are_added_not_substituted(tmp_path):
    """A slug with its own defect must keep it AND gain the corpus finding."""
    _stage(tmp_path, slug="dup-a", body="# Same\n\ntext\n")
    d = _stage(tmp_path, slug="dup-b", body="# Same\n\ntext\n")
    (d / "dup-b.md").write_text("# Same\n\ntampered\n", encoding="utf-8")

    checks = _checks(dict(audit.audit_all())["dup-b"])

    assert ("sha_mismatch", "error") in checks
