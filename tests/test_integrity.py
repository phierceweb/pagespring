"""_integrity — a killed image pass's progress told apart from damage."""

import os
import pathlib
import signal
import subprocess
import sys
import time

import pytest
from pf_core.exceptions import PreconditionError

from pagespring import (
    _image_cache,
    _integrity,
    audit,
    http,
    images,
    localize,
    manifest,
    orchestrate,
)
from pagespring.base import AcquireResult
from pagespring.config import cfg

_PNG = b"\x89PNG\r\n\x1a\nx"
_REMOTE = ("https://img.example/b.png", "https://img.example/c.png")
_BODY = '<h1>Bk</h1><img src="images/a.png">' + "".join(f'<img src="{u}">' for u in _REMOTE)


@pytest.fixture(autouse=True)
def _incoming_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "INCOMING_DIR", str(tmp_path / "incoming"))


@pytest.fixture
def images_served(monkeypatch):
    monkeypatch.setattr(
        http, "fetch_bytes_meta", lambda u, **k: (u, _PNG, {"etag": None, "last_modified": None})
    )
    monkeypatch.setattr(http, "polite_sleep", lambda *a, **k: None)


def _localized_slug(tmp_path, body=_BODY):
    """A slug an earlier pass localized in part and recorded; remote refs remain."""
    slug_dir = tmp_path / "incoming" / "bk"
    (slug_dir / "images").mkdir(parents=True)
    (slug_dir / "images" / "a.png").write_bytes(_PNG)
    doc = slug_dir / "bk.html"
    doc.write_text(body, encoding="utf-8")
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
            sha256="0" * 64,
            images=1,
            ingested_at="2026-09-22T00:00:00Z",
            localized_sha256=manifest.sha256_file(doc),
        ),
    )
    return slug_dir, doc


def _open_pass(slug_dir):
    _integrity.open_for_image_pass(slug_dir, manifest.read_manifest(slug_dir))


def _checkpoint_one_image(doc):
    (doc.parent / "images").mkdir(exist_ok=True)
    (doc.parent / "images" / "b.png").write_bytes(_PNG)
    text = doc.read_text(encoding="utf-8")
    doc.write_text(text.replace(_REMOTE[0], "images/b.png"), encoding="utf-8")


def _killed_after_a_checkpoint(slug_dir, doc):
    """What SIGKILL leaves once a pass has checkpointed one image: nothing after it ran."""
    _open_pass(slug_dir)
    _checkpoint_one_image(doc)


def _state(slug_dir):
    return _integrity.integrity(slug_dir, manifest.read_manifest(slug_dir))


def _checks(slug):
    return [(f["check"], f["level"]) for f in audit.audit_slug(slug)]


def test_a_pass_killed_before_its_first_write_leaves_the_file_intact(tmp_path):
    slug_dir, _doc = _localized_slug(tmp_path)

    _open_pass(slug_dir)

    assert _state(slug_dir) == "intact"


def test_a_pass_killed_after_a_checkpoint_resumes(tmp_path, images_served):
    slug_dir, doc = _localized_slug(tmp_path)
    _killed_after_a_checkpoint(slug_dir, doc)

    assert _state(slug_dir) == "interrupted"
    assert _integrity.deliverable_intact(slug_dir, manifest.read_manifest(slug_dir))
    res = localize.localize_images("bk")

    assert res["remaining"] == 0
    assert manifest.read_manifest(slug_dir)["localized_sha256"] == manifest.sha256_file(doc)
    assert _checks("bk") == []


@pytest.mark.parametrize("damage", ["truncated", "edited"])
def test_damage_after_a_killed_pass_is_still_refused(tmp_path, images_served, damage):
    slug_dir, doc = _localized_slug(tmp_path)
    _killed_after_a_checkpoint(slug_dir, doc)
    text = doc.read_text(encoding="utf-8")
    doc.write_text(text[:40] if damage == "truncated" else text.replace("Bk", "Bx"), "utf-8")

    assert not _integrity.deliverable_intact(slug_dir, manifest.read_manifest(slug_dir))
    assert ("sha_mismatch", "error") in _checks("bk")
    with pytest.raises(PreconditionError, match="re-ingest"):
        localize.localize_images("bk")


class _RemoteImagesPattern:
    name = "fake"

    def match(self, url):
        return True

    def acquire(self, url, workdir):
        (workdir / "raw").mkdir(parents=True, exist_ok=True)
        return AcquireResult(raw_dir=workdir / "raw", kind="html", slug="bk", pages=1)

    def normalize(self, acq, workdir):
        clean = workdir / "bk.html"
        clean.write_text("".join(f'<img src="{u}">' for u in _REMOTE), encoding="utf-8")
        return clean


def test_an_ingest_killed_mid_image_pass_is_resumable(tmp_path, monkeypatch, images_served):
    monkeypatch.setattr(orchestrate, "classify", lambda url: _RemoteImagesPattern())
    slug_dir = tmp_path / "incoming" / "bk"
    on_disk_at_kill = {}

    def killed_after_a_checkpoint(doc, images_dir, **kwargs):
        _checkpoint_one_image(doc)
        on_disk_at_kill["manifest"] = (slug_dir / manifest.MANIFEST_NAME).read_bytes()
        raise KeyboardInterrupt

    with pytest.MonkeyPatch.context() as mp, pytest.raises(KeyboardInterrupt):
        mp.setattr(images, "download_images", killed_after_a_checkpoint)
        orchestrate.run_ingest("https://x", download_images=True)
    (slug_dir / manifest.MANIFEST_NAME).write_bytes(on_disk_at_kill["manifest"])

    assert _state(slug_dir) == "interrupted"
    assert localize.localize_images("bk")["remaining"] == 0
    assert _checks("bk") == []


def test_a_case_normalized_image_name_is_pass_progress(tmp_path):
    body = '<img src="images/Fig 1.PNG"><a href="images/Fig 1.PNG">Fig 1</a>'
    slug_dir, doc = _localized_slug(tmp_path, body)
    (slug_dir / "images" / "Fig 1.PNG").write_bytes(_PNG)
    _open_pass(slug_dir)

    _image_cache.normalize_case(doc, slug_dir)

    assert "images/fig 1.png" in doc.read_text(encoding="utf-8")
    assert _state(slug_dir) == "interrupted"


_STALLED_AFTER_A_CHECKPOINT = """
import sys, time
from functools import partial
from pathlib import Path

from pagespring import cli, http, images

started = Path(sys.argv[1])
images.download_images = partial(images.download_images, checkpoint_every=1)
fetched = []

def fetch(url, **kwargs):
    if fetched:
        started.write_text("checkpointed", encoding="utf-8")
        time.sleep(60)
        raise AssertionError("the kill never arrived")
    fetched.append(url)
    return url, b"\\x89PNG\\r\\n\\x1a\\nx", {"etag": None, "last_modified": None}

http.fetch_bytes_meta = fetch
http.polite_sleep = lambda *a, **k: None
sys.argv = ["pagespring", "localize", "bk"]
cli.main()
"""


def _run_until_checkpointed_then_kill(tmp_path):
    script = tmp_path / "stalled_localize.py"
    script.write_text(_STALLED_AFTER_A_CHECKPOINT, encoding="utf-8")
    started = tmp_path / "started"
    src = str(pathlib.Path(_integrity.__file__).resolve().parents[1])
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
            assert time.monotonic() < deadline, "the pass never reached its second fetch"
            time.sleep(0.02)
        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=30)
    finally:
        proc.kill()
        proc.communicate()
    return proc.returncode


@pytest.mark.skipif(not hasattr(signal, "SIGKILL"), reason="POSIX signals")
def test_a_sigkill_mid_pass_leaves_a_record_the_next_pass_resumes_from(tmp_path, images_served):
    slug_dir, doc = _localized_slug(tmp_path)

    assert _run_until_checkpointed_then_kill(tmp_path) == -signal.SIGKILL

    assert "images/b.png" in doc.read_text(encoding="utf-8")
    findings = _checks("bk")
    assert ("localize_interrupted", "warning") in findings
    assert not {"sha_mismatch", "sha_unverified"} & {check for check, _ in findings}
    assert localize.localize_images("bk")["remaining"] == 0
    assert manifest.read_manifest(slug_dir)["localized_sha256"] == manifest.sha256_file(doc)
