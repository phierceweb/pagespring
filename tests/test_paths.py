"""paths — the one folding point between a command-line slug and a directory.

Every slug-taking command resolves through ``slug_dir``. The path it returns is
passed to ``shutil.rmtree`` and ``Path.unlink``, so these pin that no input can
name a directory outside ``incoming/``.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest
from pf_core.exceptions import InvalidInputError

from pagespring import audit, orchestrate, refresh
from pagespring import manifest as manifest_mod
from pagespring.config import cfg
from pagespring.paths import slug_dir

_ESCAPES = ["../../etc", "a/../../b", "~/x", "/etc/passwd", "sub/dir"]


def test_an_already_folded_slug_is_unchanged():
    """The corpus on disk is folded, so resolution must be a no-op for it."""
    assert slug_dir("adat-manual") == Path(cfg.INCOMING_DIR) / "adat-manual"


@pytest.mark.parametrize("slug", _ESCAPES)
def test_no_input_escapes_the_corpus(slug):
    resolved = slug_dir(slug).resolve()
    root = Path(cfg.INCOMING_DIR).resolve()
    assert resolved.parent == root, f"{slug!r} resolved outside the corpus: {resolved}"


@pytest.mark.parametrize("slug", ["..", ".", "///", "", "   "])
def test_a_slug_that_names_nothing_is_refused(slug):
    """Folding to empty must raise, not silently resolve to `incoming/` itself —
    that directory is the whole corpus."""
    with pytest.raises(InvalidInputError):
        slug_dir(slug)


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda s: audit.audit_slug(s), id="audit_slug"),
        pytest.param(lambda s: refresh.refresh_slug(s), id="refresh_slug"),
        pytest.param(lambda s: orchestrate.run_renormalize(s), id="run_renormalize"),
        pytest.param(lambda s: orchestrate.localize_images(s), id="localize_images"),
    ],
)
def test_every_slug_entry_point_folds_before_touching_disk(call, monkeypatch, tmp_path):
    """A traversal slug must never reach a path outside the corpus, whatever each
    entry point then does with it (renormalize unlinks; localize prunes).

    Asserted on the directory resolved, not on a surviving file: all four bail on
    a missing manifest, so an unguarded slug looks harmless until the traversal
    target happens to hold a manifest.json.
    """
    monkeypatch.setattr(cfg, "INCOMING_DIR", str(tmp_path))
    looked_at: list[Path] = []
    real = manifest_mod.read_manifest
    monkeypatch.setattr(
        manifest_mod,
        "read_manifest",
        lambda d: (looked_at.append(Path(d)), real(d))[1],
    )

    # Each reports differently — audit returns findings, renormalize raises.
    with contextlib.suppress(Exception):
        call("../outside")

    assert looked_at, "the entry point never resolved a slug directory"
    root = tmp_path.resolve()
    for d in looked_at:
        assert d.resolve().parent == root, f"resolved outside the corpus: {d}"
