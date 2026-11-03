"""An image whose source answers 404/410 with no cached copy is recorded as gone, so `localize` and
`audit` stop advising a re-run that cannot fetch it."""

import json
import time
import urllib.error
from email.message import Message

import pytest
from typer.testing import CliRunner

from pagespring import _cli_ingest, _image_cache, audit, http, images, manifest, orchestrate
from pagespring.cli import app
from pagespring.localize import localize_images

_PNG = b"\x89PNG\r\n\x1a\n" + b"pngbody"


def _meta():
    return {"etag": None, "last_modified": None}


def _answering(codes: dict[str, int]):
    def fetch(url, **kwargs):
        for tail, code in codes.items():
            if url.endswith(tail):
                raise urllib.error.HTTPError(url, code, "no", Message(), None)
        return url, _PNG, _meta()

    return fetch


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)


def _doc(tmp_path, body):
    doc = tmp_path / "d.md"
    doc.write_text(body, encoding="utf-8")
    return doc


@pytest.mark.parametrize("code", [404, 410])
def test_a_source_that_is_gone_is_recorded(tmp_path, monkeypatch, code):
    doc = _doc(tmp_path, "![a](https://x.com/gone.png)\n![b](https://x.com/ok.png)\n")
    monkeypatch.setattr(http, "fetch_bytes_meta", _answering({"gone.png": code}))

    images.download_images(doc, tmp_path / "images")

    assert images.read_gone(tmp_path) == ["https://x.com/gone.png"]
    assert images.gone_remote_images(doc, tmp_path) == 1


@pytest.mark.parametrize("code", [403, 429, 500, 503])
def test_a_failure_that_may_pass_is_not_recorded_as_gone(tmp_path, monkeypatch, code):
    doc = _doc(tmp_path, "![a](https://x.com/flaky.png)\n")
    monkeypatch.setattr(http, "fetch_bytes_meta", _answering({"flaky.png": code}))

    images.download_images(doc, tmp_path / "images")

    assert images.read_gone(tmp_path) == []
    assert images.gone_remote_images(doc, tmp_path) == 0


def test_an_unreachable_host_is_not_recorded_as_gone(tmp_path, monkeypatch):
    doc = _doc(tmp_path, "![a](https://x.com/a.png)\n")

    def down(url, **kwargs):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(http, "fetch_bytes_meta", down)
    images.download_images(doc, tmp_path / "images")

    assert images.read_gone(tmp_path) == []


@pytest.mark.parametrize("later", ["fetched", "failed"])
def test_a_later_answer_replaces_the_gone_mark(tmp_path, monkeypatch, later):
    """Only the last fetch counts: a source back up, or failing some other way, is worth a re-run."""
    doc = _doc(tmp_path, "![a](https://x.com/a.png)\n")
    monkeypatch.setattr(http, "fetch_bytes_meta", _answering({"a.png": 404}))
    images.download_images(doc, tmp_path / "images")
    assert images.read_gone(tmp_path) == ["https://x.com/a.png"]

    answer = {} if later == "fetched" else {"a.png": 503}
    monkeypatch.setattr(http, "fetch_bytes_meta", _answering(answer))
    images.download_images(doc, tmp_path / "images")

    assert images.read_gone(tmp_path) == []


def test_a_gone_mark_leaves_with_its_ref(tmp_path, monkeypatch):
    doc = _doc(tmp_path, "![a](https://x.com/a.png)\n![b](https://x.com/b.png)\n")
    monkeypatch.setattr(http, "fetch_bytes_meta", _answering({".png": 404}))
    images.download_images(doc, tmp_path / "images")
    assert images.read_gone(tmp_path) == ["https://x.com/a.png", "https://x.com/b.png"]

    doc.write_text("![b](https://x.com/b.png)\n", encoding="utf-8")
    images.download_images(doc, tmp_path / "images")

    assert images.read_gone(tmp_path) == ["https://x.com/b.png"]


def test_an_escaped_ref_counts_against_its_decoded_mark(tmp_path, monkeypatch):
    doc = _doc(tmp_path, "![a](https://x.com/a.png?w=1&amp;h=2)\n")
    monkeypatch.setattr(http, "fetch_bytes_meta", _answering({"h=2": 404}))

    images.download_images(doc, tmp_path / "images")

    assert images.read_gone(tmp_path) == ["https://x.com/a.png?w=1&h=2"]
    assert images.gone_remote_images(doc, tmp_path) == 1


def test_other_sidecar_writes_keep_the_gone_marks(tmp_path, monkeypatch):
    doc = _doc(tmp_path, "![a](https://x.com/gone.png)\n![b](https://x.com/ok.png)\n")
    monkeypatch.setattr(http, "fetch_bytes_meta", _answering({"gone.png": 404}))
    images.download_images(doc, tmp_path / "images")
    doc.write_text("![a](https://x.com/gone.png)\n", encoding="utf-8")

    assert _image_cache.prune_orphans(doc, tmp_path) == 1
    images.write_sidecar(tmp_path, images.read_sidecar(tmp_path))

    assert images.read_gone(tmp_path) == ["https://x.com/gone.png"]


def test_a_gone_ref_with_a_cached_copy_settles_and_is_not_counted(tmp_path, monkeypatch):
    doc = _doc(tmp_path, "![a](https://x.com/a.png)\n")
    monkeypatch.setattr(http, "fetch_bytes_meta", _answering({}))
    images.download_images(doc, tmp_path / "images")
    doc.write_text("![a](https://x.com/a.png)\n", encoding="utf-8")
    images.write_sidecar(tmp_path, images.read_sidecar(tmp_path), gone=["https://x.com/a.png"])
    monkeypatch.setattr(http, "fetch_bytes_meta", _answering({"a.png": 404}))

    assert _image_cache.reuse_unchanged(doc, tmp_path) == 1
    images.download_images(doc, tmp_path / "images")

    assert images.count_remote_images(doc) == 0
    assert images.read_gone(tmp_path) == []


# --- what localize and audit say ---


def _stage(tmp_path, body, *, image_count=1):
    d = tmp_path / "incoming" / "fakeapp"
    d.mkdir(parents=True)
    f = d / "fakeapp.md"
    f.write_text(body, encoding="utf-8")
    manifest.write_manifest(
        d,
        manifest.build_manifest(
            source_url="https://x/docs/fakeapp",
            pattern="fake",
            slug="fakeapp",
            kind="markdown",
            deliverable=f.name,
            pages=2,
            size_bytes=f.stat().st_size,
            sha256=manifest.sha256_file(f),
            images=image_count,
            ingested_at="2026-07-01T00:00:00Z",
        ),
    )
    return d


@pytest.fixture
def incoming(tmp_path, monkeypatch):
    monkeypatch.setattr(orchestrate.cfg, "INCOMING_DIR", str(tmp_path / "incoming"))
    return tmp_path / "incoming"


def test_localize_counts_the_gone_refs_apart(tmp_path, monkeypatch, incoming):
    _stage(tmp_path, "# T\n\n![a](https://x.com/gone.png)\n![b](https://x.com/flaky.png)\n")
    monkeypatch.setattr(http, "fetch_bytes_meta", _answering({"gone.png": 404, "flaky.png": 503}))

    r = localize_images("fakeapp")

    assert (r["remaining"], r["gone"]) == (2, 1)


def _checks(findings):
    return {f["check"]: f["detail"] for f in findings}


def test_audit_does_not_advise_a_rerun_for_a_gone_ref(tmp_path, monkeypatch, incoming):
    _stage(tmp_path, "# T\n\n![a](https://x.com/gone.png)\n![b](https://x.com/ok.png)\n")
    monkeypatch.setattr(http, "fetch_bytes_meta", _answering({"gone.png": 404}))
    localize_images("fakeapp")

    found = _checks(audit.audit_slug("fakeapp"))

    assert "localize_incomplete" not in found
    assert found["image_source_gone"].startswith("1 remote image ref(s)")
    assert "cannot fetch" in found["image_source_gone"]


def test_audit_does_not_advise_a_rerun_for_a_ref_the_localizer_declines(
    tmp_path, monkeypatch, incoming
):
    body = (
        "# T\n\n![a](https://[x/a.png)\n![b](https://x.com/page.html)\n![c](https://x.com/c.png)\n"
    )
    _stage(tmp_path, body)
    monkeypatch.setattr(http, "fetch_bytes_meta", _answering({}))
    assert localize_images("fakeapp")["remaining"] == 0

    found = _checks(audit.audit_slug("fakeapp"))

    assert "localize_incomplete" not in found
    assert found["image_ref_declined"].startswith("2 remote image ref(s)")


def test_a_ref_audits_matcher_cuts_short_is_not_declined(tmp_path, monkeypatch, incoming):
    body = (
        "# T\n\n![a](https://x.com/it's.png)\n![b](https://x.com/dead's.png)\n"
        '<img src="https://x.com/a b.png">\n'
    )
    _stage(tmp_path, body)
    monkeypatch.setattr(
        http, "fetch_bytes_meta", _answering({"it's.png": 503, "dead's.png": 404, "b.png": 503})
    )
    assert localize_images("fakeapp")["remaining"] == 3

    found = _checks(audit.audit_slug("fakeapp"))

    assert "image_ref_declined" not in found
    assert found["localize_incomplete"].startswith("2 remote image ref(s) remain")
    assert found["image_source_gone"].startswith("1 remote image ref(s)")


@pytest.mark.parametrize(
    ("code", "check"), [(503, "localize_incomplete"), (404, "image_source_gone")]
)
def test_audit_counts_one_url_written_two_ways_as_localize_does(
    tmp_path, monkeypatch, incoming, code, check
):
    body = '# T\n\n![a](https://x.com/a.png?p=1&amp;q=2)\n<img src="https://x.com/a.png?p=1&q=2">\n'
    _stage(tmp_path, body)
    monkeypatch.setattr(http, "fetch_bytes_meta", _answering({"q=2": code}))
    r = localize_images("fakeapp")

    found = _checks(audit.audit_slug("fakeapp"))

    assert r["remaining"] == 2
    assert found[check].startswith("2 remote image ref(s)")


@pytest.mark.parametrize(
    "body",
    [
        '![a](https://x.com/g.png junk)\n<img src="https://x.com/g.png">\n',
        "![b](https://x.com/p.png junk)\n![a](https://x.com/p.png?v=1)\n",
    ],
    ids=["same-url-taken-elsewhere", "prefix-of-a-taken-ref"],
)
def test_a_ref_the_localizer_never_takes_is_declined_wherever_its_text_recurs(
    tmp_path, monkeypatch, incoming, body
):
    _stage(tmp_path, f"# T\n\n{body}")
    monkeypatch.setattr(http, "fetch_bytes_meta", _answering({".png": 503, "v=1": 503}))
    localize_images("fakeapp")

    found = _checks(audit.audit_slug("fakeapp"))

    assert found["image_ref_declined"].startswith("1 remote image ref(s)")
    assert found["localize_incomplete"].startswith("1 remote image ref(s)")


def test_a_markdown_copy_a_pass_re_points_is_not_declined(tmp_path, monkeypatch, incoming):
    """The localizer's html finder takes the uppercase scheme; its rewrite re-points both copies."""
    _stage(tmp_path, '# T\n\n![a](HTTPS://x.com/u.png)\n<img src="HTTPS://x.com/u.png">\n')
    monkeypatch.setattr(http, "fetch_bytes_meta", _answering({"u.png": 503}))
    localize_images("fakeapp")

    found = _checks(audit.audit_slug("fakeapp"))

    assert "image_ref_declined" not in found
    assert found["localize_incomplete"].startswith("1 remote image ref(s)")


def test_declined_refs_are_counted_in_linear_time():
    doc = "".join(f'<img src="https://x.com/img {i}.png">\n' for i in range(8000))
    started = time.monotonic()
    audit._declined_refs(doc)
    assert time.monotonic() - started < 0.5


def test_audit_still_advises_a_rerun_for_the_rest(tmp_path, monkeypatch, incoming):
    _stage(tmp_path, "# T\n\n![a](https://x.com/gone.png)\n![b](https://x.com/flaky.png)\n")
    monkeypatch.setattr(http, "fetch_bytes_meta", _answering({"gone.png": 410, "flaky.png": 503}))
    localize_images("fakeapp")

    found = _checks(audit.audit_slug("fakeapp"))

    assert found["localize_incomplete"].startswith("1 remote image ref(s) remain")
    assert found["image_source_gone"].startswith("1 remote image ref(s)")


runner = CliRunner()


@pytest.mark.parametrize(
    ("remaining", "gone", "expected", "absent"),
    [
        (3, 3, "3 gone at the source", "re-run to continue"),
        (5, 2, "3 remaining — re-run to continue; 2 gone at the source", None),
        (4, 0, "4 remaining — re-run to continue", "gone"),
    ],
)
def test_localize_command_says_which_refs_a_rerun_can_fetch(
    monkeypatch, remaining, gone, expected, absent
):
    result = {
        "slug": "s",
        "localized": 0,
        "reused": 0,
        "pruned": 0,
        "remaining": remaining,
        "gone": gone,
        "images_total": 0,
    }
    monkeypatch.setattr(_cli_ingest, "localize_images", lambda s: result)

    r = runner.invoke(app, ["localize", "s"])

    assert r.exit_code == 0
    assert expected in r.output
    if absent:
        assert absent not in r.output


def test_audit_reports_gone_refs_when_no_image_was_ever_downloaded(tmp_path, monkeypatch, incoming):
    d = _stage(tmp_path, "# T\n\n![a](https://x.com/gone.png)\n", image_count=0)
    monkeypatch.setattr(http, "fetch_bytes_meta", _answering({"gone.png": 404}))
    localize_images("fakeapp")
    assert (d / "images").is_dir() and not any((d / "images").iterdir())

    assert "image_source_gone" in _checks(audit.audit_slug("fakeapp"))


def test_audit_matches_an_escaped_ref_to_its_decoded_mark(tmp_path, monkeypatch, incoming):
    _stage(tmp_path, "# T\n\n![a](https://x.com/a.png?w=1&amp;h=2)\n")
    monkeypatch.setattr(http, "fetch_bytes_meta", _answering({"h=2": 404}))
    localize_images("fakeapp")

    found = _checks(audit.audit_slug("fakeapp"))

    assert "localize_incomplete" not in found
    assert found["image_source_gone"].startswith("1 remote image ref(s)")


def test_an_interrupted_pass_keeps_every_mark_it_did_not_settle(tmp_path, monkeypatch):
    doc = _doc(tmp_path, "![a](https://x.com/a.png)\n![b](https://x.com/b.png)\n")
    monkeypatch.setattr(http, "fetch_bytes_meta", _answering({".png": 404}))
    images.download_images(doc, tmp_path / "images")

    def interrupted(url, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(http, "fetch_bytes_meta", interrupted)
    with pytest.raises(KeyboardInterrupt):
        images.download_images(doc, tmp_path / "images")

    assert images.read_gone(tmp_path) == ["https://x.com/a.png", "https://x.com/b.png"]


@pytest.mark.parametrize("gone", ["https://x.com/a.png", {"u": 1}, [1, "https://x.com/b.png"]])
def test_a_malformed_gone_list_reads_as_only_its_urls(tmp_path, gone):
    (tmp_path / images.SIDECAR_NAME).write_text(
        json.dumps({"schema_version": 1, "images": [], "gone": gone}), encoding="utf-8"
    )

    expected = ["https://x.com/b.png"] if isinstance(gone, list) else []
    assert images.read_gone(tmp_path) == expected
