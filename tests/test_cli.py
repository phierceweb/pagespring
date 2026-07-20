"""CLI commands (patterns / classify / ingest / status), via Typer's runner."""

from typer.testing import CliRunner

import pagespring.cli as climod
from pagespring import manifest
from pagespring.cli import app
from pagespring.orchestrate import AcquireError, EmptyOutputError, NoPatternError

runner = CliRunner()


def test_patterns_lists_registered():
    r = runner.invoke(app, ["patterns"])
    assert r.exit_code == 0
    assert "apple_help" in r.output
    assert "gitbook" in r.output


def test_classify_routes_apple():
    r = runner.invoke(app, ["classify", "https://support.apple.com/guide/keynote/welcome/mac"])
    assert r.exit_code == 0
    assert "apple_help" in r.output


def test_classify_unknown():
    # docs_probe now claims every http(s) URL — "no pattern" is only reachable
    # for a non-web argument (a local file path / file:// URL).
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

    monkeypatch.setattr(climod, "run_ingest", fake_run_ingest)
    r = runner.invoke(app, ["ingest", "https://support.apple.com/guide/keynote/welcome/mac"])
    assert r.exit_code == 0
    assert "apple_help" in r.output
    assert "incoming" in r.output.lower()
    assert "187" in r.output  # crawl scale visible at a glance
    assert "1.1 MB" in r.output


def test_ingest_no_pattern_exits_2(monkeypatch):
    def fake_run_ingest(url, **kwargs):
        raise NoPatternError(url)

    monkeypatch.setattr(climod, "run_ingest", fake_run_ingest)
    r = runner.invoke(app, ["ingest", "https://example.com/x"])
    assert r.exit_code == 2
    assert "no pattern matched" in r.output.lower()


def test_ingest_empty_output_exits_3(monkeypatch):
    def fake_run_ingest(url, **kwargs):
        raise EmptyOutputError(url)

    monkeypatch.setattr(climod, "run_ingest", fake_run_ingest)
    r = runner.invoke(app, ["ingest", "https://example.com/x"])
    assert r.exit_code == 3
    assert "empty" in r.output.lower()


def test_ingest_fetch_failure_exits_4(monkeypatch):
    def fake_run_ingest(url, **kwargs):
        raise AcquireError(url, "HTTP Error 404: Not Found")

    monkeypatch.setattr(climod, "run_ingest", fake_run_ingest)
    r = runner.invoke(app, ["ingest", "https://docs.x.com"])
    assert r.exit_code == 4
    assert "fetch failed" in r.output.lower()
    assert "404" in r.output


def test_status_reports_incoming_slugs(monkeypatch, tmp_path):
    """status: one row per incoming/<slug>/ with its deliverable file + size.
    No corpus/converted column — that's pagespeak's side, downstream."""
    monkeypatch.setattr(climod.cfg, "INCOMING_DIR", str(tmp_path / "incoming"))
    (tmp_path / "incoming" / "keynote").mkdir(parents=True)
    (tmp_path / "incoming" / "keynote" / "keynote.html").write_text("<h1>K</h1>", encoding="utf-8")
    (tmp_path / "incoming" / "numbers").mkdir()
    (tmp_path / "incoming" / "numbers" / "numbers.md").write_text("# N", encoding="utf-8")

    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0
    keynote = next(line for line in r.output.splitlines() if "keynote" in line)
    assert "keynote.html" in keynote
    assert any("numbers.md" in line for line in r.output.splitlines())
    assert "converted" not in r.output  # corpus column dropped


def test_status_empty_incoming(monkeypatch, tmp_path):
    monkeypatch.setattr(climod.cfg, "INCOMING_DIR", str(tmp_path / "incoming"))
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

    monkeypatch.setattr(climod, "run_ingest", fake_run_ingest)
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

    monkeypatch.setattr(climod, "run_ingest", fake_run_ingest)
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

    monkeypatch.setattr(climod, "run_renormalize", fake_run_renormalize)
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

    monkeypatch.setattr(climod, "run_renormalize", fake_run_renormalize)
    r = runner.invoke(app, ["renormalize", "docs"])
    assert r.exit_code == 0
    assert "unchanged" in r.output.lower()


def test_renormalize_precondition_exits_2(monkeypatch):
    from pf_core.exceptions import PreconditionError

    def fake_run_renormalize(slug):
        raise PreconditionError(f"no raw/ kept for incoming/{slug}/ — re-ingest with --keep-raw")

    monkeypatch.setattr(climod, "run_renormalize", fake_run_renormalize)
    r = runner.invoke(app, ["renormalize", "helpsite"])
    assert r.exit_code == 2
    assert "--keep-raw" in r.output


def test_renormalize_empty_output_exits_3(monkeypatch):
    def fake_run_renormalize(slug):
        raise EmptyOutputError(slug)

    monkeypatch.setattr(climod, "run_renormalize", fake_run_renormalize)
    r = runner.invoke(app, ["renormalize", "helpsite"])
    assert r.exit_code == 3
    assert "empty" in r.output.lower()


def test_refresh_all_prints_report_and_summary(monkeypatch):
    outcomes = [
        {"slug": "aaa", "status": "changed", "detail": ""},
        {"slug": "bbb", "status": "unchanged", "detail": ""},
        {"slug": "ccc", "status": "unchanged", "detail": "not modified (validator probe)"},
    ]
    monkeypatch.setattr(climod, "refresh_all", lambda: outcomes)
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
    monkeypatch.setattr(climod, "refresh_all", lambda: outcomes)
    r = runner.invoke(app, ["refresh", "--all"])
    assert r.exit_code == 1
    assert "bbb: failed — connection refused" in r.output
    assert "1 failed" in r.output


def test_refresh_single_slug_skipped_exits_2(monkeypatch):
    monkeypatch.setattr(
        climod,
        "refresh_slug",
        lambda s: {"slug": s, "status": "skipped", "detail": "no manifest — ingest it first"},
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
    monkeypatch.setattr(climod, "audit_all", lambda: results)
    r = runner.invoke(app, ["audit", "--all"])
    assert r.exit_code == 0  # report-only by default
    assert "aaa: ok" in r.output
    assert "bbb: sha_mismatch (error) — content differs" in r.output
    assert "ccc: no_headings (warning) — 40 pages, 0 headings" in r.output
    assert "1 error, 1 warning" in r.output


def test_audit_strict_exits_1_on_errors(monkeypatch):
    results = [("bbb", [{"check": "deliverable_empty", "level": "error", "detail": "0 bytes"}])]
    monkeypatch.setattr(climod, "audit_all", lambda: results)
    r = runner.invoke(app, ["audit", "--all", "--strict"])
    assert r.exit_code == 1


def test_audit_strict_passes_on_warnings_only(monkeypatch):
    results = [("ccc", [{"check": "no_headings", "level": "warning", "detail": "…"}])]
    monkeypatch.setattr(climod, "audit_all", lambda: results)
    r = runner.invoke(app, ["audit", "--all", "--strict"])
    assert r.exit_code == 0


def test_audit_single_slug(monkeypatch):
    monkeypatch.setattr(climod, "audit_slug", lambda s: [])
    r = runner.invoke(app, ["audit", "keynote"])
    assert r.exit_code == 0
    assert "keynote: ok" in r.output


def test_audit_requires_slug_or_all():
    r = runner.invoke(app, ["audit"])
    assert r.exit_code == 2


def test_localize_command_reports_done(monkeypatch):
    monkeypatch.setattr(
        climod,
        "localize_images",
        lambda s: {"slug": s, "localized": 5, "remaining": 0, "images_total": 5},
    )
    r = runner.invoke(app, ["localize", "biology-2e"])
    assert r.exit_code == 0
    assert "biology-2e" in r.output
    assert "5" in r.output
    assert "done" in r.output.lower()


def test_localize_command_reports_remaining(monkeypatch):
    """When images remain (a big book exceeded one pass), the output says re-run."""
    monkeypatch.setattr(
        climod,
        "localize_images",
        lambda s: {"slug": s, "localized": 50, "remaining": 120, "images_total": 50},
    )
    r = runner.invoke(app, ["localize", "biology-2e"])
    assert r.exit_code == 0
    assert "120" in r.output
    assert "re-run" in r.output.lower()


def test_localize_all_iterates_incoming(monkeypatch, tmp_path):
    monkeypatch.setattr(climod.cfg, "INCOMING_DIR", str(tmp_path / "incoming"))
    (tmp_path / "incoming" / "a").mkdir(parents=True)
    (tmp_path / "incoming" / "b").mkdir()
    calls: list[str] = []

    def fake(slug):
        calls.append(slug)
        return {"slug": slug, "localized": 1, "remaining": 0, "images_total": 1}

    monkeypatch.setattr(climod, "localize_images", fake)
    r = runner.invoke(app, ["localize", "--all"])
    assert r.exit_code == 0
    assert sorted(calls) == ["a", "b"]


def test_localize_requires_slug_or_all():
    r = runner.invoke(app, ["localize"])
    assert r.exit_code == 2


def _write_manifest(slug_dir, **over):
    slug_dir.mkdir(parents=True, exist_ok=True)
    fields = {
        "source_url": "https://docs.tableplus.com/",
        "pattern": "gitbook",
        "slug": slug_dir.name,
        "kind": "markdown",
        "deliverable": "docs-tableplus-com.md",
        "convert_recipe": ["--split-sections"],
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

    monkeypatch.setattr(climod, "run_ingest", fake_run_ingest)
    r = runner.invoke(app, ["ingest", "https://x.com/thing.json"])
    assert r.exit_code == 2
    assert "not a recognizable" in r.output.lower()


def test_status_reads_manifest(monkeypatch, tmp_path):
    """status surfaces the manifest's pattern, pages, source host, and date —
    and never reports manifest.json itself as the deliverable."""
    monkeypatch.setattr(climod.cfg, "INCOMING_DIR", str(tmp_path / "incoming"))
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
