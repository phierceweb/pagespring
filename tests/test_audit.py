"""audit: a healthy slug audits to []; each defect is one (check, level, detail) finding."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from pagespring import _integrity, _staging, audit, images, manifest, orchestrate, refresh
from pagespring.cli import app


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
    single_document=False,
    lost=0,
    localized_sha256=None,
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
            single_document=single_document,
            lost=lost,
            localized_sha256=localized_sha256,
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
    """A 40-page crawl that normalized to heading-less soup is the signature of a
    half-lost crawl."""
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
    """Seeding one doc page makes llms_txt fetch only its .md twin, and the ingest still exits 0."""
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
    """A capped crawl passes every content check; only the manifest knows."""
    d = _stage(tmp_path, pattern="apple_help", pages=1500)
    m = manifest.read_manifest(d)
    m["truncated"] = True
    manifest.write_manifest(d, m)

    finding = next(f for f in audit.audit_slug("fakeapp") if f["check"] == "crawl_truncated")
    assert finding["level"] == "error"
    assert finding["detail"].startswith("the crawl stopped short at 1500 pages")


def test_untruncated_crawl_is_fine(tmp_path):
    _stage(tmp_path, pattern="apple_help", pages=1500)
    assert audit.audit_slug("fakeapp") == []


def test_pre_v4_manifest_without_truncated_is_fine(tmp_path):
    """A manifest without a `truncated` key must not read as truncated."""
    d = _stage(tmp_path, pattern="apple_help", pages=1500)
    m = manifest.read_manifest(d)
    del m["truncated"]
    manifest.write_manifest(d, m)

    assert audit.audit_slug("fakeapp") == []


# --- corpus-level checks: findings that no single slug can see ---


def test_duplicate_content_across_slugs_is_reported(tmp_path):
    """``run_ingest`` only logs a byte-identical duplicate, so audit has to find it again."""
    _stage(tmp_path, slug="mic-manual", body="# Same\n\ntext\n")
    _stage(tmp_path, slug="mic-spec", body="# Same\n\ntext\n")

    results = dict(audit.audit_all())

    assert ("duplicate_content", "warning") in _checks(results["mic-spec"])
    assert "mic-manual" in " ".join(f["detail"] for f in results["mic-spec"])


def test_duplicate_source_url_across_slugs_is_an_error(tmp_path):
    """Two slugs pointing at the same URL — one of them is almost certainly the
    wrong document, even if the bytes later diverge."""
    _stage(tmp_path, slug="a-manual", body="# A\n\naaa\n")
    _stage(tmp_path, slug="b-manual", body="# B\n\nbbb\n")
    for slug in ("a-manual", "b-manual"):
        d = tmp_path / "incoming" / slug
        m = manifest.read_manifest(d)
        m["source_url"] = "https://vendor/same.pdf"
        manifest.write_manifest(d, m)

    results = dict(audit.audit_all())

    assert ("duplicate_source_url", "error") in _checks(results["b-manual"])


def test_duplicate_source_url_matches_spellings_the_staging_guard_calls_one_source(tmp_path):
    spellings = {
        "a-manual": "http://www.vendor.example/same.pdf",
        "b-manual": "https://vendor.example/same.pdf",
    }
    for slug, url in spellings.items():
        d = _stage(tmp_path, slug=slug, body=f"# {slug}\n\ntext\n")
        m = manifest.read_manifest(d)
        m["source_url"] = url
        manifest.write_manifest(d, m)
    assert _staging._same_source(*spellings.values())

    results = dict(audit.audit_all())

    assert ("duplicate_source_url", "error") in _checks(results["a-manual"])
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


def test_single_document_source_does_not_fire_single_page_crawl(tmp_path):
    """A WordPress post is one page by construction. Firing here would make
    `audit --all --strict` exit 1 on a perfectly good deliverable."""
    _stage(tmp_path, "post", pattern="docs_probe", kind="html", pages=1, single_document=True)

    assert ("single_page_crawl", "error") not in _checks(audit.audit_slug("post"))


def test_dangling_local_image_ref_is_an_error(tmp_path):
    """A ref to images/<name> whose file is absent renders as a broken image, and
    count_remote_images counts only REMOTE refs, so nothing else sees it."""
    d = _stage(tmp_path, body="![a](images/gone.png)\n\n# T\n", images=1)
    (d / "images").mkdir()
    (d / "images" / "kept.png").write_bytes(b"png")

    assert ("broken_image_ref", "error") in _checks(audit.audit_slug("fakeapp"))


def test_resolved_local_image_refs_are_fine(tmp_path):
    d = _stage(tmp_path, body="![a](images/kept.png)\n\n# T\n", images=1)
    (d / "images").mkdir()
    (d / "images" / "kept.png").write_bytes(b"png")

    assert ("broken_image_ref", "error") not in _checks(audit.audit_slug("fakeapp"))


def test_pages_lost_is_an_error(tmp_path):
    """60% missing yet healthy-looking: no page cap was hit, so ``truncated`` stays False."""
    d = _stage(tmp_path, pattern="apple_help", pages=40)
    m = manifest.read_manifest(d)
    m["lost"] = 60
    manifest.write_manifest(d, m)

    assert ("pages_lost", "error") in _checks(audit.audit_slug("fakeapp"))


def test_no_pages_lost_is_fine(tmp_path):
    _stage(tmp_path, pattern="apple_help", pages=40)
    assert ("pages_lost", "error") not in _checks(audit.audit_slug("fakeapp"))


def test_tampering_after_localize_is_an_error(tmp_path):
    """`localized_sha256` is a localized deliverable's integrity record, so a
    hand-edit or a truncated write after localize is a sha_mismatch."""
    d = _stage(tmp_path, body="![a](images/a.png)\n\n# T\n", images=1)
    (d / "images").mkdir()
    (d / "images" / "a.png").write_bytes(b"png")
    f = d / "fakeapp.md"
    m = manifest.read_manifest(d)
    m["localized_sha256"] = manifest.sha256_file(f)
    manifest.write_manifest(d, m)
    assert audit.audit_slug("fakeapp") == []

    f.write_text(f.read_text(encoding="utf-8") + "x", encoding="utf-8")

    assert ("sha_mismatch", "error") in _checks(audit.audit_slug("fakeapp"))


def test_failed_localize_with_empty_images_dir_is_a_warning(tmp_path):
    """Every download failed, so images stays 0 while remote refs remain;
    images/ existing is what says a pass ran."""
    d = _stage(tmp_path, body="![b](https://x/b.png)\n\n# T\n", images=0)
    (d / "images").mkdir()

    assert ("localize_incomplete", "warning") in _checks(audit.audit_slug("fakeapp"))


def test_remote_ref_the_localizer_skipped_is_still_flagged(tmp_path, monkeypatch):
    """The count is stubbed to 0: audit must read the file, since the localizer never counts a ref
    it declines to claim."""
    skipped = "https://cdn.example.com/is/image/Prod/whats-new-widget-3.1-preview"
    d = _stage(tmp_path, body=f'![a](images/a.png)\n<img src="{skipped}">\n\n# T\n', images=1)
    (d / "images").mkdir()
    (d / "images" / "a.png").write_bytes(b"png")
    monkeypatch.setattr(images, "count_remote_images", lambda _doc: 0)

    assert ("localize_incomplete", "warning") in _checks(audit.audit_slug("fakeapp"))


def test_remote_non_image_refs_are_not_localize_findings(tmp_path):
    """Only image refs count. Deliverables keep their ordinary hyperlinks and
    their video/iframe embeds, and none of those are the localizer's to fetch."""
    body = (
        "![a](images/a.png)\n"
        "[Code Preview](https://docs.example.com/gui-tools/preview)\n"
        '<iframe src="https://www.youtube.com/embed/abc123"></iframe>\n'
        '<video><source src="https://cdn.example.com/clip.mp4"></video>\n'
        "\n# T\n"
    )
    d = _stage(tmp_path, body=body, images=1)
    (d / "images").mkdir()
    (d / "images" / "a.png").write_bytes(b"png")

    assert "localize_incomplete" not in [c for c, _l in _checks(audit.audit_slug("fakeapp"))]


def test_single_operation_api_spec_is_not_a_collapsed_crawl(tmp_path):
    """``pages`` counts operations, so one operation is a complete spec."""
    _stage(tmp_path, pattern="api_spec", pages=1)
    assert audit.audit_slug("fakeapp") == []


def _lost_detail(tmp_path, *, pages, lost):
    _stage(tmp_path, pattern="llms_txt", pages=pages, lost=lost)
    return next(f for f in audit.audit_slug("fakeapp") if f["check"] == "pages_lost")["detail"]


def test_sub_one_percent_page_loss_is_not_reported_as_zero(tmp_path):
    """A sub-1% loss rounded to "(0%)" would be an error whose own number says
    nothing was lost."""
    detail = _lost_detail(tmp_path, pages=1971, lost=1)
    assert "1 of 1972" in detail
    assert "(<1%)" in detail, f"loss rendered as zero: {detail}"


def test_page_loss_share_renders_the_rounded_percentage(tmp_path):
    assert "(60%)" in _lost_detail(tmp_path, pages=40, lost=60)


def test_page_loss_of_exactly_one_percent_is_not_floored(tmp_path):
    """The special cases must not widen past the values that round to a lie."""
    assert "(1%)" in _lost_detail(tmp_path, pages=99, lost=1)


def test_page_loss_just_under_the_ceiling_still_rounds_plainly(tmp_path):
    assert "(99%)" in _lost_detail(tmp_path, pages=6, lost=994)


def test_near_total_page_loss_is_not_reported_as_total(tmp_path):
    """A near-total loss rounded to "(100%)" would claim nothing was staged when
    one page was."""
    detail = _lost_detail(tmp_path, pages=1, lost=999)
    assert "(>99%)" in detail, f"partial loss rendered as total: {detail}"


def test_actual_total_page_loss_says_so(tmp_path):
    assert "(100%)" in _lost_detail(tmp_path, pages=0, lost=7)


def test_unverifiable_deliverable_is_not_reported_as_healthy(tmp_path):
    """With images but no localized_sha256 the sha check cannot run; the warning
    keeps "ok" meaning "verified" rather than "never checked"."""
    d = _stage(tmp_path, images=5)
    # A real localize re-points refs, so the bytes diverge from the staged sha.
    (d / "fakeapp.md").write_text("# Title\n\n![i](images/i.png)\n", encoding="utf-8")
    (d / "images").mkdir()
    (d / "images" / "i.png").write_bytes(b"png")
    findings = _checks(audit.audit_slug("fakeapp"))
    assert ("sha_unverified", "warning") in findings, f"reported healthy: {findings}"
    assert ("sha_mismatch", "error") not in findings


def test_sha_unverified_advises_a_reingest_not_a_localize(tmp_path):
    """localize would record whatever is on disk, damaged or not, as verified."""
    d = _stage(tmp_path, images=5)
    (d / "fakeapp.md").write_text("# Title\n\n![i](images/i.png)\n", encoding="utf-8")
    (d / "images").mkdir()
    (d / "images" / "i.png").write_bytes(b"png")

    [detail] = [f["detail"] for f in audit.audit_slug("fakeapp") if f["check"] == "sha_unverified"]

    assert "--download-images" in detail
    assert "run localize" not in detail


def test_ingest_killed_mid_image_pass_warns_instead_of_reporting_corruption(tmp_path):
    """Before schema v7 no pass was marked open; only images/ tells a killed pass from damage."""
    d = _stage(tmp_path, body="# T\n\n![i](images/i.png)\n![j](https://x/j.png)\n")
    m = manifest.read_manifest(d)
    # The staged sha describes the pre-localize body; the file was checkpointed after.
    m["sha256"] = "0" * 64
    manifest.write_manifest(d, m)
    (d / "images").mkdir()
    (d / "images" / "i.png").write_bytes(b"png")

    findings = _checks(audit.audit_slug("fakeapp"))
    assert ("sha_unverified", "warning") in findings
    assert ("sha_mismatch", "error") not in findings, "a killed pass is not corruption"


def test_failed_image_pass_that_changed_nothing_still_verifies(tmp_path):
    """images/ exists but every download failed: the deliverable is byte-identical
    to the staged sha, which verifies it — warning here would cry wolf."""
    d = _stage(tmp_path)
    (d / "images").mkdir()

    assert _checks(audit.audit_slug("fakeapp")) == []


def test_localized_sha_present_means_the_deliverable_is_verified(tmp_path):
    """The counterpart: a localized deliverable carrying its post-localize hash is
    genuinely checked, so it must stay silent."""
    d = _stage(tmp_path, images=5)
    m = manifest.read_manifest(d)
    m["localized_sha256"] = manifest.sha256_file(d / "fakeapp.md")
    manifest.write_manifest(d, m)
    assert audit.audit_slug("fakeapp") == []


def test_an_image_pass_cut_short_asks_for_localize_not_a_reingest(tmp_path):
    """A pass that never recorded its outcome is resumable: the checkpoint it left is
    neither corruption nor unverifiable."""
    d = _stage(tmp_path, body="# T\n\n![i](https://x/i.png)\n![j](https://x/j.png)\n")
    _integrity.open_for_image_pass(d, manifest.read_manifest(d))
    (d / "images").mkdir()
    (d / "images" / "i.png").write_bytes(b"png")
    f = d / "fakeapp.md"
    f.write_text(f.read_text(encoding="utf-8").replace("https://x/i.png", "images/i.png"), "utf-8")

    findings = audit.audit_slug("fakeapp")

    checks = _checks(findings)
    assert ("localize_interrupted", "warning") in checks
    assert ("sha_unverified", "warning") not in checks
    assert ("sha_mismatch", "error") not in checks
    [detail] = [x["detail"] for x in findings if x["check"] == "localize_interrupted"]
    assert "re-run localize" in detail


# --- one unreadable slug dir must not abort a corpus sweep ---


def _stage_unreadable(tmp_path, slug="mmm-bad"):
    """A slug dir whose manifest.json parses but carries none of the fields the
    sweeps index — a truncated or hand-edited file."""
    d = tmp_path / "incoming" / slug
    d.mkdir(parents=True)
    (d / "manifest.json").write_text('{"pages": 3}\n', encoding="utf-8")
    return d


def test_audit_all_reports_healthy_slugs_around_an_unreadable_one(tmp_path):
    """A field-less manifest is a finding for its own slug, not an exception that
    ends the corpus pass."""
    _stage(tmp_path, slug="aaa-good", body="# T\n\ntext\n")
    _stage_unreadable(tmp_path)

    results = dict(audit.audit_all())

    assert results["aaa-good"] == []
    assert _checks(results["mmm-bad"]) == [("manifest_missing", "error")]


def test_refresh_all_skips_an_unreadable_slug_and_sweeps_the_rest(tmp_path, monkeypatch):
    _stage(tmp_path, slug="aaa-good", body="# T\n\ntext\n")
    _stage_unreadable(tmp_path)
    monkeypatch.setattr(refresh, "run_ingest", lambda url, **kw: {"changed": True})

    outcomes = {o["slug"]: o["status"] for o in refresh.refresh_all()}

    assert outcomes["aaa-good"] == "changed"
    assert outcomes["mmm-bad"] == "skipped"


def test_status_lists_healthy_slugs_around_an_unreadable_one(tmp_path):
    _stage(tmp_path, slug="aaa-good", body="# T\n\ntext\n")
    _stage_unreadable(tmp_path)

    r = CliRunner().invoke(app, ["status"])

    assert r.exit_code == 0, r.exception
    assert "aaa-good.md" in r.output
    assert "mmm-bad" in r.output


def test_a_leftover_images_dir_does_not_excuse_a_corrupt_deliverable(tmp_path):
    """A re-ingest keeps images/, so the bare dir must not downgrade corruption to a warning."""
    d = _stage(tmp_path, body="# T\n\ntext\n")
    (d / "images").mkdir()  # left behind by an earlier --download-images run
    deliverable = d / "fakeapp.md"
    deliverable.write_text("# T\n\nCORRUPTED\n", encoding="utf-8")

    findings = audit.audit_slug("fakeapp")

    assert _checks(findings) == [("sha_mismatch", "error")], (
        f"corruption was downgraded: {_checks(findings)}"
    )


def test_the_deliverable_is_read_at_most_once_per_audit(tmp_path, monkeypatch):
    """`audit_slug` walks the deliverable for several checks, so it reads the file once —
    and never decodes a PDF, which carries its images inline and never localizes."""
    d = _stage(tmp_path, body="# T\n\n![a](images/a.png)\n")
    (d / "images").mkdir()
    (d / "images" / "a.png").write_bytes(b"x")
    deliverable = d / "fakeapp.md"

    reads = []
    real_read_text = Path.read_text

    def counting_read_text(self, *a, **kw):
        if self == deliverable:
            reads.append(1)
        return real_read_text(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", counting_read_text)
    audit.audit_slug("fakeapp")

    assert len(reads) <= 1, f"deliverable decoded {len(reads)} times in one audit"


def test_a_source_path_through_a_symlink_loop_does_not_stop_the_audit(tmp_path):
    loop_a, loop_b = tmp_path / "a", tmp_path / "b"
    loop_a.symlink_to(loop_b)
    loop_b.symlink_to(loop_a)
    d = _stage(tmp_path, slug="spec", body="# Spec\n\ntext\n")
    m = manifest.read_manifest(d)
    m["source_url"] = str(loop_a / "spec.json")
    manifest.write_manifest(d, m)
    _stage(tmp_path, slug="other", body="# Other\n\ntext\n")

    results = dict(audit.audit_all())

    assert results["spec"] == [] and results["other"] == []
