"""conftest's corpus guard — the suite's last line of defence.

`incoming/` is gitignored, so whatever the guard misses is destroyed with no copy
to recover. Everything here is aimed at a stand-in corpus: a guard that has
stopped working must fail this file, not prove it by deleting a manual.
"""

import shutil
from pathlib import Path

import conftest
import pf_core.fetch.images as core_images
import pytest

from pagespring import _staging, images, manifest, orchestrate

_DOC = "<h1>real</h1>"


@pytest.fixture
def stand_in_corpus(tmp_path, monkeypatch):
    """A tmp corpus the guard protects exactly as it protects `incoming/`."""
    corpus = tmp_path / "incoming"
    (corpus / "precious").mkdir(parents=True)
    (corpus / "precious" / "precious.html").write_text(_DOC, encoding="utf-8")
    monkeypatch.setattr(conftest, "_PROTECTED", {corpus.resolve()})
    return corpus


def test_the_repo_corpus_is_protected():
    assert Path(__file__).resolve().parents[1] / "incoming" in conftest._PROTECTED


def test_deleting_inside_the_corpus_is_refused(stand_in_corpus):
    doc = stand_in_corpus / "precious" / "precious.html"

    with pytest.raises(AssertionError, match="real corpus"):
        doc.unlink()
    with pytest.raises(AssertionError, match="real corpus"):
        shutil.rmtree(stand_in_corpus / "precious")
    with pytest.raises(AssertionError, match="real corpus"):
        orchestrate._clear_except(stand_in_corpus / "precious", keep=set())

    assert doc.read_text(encoding="utf-8") == _DOC


def test_overwriting_inside_the_corpus_is_refused(stand_in_corpus, tmp_path):
    doc = stand_in_corpus / "precious" / "precious.html"
    replacement = tmp_path / "replacement.html"
    replacement.write_text("<h1>DIFFERENT</h1>", encoding="utf-8")

    with pytest.raises(AssertionError, match="real corpus"):
        shutil.copy2(replacement, doc)
    with pytest.raises(AssertionError, match="real corpus"):
        shutil.copytree(tmp_path / "raw", stand_in_corpus / "precious" / "raw")
    with pytest.raises(AssertionError, match="real corpus"):
        doc.write_text("<h1>DIFFERENT</h1>", encoding="utf-8")
    with pytest.raises(AssertionError, match="real corpus"):
        (stand_in_corpus / "precious" / "images" / "fig1.png").write_bytes(b"x")
    with pytest.raises(AssertionError, match="real corpus"):
        _staging._stage_file(replacement, doc)

    assert doc.read_text(encoding="utf-8") == _DOC


def test_atomic_writes_inside_the_corpus_are_refused(stand_in_corpus):
    """The manifest, the image sidecar, the localized deliverable and every
    downloaded image are staged through pf-core's atomic writers, which reach the
    filesystem via `os.replace` — none of the `Path`/`shutil` calls guarded above.
    """
    slug = stand_in_corpus / "precious"

    with pytest.raises(AssertionError, match="real corpus"):
        manifest.write_manifest(slug, _manifest())
    with pytest.raises(AssertionError, match="real corpus"):
        images.write_sidecar(slug, [])
    with pytest.raises(AssertionError, match="real corpus"):
        core_images.atomic_write_bytes(slug / "images" / "fig1.png", b"x")
    with pytest.raises(AssertionError, match="real corpus"):
        core_images.atomic_write_text(slug / "precious.html", "<h1>DIFFERENT</h1>")

    assert (slug / "precious.html").read_text(encoding="utf-8") == _DOC
    assert not (slug / manifest.MANIFEST_NAME).exists()
    assert not (slug / images.SIDECAR_NAME).exists()


def _manifest() -> manifest.Manifest:
    return manifest.build_manifest(
        source_url="https://example.test/",
        pattern="docs_probe",
        slug="precious",
        kind="html",
        deliverable="precious.html",
        pages=1,
        size_bytes=len(_DOC),
        sha256="d" * 64,
        images=0,
        ingested_at="2026-08-29T00:00:00Z",
    )


def test_the_guard_leaves_everything_outside_the_corpus_alone(stand_in_corpus, tmp_path):
    """The suite writes and deletes constantly; only the corpus is off limits."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "x.html").write_text("x", encoding="utf-8")
    (scratch / "x.html").unlink()
    shutil.rmtree(scratch)

    assert not scratch.exists()
