"""CLI commands (patterns / classify / ingest / status), via Typer's runner."""

import json
import os
import pathlib
import signal
import subprocess
import sys
import time

import pytest
from typer.testing import CliRunner

import pagespring.cli as climod
from pagespring import _cli_corpus, _cli_ingest, manifest
from pagespring.cli import app
from pagespring.config import cfg
from pagespring.orchestrate import AcquireError, EmptyOutputError, NoPatternError

runner = CliRunner()


def test_patterns_lists_registered():
    r = runner.invoke(app, ["patterns"])
    assert r.exit_code == 0
    assert "apple_help" in r.output
    assert "gitbook" in r.output
    # Names only — pagespring has no opinion about downstream conversion.
    assert "recipe" not in r.output.lower()


def test_classify_routes_apple():
    r = runner.invoke(app, ["classify", "https://support.apple.com/guide/keynote/welcome/mac"])
    assert r.exit_code == 0
    assert "apple_help" in r.output


def test_classify_unknown():
    # docs_probe claims every http(s) URL; only a non-web argument reaches "no pattern".
    r = runner.invoke(app, ["classify", "./no-such-file"])
    assert r.exit_code == 0
    assert "no pattern" in r.output.lower()


def test_ingest_formats_output(monkeypatch, tmp_path):
    def fake_run_ingest(url, **kwargs):
        return {
            "pattern": "apple_help",
            "slug": "keynote",
            "kind": "html",
            "clean": str(tmp_path / "keynote.html"),
            "images": 0,
            "pages": 187,
            "bytes": 1_153_433,
        }

    monkeypatch.setattr(_cli_ingest, "run_ingest", fake_run_ingest)
    r = runner.invoke(app, ["ingest", "https://support.apple.com/guide/keynote/welcome/mac"])
    assert r.exit_code == 0
    assert "apple_help" in r.output
    assert "incoming" in r.output.lower()
    assert "187" in r.output  # crawl scale visible at a glance
    assert "1.1 MB" in r.output


def test_ingest_no_pattern_exits_2(monkeypatch):
    def fake_run_ingest(url, **kwargs):
        raise NoPatternError(url)

    monkeypatch.setattr(_cli_ingest, "run_ingest", fake_run_ingest)
    r = runner.invoke(app, ["ingest", "https://example.com/x"])
    assert r.exit_code == 2
    assert "no pattern matched" in r.output.lower()


def test_ingest_empty_output_exits_3(monkeypatch):
    def fake_run_ingest(url, **kwargs):
        raise EmptyOutputError(url)

    monkeypatch.setattr(_cli_ingest, "run_ingest", fake_run_ingest)
    r = runner.invoke(app, ["ingest", "https://example.com/x"])
    assert r.exit_code == 3
    assert "empty" in r.output.lower()


def test_ingest_fetch_failure_exits_4(monkeypatch):
    def fake_run_ingest(url, **kwargs):
        raise AcquireError(url, "HTTP Error 404: Not Found")

    monkeypatch.setattr(_cli_ingest, "run_ingest", fake_run_ingest)
    r = runner.invoke(app, ["ingest", "https://docs.x.com"])
    assert r.exit_code == 4
    assert "fetch failed" in r.output.lower()
    assert "404" in r.output


def test_status_reports_incoming_slugs(monkeypatch, tmp_path):
    """status: one row per incoming/<slug>/ with its deliverable file + size, and no
    conversion column."""
    monkeypatch.setattr(cfg, "INCOMING_DIR", str(tmp_path / "incoming"))
    (tmp_path / "incoming" / "keynote").mkdir(parents=True)
    (tmp_path / "incoming" / "keynote" / "keynote.html").write_text("<h1>K</h1>", encoding="utf-8")
    (tmp_path / "incoming" / "numbers").mkdir()
    (tmp_path / "incoming" / "numbers" / "numbers.md").write_text("# N", encoding="utf-8")

    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0
    keynote = next(line for line in r.output.splitlines() if "keynote" in line)
    assert "keynote.html" in keynote
    assert any("numbers.md" in line for line in r.output.splitlines())
    assert "converted" not in r.output


def test_status_empty_incoming(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "INCOMING_DIR", str(tmp_path / "incoming"))
    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0
    assert "nothing in incoming/" in r.output


def test_ingest_slug_forwarded_and_duplicate_warned(monkeypatch, tmp_path):
    """--slug reaches run_ingest; a duplicate_of result prints a warning line."""
    captured: dict = {}

    def fake_run_ingest(url, **kwargs):
        captured.update(kwargs)
        return {
            "pattern": "pdf_url",
            "slug": "tidy",
            "kind": "pdf",
            "clean": str(tmp_path / "tidy.pdf"),
            "images": 0,
            "pages": None,
            "bytes": 100,
            "changed": True,
            "duplicate_of": "existing-slug",
        }

    monkeypatch.setattr(_cli_ingest, "run_ingest", fake_run_ingest)
    r = runner.invoke(app, ["ingest", "https://x/m.pdf", "--slug", "tidy"])
    assert r.exit_code == 0
    assert captured.get("slug_override") == "tidy"
    assert "identical to incoming/existing-slug/" in r.output


def test_ingest_if_changed_forwarded_and_unchanged_reported(monkeypatch, tmp_path):
    """--if-changed reaches run_ingest; a changed=False result prints 'unchanged'."""
    captured: dict = {}

    def fake_run_ingest(url, **kwargs):
        captured.update(kwargs)
        return {
            "pattern": "gitbook",
            "slug": "docs",
            "kind": "markdown",
            "clean": str(tmp_path / "docs.md"),
            "images": 0,
            "pages": 5,
            "bytes": 100,
            "changed": False,
        }

    monkeypatch.setattr(_cli_ingest, "run_ingest", fake_run_ingest)
    r = runner.invoke(app, ["ingest", "https://docs.x.com", "--if-changed"])
    assert r.exit_code == 0
    assert captured.get("if_changed") is True
    assert "unchanged" in r.output.lower()


def test_renormalize_formats_output(monkeypatch, tmp_path):
    def fake_run_renormalize(slug):
        return {
            "pattern": "zendesk_help",
            "slug": slug,
            "kind": "html",
            "clean": str(tmp_path / "incoming" / "helpsite" / "helpsite.html"),
            "pages": 42,
            "bytes": 200_000,
            "changed": True,
        }

    monkeypatch.setattr(_cli_ingest, "run_renormalize", fake_run_renormalize)
    r = runner.invoke(app, ["renormalize", "helpsite"])
    assert r.exit_code == 0
    assert "zendesk_help" in r.output
    assert "incoming" in r.output.lower()
    assert "42" in r.output
    assert "195.3 KB" in r.output


def test_renormalize_unchanged_reported(monkeypatch, tmp_path):
    def fake_run_renormalize(slug):
        return {
            "pattern": "gitbook",
            "slug": slug,
            "kind": "markdown",
            "clean": str(tmp_path / "docs.md"),
            "pages": 5,
            "bytes": 100,
            "changed": False,
        }

    monkeypatch.setattr(_cli_ingest, "run_renormalize", fake_run_renormalize)
    r = runner.invoke(app, ["renormalize", "docs"])
    assert r.exit_code == 0
    assert "unchanged" in r.output.lower()


def test_renormalize_precondition_exits_2(monkeypatch):
    from pf_core.exceptions import PreconditionError

    def fake_run_renormalize(slug):
        raise PreconditionError(f"no raw/ kept for incoming/{slug}/ — re-ingest with --keep-raw")

    monkeypatch.setattr(_cli_ingest, "run_renormalize", fake_run_renormalize)
    r = runner.invoke(app, ["renormalize", "helpsite"])
    assert r.exit_code == 2
    assert "--keep-raw" in r.output


def test_renormalize_empty_output_exits_3(monkeypatch):
    def fake_run_renormalize(slug):
        raise EmptyOutputError(slug)

    monkeypatch.setattr(_cli_ingest, "run_renormalize", fake_run_renormalize)
    r = runner.invoke(app, ["renormalize", "helpsite"])
    assert r.exit_code == 3
    assert "empty" in r.output.lower()


def test_refresh_all_prints_report_and_summary(monkeypatch):
    outcomes = [
        {"slug": "aaa", "status": "changed", "detail": ""},
        {"slug": "bbb", "status": "unchanged", "detail": ""},
        {"slug": "ccc", "status": "unchanged", "detail": "not modified (validator probe)"},
    ]
    monkeypatch.setattr(_cli_corpus, "refresh_all", lambda: outcomes)
    r = runner.invoke(app, ["refresh", "--all"])
    assert r.exit_code == 0
    assert "aaa: changed" in r.output
    assert "ccc: unchanged — not modified (validator probe)" in r.output
    assert "1 changed, 2 unchanged" in r.output


def test_refresh_all_exits_1_when_any_slug_failed(monkeypatch):
    outcomes = [
        {"slug": "aaa", "status": "changed", "detail": ""},
        {"slug": "bbb", "status": "failed", "detail": "connection refused"},
    ]
    monkeypatch.setattr(_cli_corpus, "refresh_all", lambda: outcomes)
    r = runner.invoke(app, ["refresh", "--all"])
    assert r.exit_code == 1
    assert "bbb: failed — connection refused" in r.output
    assert "1 failed" in r.output


def test_refresh_single_slug_skipped_exits_2(monkeypatch):
    monkeypatch.setattr(
        _cli_corpus,
        "refresh_slugs",
        lambda slugs: [
            {"slug": s, "status": "skipped", "detail": "no manifest — ingest it first"}
            for s in slugs
        ],
    )
    r = runner.invoke(app, ["refresh", "ghost"])
    assert r.exit_code == 2
    assert "no manifest" in r.output


def test_refresh_requires_slug_or_all():
    r = runner.invoke(app, ["refresh"])
    assert r.exit_code == 2


def test_audit_all_prints_findings_and_ok_lines(monkeypatch):
    results = [
        ("aaa", []),
        ("bbb", [{"check": "sha_mismatch", "level": "error", "detail": "content differs"}]),
        ("ccc", [{"check": "no_headings", "level": "warning", "detail": "40 pages, 0 headings"}]),
    ]
    monkeypatch.setattr(_cli_corpus, "audit_all", lambda: results)
    r = runner.invoke(app, ["audit", "--all"])
    assert r.exit_code == 0  # report-only by default
    assert "aaa: ok" in r.output
    assert "bbb: sha_mismatch (error) — content differs" in r.output
    assert "ccc: no_headings (warning) — 40 pages, 0 headings" in r.output
    assert "1 error, 1 warning" in r.output


def test_audit_strict_exits_1_on_errors(monkeypatch):
    results = [("bbb", [{"check": "deliverable_empty", "level": "error", "detail": "0 bytes"}])]
    monkeypatch.setattr(_cli_corpus, "audit_all", lambda: results)
    r = runner.invoke(app, ["audit", "--all", "--strict"])
    assert r.exit_code == 1


def test_audit_strict_passes_on_warnings_only(monkeypatch):
    results = [("ccc", [{"check": "no_headings", "level": "warning", "detail": "…"}])]
    monkeypatch.setattr(_cli_corpus, "audit_all", lambda: results)
    r = runner.invoke(app, ["audit", "--all", "--strict"])
    assert r.exit_code == 0


def test_audit_single_slug(monkeypatch):
    monkeypatch.setattr(_cli_corpus, "audit_slug", lambda s: [])
    r = runner.invoke(app, ["audit", "keynote"])
    assert r.exit_code == 0
    assert "keynote: ok" in r.output


def test_audit_requires_slug_or_all():
    r = runner.invoke(app, ["audit"])
    assert r.exit_code == 2


def test_localize_command_reports_done(monkeypatch):
    monkeypatch.setattr(
        _cli_ingest,
        "localize_images",
        lambda s: {
            "slug": s,
            "localized": 5,
            "reused": 0,
            "pruned": 0,
            "remaining": 0,
            "images_total": 5,
        },
    )
    r = runner.invoke(app, ["localize", "biology-2e"])
    assert r.exit_code == 0
    assert "biology-2e" in r.output
    assert "5" in r.output
    assert "done" in r.output.lower()


def test_localize_command_reports_remaining(monkeypatch):
    """When images remain (a big book exceeded one pass), the output says re-run."""
    monkeypatch.setattr(
        _cli_ingest,
        "localize_images",
        lambda s: {
            "slug": s,
            "localized": 50,
            "reused": 0,
            "pruned": 0,
            "remaining": 120,
            "images_total": 50,
        },
    )
    r = runner.invoke(app, ["localize", "biology-2e"])
    assert r.exit_code == 0
    assert "120" in r.output
    assert "re-run" in r.output.lower()


def test_localize_all_iterates_incoming(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "INCOMING_DIR", str(tmp_path / "incoming"))
    (tmp_path / "incoming" / "a").mkdir(parents=True)
    (tmp_path / "incoming" / "b").mkdir()
    calls: list[str] = []

    def fake(slug):
        calls.append(slug)
        return {
            "slug": slug,
            "localized": 1,
            "reused": 0,
            "pruned": 0,
            "remaining": 0,
            "images_total": 1,
        }

    monkeypatch.setattr(_cli_ingest, "localize_images", fake)
    r = runner.invoke(app, ["localize", "--all"])
    assert r.exit_code == 0
    assert sorted(calls) == ["a", "b"]


def test_localize_requires_slug_or_all():
    r = runner.invoke(app, ["localize"])
    assert r.exit_code == 2


def test_localize_all_reports_an_unreadable_manifest_and_sweeps_on(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "INCOMING_DIR", str(tmp_path / "incoming"))
    bad = tmp_path / "incoming" / "aaa-bad"
    bad.mkdir(parents=True)
    (bad / manifest.MANIFEST_NAME).write_text('{"pages": 3}\n', encoding="utf-8")
    good = tmp_path / "incoming" / "bbb-good"
    _write_manifest(good, kind="pdf", deliverable="bbb-good.pdf")
    (good / "bbb-good.pdf").write_bytes(b"%PDF-1.7")

    r = runner.invoke(app, ["localize", "--all"])

    assert r.exit_code == 0, r.output
    assert "skip aaa-bad" in r.output
    assert "bbb-good: +0 images" in r.output


def test_localize_of_one_slug_it_refuses_exits_2(monkeypatch, tmp_path):
    from pf_core.exceptions import PreconditionError

    def refuse(slug):
        raise PreconditionError("fakeapp.html no longer matches its manifest — re-ingest")

    monkeypatch.setattr(_cli_ingest, "localize_images", refuse)

    r = runner.invoke(app, ["localize", "fakeapp"])

    assert r.exit_code == 2
    assert "re-ingest" in r.output


def test_ingest_reports_images_downloaded_this_run_apart_from_the_total(monkeypatch, tmp_path):
    monkeypatch.setattr(
        _cli_ingest,
        "run_ingest",
        lambda url, **kw: {
            "pattern": "gitbook",
            "slug": "docs",
            "kind": "markdown",
            "clean": str(tmp_path / "docs.md"),
            "pages": 12,
            "bytes": 2048,
            "images": 40,
            "images_downloaded": 3,
            "changed": True,
            "duplicate_of": None,
        },
    )

    r = runner.invoke(app, ["ingest", "https://docs.example.com/", "--download-images"])

    assert r.exit_code == 0
    [line] = [ln for ln in r.output.splitlines() if ln.startswith("images")]
    assert "40" in line and "3 downloaded" in line
    assert "40 downloaded" not in line


def _write_manifest(slug_dir, **over):
    slug_dir.mkdir(parents=True, exist_ok=True)
    fields = {
        "source_url": "https://docs.tableplus.com/",
        "pattern": "gitbook",
        "slug": slug_dir.name,
        "kind": "markdown",
        "deliverable": "docs-tableplus-com.md",
        "pages": 62,
        "size_bytes": 100,
        "sha256": "abc",
        "images": 0,
        "ingested_at": "2026-06-14T17:23:01Z",
    }
    fields.update(over)
    manifest.write_manifest(slug_dir, manifest.build_manifest(**fields))


def test_ingest_unrecognized_spec_exits_2(monkeypatch):
    from pf_core.exceptions import InvalidInputError

    def fake_run_ingest(url, **kwargs):
        raise InvalidInputError("…not a recognizable OpenAPI/Swagger spec or Postman collection")

    monkeypatch.setattr(_cli_ingest, "run_ingest", fake_run_ingest)
    r = runner.invoke(app, ["ingest", "https://x.com/thing.json"])
    assert r.exit_code == 2
    assert "not a recognizable" in r.output.lower()


def test_status_reads_manifest(monkeypatch, tmp_path):
    """status surfaces the manifest's pattern, pages, source host, and date —
    and never reports manifest.json itself as the deliverable."""
    monkeypatch.setattr(cfg, "INCOMING_DIR", str(tmp_path / "incoming"))
    slug_dir = tmp_path / "incoming" / "docs-tableplus-com"
    _write_manifest(slug_dir)
    (slug_dir / "docs-tableplus-com.md").write_text("# T", encoding="utf-8")

    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0
    line = next(line for line in r.output.splitlines() if "docs-tableplus-com" in line)
    assert "gitbook" in line  # pattern from manifest
    assert "62" in line  # pages from manifest
    assert "docs.tableplus.com" in line  # source host
    assert "2026-06-14" in line  # ingested date from manifest
    assert "manifest.json" not in r.output  # never the deliverable


def test_status_flags_slugs_that_kept_raw(monkeypatch, tmp_path):
    """Which slugs replay offline is otherwise invisible — you would have to
    stat every directory to plan a normalize change."""
    monkeypatch.setattr(cfg, "INCOMING_DIR", str(tmp_path / "incoming"))
    for slug, kept in (("withraw", True), ("noraw", False)):
        d = tmp_path / "incoming" / slug
        d.mkdir(parents=True)
        f = d / f"{slug}.html"
        f.write_text("<h1>x</h1>", encoding="utf-8")
        manifest.write_manifest(
            d,
            manifest.build_manifest(
                source_url=f"https://x/{slug}",
                pattern="docs_probe",
                slug=slug,
                kind="html",
                deliverable=f.name,
                pages=2,
                size_bytes=f.stat().st_size,
                sha256=manifest.sha256_file(f),
                images=0,
                ingested_at="2026-08-02T00:00:00Z",
                kept_raw=kept,
            ),
        )

    r = runner.invoke(app, ["status"])

    assert r.exit_code == 0
    with_raw = next(line for line in r.output.splitlines() if "withraw" in line)
    without = next(line for line in r.output.splitlines() if line.startswith("noraw"))
    assert "raw" in with_raw
    assert "raw" not in without.replace("noraw", "")


def test_audit_all_on_an_empty_corpus_is_not_success(monkeypatch):
    """`audit_all()` returns [] when `incoming/` is missing or empty."""
    monkeypatch.setattr(_cli_corpus, "audit_all", lambda: [])
    r = runner.invoke(app, ["audit", "--all"])
    assert r.exit_code == 2, f"empty corpus reported success: {r.output!r}"
    assert "all ok" not in r.output, f"misleading summary: {r.output!r}"


def test_audit_all_strict_on_an_empty_corpus_fails(monkeypatch):
    """The gate case: --strict must never certify an empty corpus."""
    monkeypatch.setattr(_cli_corpus, "audit_all", lambda: [])
    r = runner.invoke(app, ["audit", "--all", "--strict"])
    assert r.exit_code != 0, f"--strict certified an empty corpus: {r.output!r}"


def test_audit_all_message_names_the_cause(monkeypatch):
    monkeypatch.setattr(_cli_corpus, "audit_all", lambda: [])
    r = runner.invoke(app, ["audit", "--all"])
    assert "incoming" in r.output.lower(), (
        f"the message should say where nothing was found: {r.output!r}"
    )


def test_audit_all_with_slugs_still_reports_ok(monkeypatch):
    """The empty guard must not fire on a genuinely clean corpus."""
    monkeypatch.setattr(_cli_corpus, "audit_all", lambda: [("aaa", []), ("bbb", [])])
    r = runner.invoke(app, ["audit", "--all", "--strict"])
    assert r.exit_code == 0
    assert "2 audited, all ok" in r.output


def test_refresh_all_on_an_empty_corpus_is_not_a_clean_sweep(monkeypatch):
    """`refresh_all()` returns [] when `incoming/` is missing or empty. Exit 0
    there tells a wrapper the sweep was clean when nothing was swept."""
    monkeypatch.setattr(_cli_corpus, "refresh_all", lambda: [])
    r = runner.invoke(app, ["refresh", "--all"])
    assert r.exit_code == 2, f"empty corpus reported a clean sweep: {r.output!r}"


def test_refresh_all_empty_message_names_the_cause(monkeypatch):
    monkeypatch.setattr(_cli_corpus, "refresh_all", lambda: [])
    r = runner.invoke(app, ["refresh", "--all"])
    assert "incoming" in r.output.lower(), (
        f"the message should say where nothing was found: {r.output!r}"
    )


def test_refresh_all_with_slugs_still_exits_clean(monkeypatch):
    """The empty guard must not fire on a genuinely clean sweep."""
    monkeypatch.setattr(
        _cli_corpus,
        "refresh_all",
        lambda: [
            {"slug": "aaa", "status": "unchanged", "detail": ""},
            {"slug": "bbb", "status": "changed", "detail": ""},
        ],
    )
    r = runner.invoke(app, ["refresh", "--all"])
    assert r.exit_code == 0
    assert "1 changed" in r.output and "1 unchanged" in r.output


@pytest.mark.parametrize("cmd", [["audit"], ["refresh"], ["localize"], ["renormalize"]])
def test_a_slug_that_folds_to_nothing_exits_2(cmd, monkeypatch, tmp_path):
    """`2` means "could not proceed with what it was given" — the same code a
    folds-to-nothing `--slug` gets at ingest, not `1` ("ran, found problems")."""
    monkeypatch.setattr(cfg, "INCOMING_DIR", str(tmp_path))
    r = runner.invoke(app, [*cmd, ".."])
    assert r.exit_code == 2, f"{cmd[0]} '..' exited {r.exit_code}: {r.output!r}"


def test_ingest_replace_reaches_run_ingest_and_defaults_off(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run_ingest(url, **kwargs):
        captured.update(kwargs)
        return {
            "pattern": "fake",
            "slug": "s",
            "kind": "html",
            "clean": str(tmp_path / "s.html"),
            "images": 0,
            "pages": 1,
            "bytes": 10,
            "changed": True,
            "duplicate_of": None,
        }

    monkeypatch.setattr(_cli_ingest, "run_ingest", fake_run_ingest)
    assert runner.invoke(app, ["ingest", "https://x"]).exit_code == 0
    assert captured.get("replace") is False

    assert runner.invoke(app, ["ingest", "https://x", "--replace"]).exit_code == 0
    assert captured.get("replace") is True


def _spec_file(path, title):
    path.parent.mkdir(parents=True, exist_ok=True)
    spec = {
        "openapi": "3.0.0",
        "info": {"title": title, "version": "1"},
        "paths": {"/ping": {"get": {"summary": "ping"}}},
    }
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def test_ingest_refused_slug_takeover_exits_2(monkeypatch, tmp_path):
    """Two vendors' specs share a title, so the second lands on the first's slug:
    the refusal must reach the operator as exit 2 naming what is held, not as a
    traceback — and the held manual must still be there afterwards."""
    monkeypatch.setattr(cfg, "INCOMING_DIR", str(tmp_path / "incoming"))
    held = _spec_file(tmp_path / "vendor-a" / "openapi.json", "Vendor API")
    incoming = _spec_file(tmp_path / "vendor-b" / "openapi.json", "Vendor API")

    assert runner.invoke(app, ["ingest", str(held)]).exit_code == 0

    r = runner.invoke(app, ["ingest", str(incoming)])

    assert r.exit_code == 2
    assert "vendor-a" in r.output and "would delete it" in r.output
    slug_dir = next((tmp_path / "incoming").iterdir())
    assert manifest.read_manifest(slug_dir)["source_url"] == str(held)


def test_refresh_several_named_slugs(monkeypatch):
    calls: list = []

    def fake(slugs):
        calls.append(list(slugs))
        return [{"slug": s, "status": "unchanged", "detail": ""} for s in slugs]

    monkeypatch.setattr(_cli_corpus, "refresh_slugs", fake)
    r = runner.invoke(app, ["refresh", "aaa", "bbb"])
    assert r.exit_code == 0, r.output
    assert calls == [["aaa", "bbb"]]
    assert "aaa: unchanged" in r.output and "bbb: unchanged" in r.output


def test_refresh_named_slugs_exit_2_when_one_can_not_be_refreshed(monkeypatch):
    monkeypatch.setattr(
        _cli_corpus,
        "refresh_slugs",
        lambda slugs: [
            {"slug": "aaa", "status": "changed", "detail": ""},
            {"slug": "ghost", "status": "skipped", "detail": "no manifest — ingest it first"},
        ],
    )
    r = runner.invoke(app, ["refresh", "aaa", "ghost"])
    assert r.exit_code == 2
    assert "ghost: skipped" in r.output


def test_refresh_pattern_sweeps_those_patterns(monkeypatch):
    calls: list = []

    def fake(*, patterns=None):
        calls.append(patterns)
        return [{"slug": "bbb-pdf", "status": "unchanged", "detail": ""}]

    monkeypatch.setattr(_cli_corpus, "refresh_all", fake)
    r = runner.invoke(app, ["refresh", "--pattern", "pdf_url", "--pattern", "archive_download"])
    assert r.exit_code == 0, r.output
    assert calls == [{"pdf_url", "archive_download"}]


def test_refresh_pattern_that_names_no_registered_pattern_exits_2(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "INCOMING_DIR", str(tmp_path))
    r = runner.invoke(app, ["refresh", "--pattern", "pdf-url"])
    assert r.exit_code == 2
    assert "pdf-url" in r.output and "patterns" in r.output


def test_refresh_pattern_matching_no_slug_is_not_a_clean_sweep(monkeypatch):
    monkeypatch.setattr(_cli_corpus, "refresh_all", lambda *, patterns=None: [])
    r = runner.invoke(app, ["refresh", "--pattern", "api_spec"])
    assert r.exit_code == 2
    assert "api_spec" in r.output


@pytest.mark.parametrize("extra", [["--all"], ["--pattern", "pdf_url"]])
def test_refresh_refuses_slugs_mixed_with_a_sweep(extra):
    r = runner.invoke(app, ["refresh", "aaa", *extra])
    assert r.exit_code == 2
    assert "not both" in r.output


def test_classify_probe_reports_the_docs_probe_route(monkeypatch):
    from pagespring.patterns._detect import Detection

    monkeypatch.setattr(
        climod,
        "detect",
        lambda url: Detection("mkdocs", "meta", url, generator="mkdocs-1.6.1"),
    )
    r = runner.invoke(app, ["classify", "--probe", "https://docs.vendor.example/"])
    assert r.exit_code == 0, r.output
    assert r.output.splitlines() == [
        "docs_probe",
        "route    : mkdocs (via meta)",
        "generator: mkdocs-1.6.1",
    ]


def test_classify_probe_leaves_a_url_routed_pattern_unprobed(monkeypatch):
    monkeypatch.setattr(climod, "detect", lambda url: pytest.fail("no probe for a URL route"))
    r = runner.invoke(app, ["classify", "--probe", "https://x.example/manual.pdf"])
    assert r.exit_code == 0
    assert r.output.strip() == "pdf_url"


def test_classify_probe_exit_codes(monkeypatch):
    import urllib.error

    from pf_core.exceptions import InvalidInputError

    def unrecognized(url):
        raise InvalidInputError("unrecognized docs site: x")

    monkeypatch.setattr(climod, "detect", unrecognized)
    r = runner.invoke(app, ["classify", "--probe", "https://docs.vendor.example/"])
    assert r.exit_code == 2 and "unrecognized" in r.output

    def unreachable(url):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(climod, "detect", unreachable)
    r = runner.invoke(app, ["classify", "--probe", "https://docs.vendor.example/"])
    assert r.exit_code == 4 and "no route to host" in r.output


def test_refresh_a_failed_source_outranks_a_skipped_slug(monkeypatch):
    monkeypatch.setattr(
        _cli_corpus,
        "refresh_slugs",
        lambda slugs: [
            {"slug": "dead", "status": "failed", "detail": "connection refused"},
            {"slug": "ghost", "status": "skipped", "detail": "no manifest — ingest it first"},
        ],
    )
    r = runner.invoke(app, ["refresh", "dead", "ghost"])
    assert r.exit_code == 1


def test_classify_probe_marks_an_oversize_page_as_unverified(monkeypatch):
    from pf_core.exceptions import ClientError

    from pagespring.patterns._detect import Detection

    oversize = ClientError("response exceeded max_bytes", context={"max_bytes": 1})
    monkeypatch.setattr(
        climod, "detect", lambda url: Detection("pdf", "oversize_body", url, oversize=oversize)
    )
    r = runner.invoke(app, ["classify", "--probe", "https://docs.vendor.example/all"])
    assert r.exit_code == 0
    assert "route    : pdf (via oversize_body" in r.output
    assert "ingest checks it is a PDF" in r.output


_STALLED_LOCALIZE = """
import sys, time
from pathlib import Path

from pagespring import cli, http

started = Path(sys.argv[1])

def stall(url, **kwargs):
    started.write_text("fetching", encoding="utf-8")
    time.sleep(60)
    raise AssertionError("the signal never arrived")

http.fetch_bytes_meta = stall
http.polite_sleep = lambda *a, **k: None
sys.argv = ["pagespring", "localize", "bk"]
cli.main()
"""


@pytest.mark.skipif(not hasattr(signal, "SIGTERM") or os.name == "nt", reason="POSIX signals")
def test_a_sigterm_mid_localize_keeps_the_integrity_record(tmp_path):
    """`timeout` stops a pass with SIGTERM; the pass's own record must still land, or
    later damage audits as unverifiable and the next localize runs over it."""
    slug_dir = tmp_path / "incoming" / "bk"
    (slug_dir / "images").mkdir(parents=True)
    (slug_dir / "images" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\nx")
    doc = slug_dir / "bk.html"
    doc.write_text('<img src="images/a.png"><img src="https://img.example/b.png">', "utf-8")
    staged_sha = "0" * 64
    manifest.write_manifest(
        slug_dir,
        manifest.build_manifest(
            source_url="https://x",
            pattern="fake",
            slug="bk",
            kind="html",
            deliverable="bk.html",
            pages=1,
            size_bytes=doc.stat().st_size,
            sha256=staged_sha,
            images=1,
            ingested_at="2026-09-22T00:00:00Z",
            localized_sha256=manifest.sha256_file(doc),
        ),
    )
    script = tmp_path / "stalled_localize.py"
    script.write_text(_STALLED_LOCALIZE, encoding="utf-8")
    started = tmp_path / "started"
    src = str(pathlib.Path(climod.__file__).resolve().parents[1])
    env = {**os.environ, "INCOMING_DIR": str(tmp_path / "incoming")}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [src, env.get("PYTHONPATH")]))

    proc = subprocess.Popen(
        [sys.executable, str(script), str(started)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 30
        while not started.exists():
            assert proc.poll() is None, proc.communicate()[1].decode()
            assert time.monotonic() < deadline, "the pass never reached its fetch"
            time.sleep(0.02)
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=30)
    finally:
        proc.kill()
        proc.communicate()

    recorded = manifest.read_manifest(slug_dir)["localized_sha256"]
    assert recorded == manifest.sha256_file(doc)
    assert proc.returncode == 128 + signal.SIGTERM


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="POSIX signals")
def test_a_signal_the_caller_ignores_stays_ignored():
    """`nohup` ignores SIGHUP so a run outlives its terminal; unwinding on it would
    end the run nohup was asked to keep alive."""
    saved = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGHUP)}
    try:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        climod._unwind_on_termination()
        assert signal.getsignal(signal.SIGHUP) is signal.SIG_IGN
        with pytest.raises(SystemExit) as exc:
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        assert exc.value.code == 128 + signal.SIGTERM
    finally:
        for s, handler in saved.items():
            signal.signal(s, handler)


@pytest.fixture
def batch_ingest(monkeypatch):
    """`ingest --batch` over a fake run_ingest that fails any URL naming 'dead'."""
    from pagespring import batch, http

    calls: list[str] = []

    def fake_run_ingest(url, **kwargs):
        calls.append(url)
        if "dead" in url:
            raise AcquireError(url, "HTTP Error 404: Not Found")
        return {
            "pattern": "fake",
            "slug": url.rstrip("/").rsplit("/", 1)[-1],
            "kind": "html",
            "clean": "/x",
            "pages": 3,
            "bytes": 2048,
            "images": 0,
            "images_downloaded": 0,
            "changed": "current" not in url,
            "duplicate_of": "held" if "twin" in url else None,
        }

    monkeypatch.setattr(batch, "run_ingest", fake_run_ingest)
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)
    return calls


def test_ingest_batch_prints_each_line_and_a_summary(batch_ingest, tmp_path):
    urls = tmp_path / "urls.txt"
    urls.write_text(
        "# vendor manuals\nhttps://a.example/docs\n\nhttps://b.example/current\n"
        "https://c.example/twin\nhttps://a.example/docs\n",
        encoding="utf-8",
    )

    r = runner.invoke(app, ["ingest", "--batch", str(urls), "--if-changed"])

    assert r.exit_code == 0, r.output
    assert batch_ingest == [
        "https://a.example/docs",
        "https://b.example/current",
        "https://c.example/twin",
    ]
    assert "line 2: https://a.example/docs → docs: staged (3 pages, 2.0 KB)" in r.output
    assert "line 4: https://b.example/current → current: unchanged" in r.output
    assert "content identical to incoming/held/" in r.output
    assert "line 6: https://a.example/docs: skipped — repeats line 2" in r.output
    assert r.output.rstrip().endswith("4 URLs: 2 staged, 1 unchanged, 1 skipped")


def test_ingest_batch_exits_1_when_a_line_failed(batch_ingest, tmp_path):
    urls = tmp_path / "urls.txt"
    urls.write_text("https://dead.example/x\nhttps://a.example/docs\n", encoding="utf-8")

    r = runner.invoke(app, ["ingest", "--batch", str(urls)])

    assert r.exit_code == 1
    assert "line 1: https://dead.example/x: failed — HTTP Error 404: Not Found" in r.output
    assert "2 URLs: 1 staged, 1 failed" in r.output
    assert "failed lines: 1" in r.output


@pytest.mark.parametrize("content", ["# nothing staged yet\n\n", None])
def test_ingest_batch_of_a_file_without_urls_or_unreadable_exits_2(batch_ingest, tmp_path, content):
    urls = tmp_path / "urls.txt"
    if content is not None:
        urls.write_text(content, encoding="utf-8")

    r = runner.invoke(app, ["ingest", "--batch", str(urls)])

    assert r.exit_code == 2
    assert "urls.txt" in r.output
    assert batch_ingest == []


@pytest.mark.parametrize(
    "extra", [["https://a.example/docs"], ["--slug", "tidy"]], ids=["url", "slug"]
)
def test_ingest_batch_refuses_a_url_or_slug_beside_it(batch_ingest, tmp_path, extra):
    urls = tmp_path / "urls.txt"
    urls.write_text("https://a.example/docs\n", encoding="utf-8")

    r = runner.invoke(app, ["ingest", "--batch", str(urls), *extra])

    assert r.exit_code == 2
    assert batch_ingest == []


def test_ingest_without_a_url_or_batch_exits_2(batch_ingest):
    r = runner.invoke(app, ["ingest"])

    assert r.exit_code == 2
    assert "--batch" in r.output


@pytest.mark.parametrize(
    ("pages", "shown"), [(1, "(1 page, 2.0 KB)"), (None, "(2.0 KB)"), (7, "(7 pages, 2.0 KB)")]
)
def test_a_staged_batch_line_counts_pages_in_words(pages, shown):
    result = {"slug": "s", "pages": pages, "bytes": 2048, "duplicate_of": None}
    outcome = {"line": 1, "url": "u", "status": "staged", "detail": "", "result": result}

    assert _cli_ingest._batch_line(outcome) == f"line 1: u → s: staged {shown}"
