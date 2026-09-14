"""Suite-wide guards protecting the real corpus.

`INCOMING_DIR` defaults to the relative ``incoming``, which resolves to the real
corpus whenever pytest runs from the repo root. `incoming/` is gitignored, so a
test that writes or deletes there destroys manuals with no copy to recover.
"""

import functools
import os
import shutil
from pathlib import Path

import pf_core.fetch.images as core_images
import pytest

from pagespring import _staging, images, manifest, orchestrate
from pagespring.config import cfg

# Read before `_sandbox_incoming_dir` rewrites it: a corpus configured elsewhere
# (PAGESPRING_ENV_FILE / INCOMING_DIR) is as unrecoverable as the repo one.
_PROTECTED = {
    Path(__file__).resolve().parents[1] / "incoming",
    Path(cfg.INCOMING_DIR).resolve(),
}


def _refuse_if_protected(verb, target):
    resolved = Path(target).resolve()
    for corpus in _PROTECTED:
        if resolved == corpus or corpus in resolved.parents:
            raise AssertionError(
                f"test tried to {verb} {resolved}, inside the real corpus — "
                "point cfg.INCOMING_DIR at a tmp dir"
            )


def _guarded(fn, verb, pos, kw=None):
    """`fn` refused when its path argument — positional `pos`, or keyword `kw` —
    lands inside a protected corpus."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        _refuse_if_protected(verb, kwargs[kw] if kw and kw in kwargs else args[pos])
        return fn(*args, **kwargs)

    return wrapper


@pytest.fixture(autouse=True, scope="session")
def _sandbox_incoming_dir(tmp_path_factory):
    """Point `INCOMING_DIR` away from the corpus for every test in the suite.

    Per-file fixtures still narrow it to their own `tmp_path`; this only stops a
    file that omits one from falling back to the real thing.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(cfg, "INCOMING_DIR", str(tmp_path_factory.mktemp("incoming")))
        yield


@pytest.fixture(autouse=True, scope="session")
def _refuse_to_touch_the_corpus():
    """Reject any delete or overwrite aimed at the real corpus, whatever
    `INCOMING_DIR` says.

    At the point of danger, not a setup-time assertion: a per-file fixture
    redirecting wrongly runs after conftest's setup, so a check there cannot fail.
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            orchestrate,
            "_clear_except",
            _guarded(orchestrate._clear_except, "clear", 0, "directory"),
        )
        mp.setattr(shutil, "rmtree", _guarded(shutil.rmtree, "delete", 0, "path"))
        mp.setattr(shutil, "copy2", _guarded(shutil.copy2, "overwrite", 1, "dst"))
        mp.setattr(shutil, "copytree", _guarded(shutil.copytree, "overwrite", 1, "dst"))
        mp.setattr(Path, "unlink", _guarded(Path.unlink, "delete", 0))
        mp.setattr(Path, "write_text", _guarded(Path.write_text, "overwrite", 0))
        mp.setattr(Path, "write_bytes", _guarded(Path.write_bytes, "overwrite", 0))
        # Atomic writes land through `os.replace`, reaching none of the guards above,
        # and each module imported the name directly — so patch per binding site.
        for module, attr in (
            (manifest, "atomic_write_text"),
            (_staging, "atomic_write_bytes"),
            (images, "atomic_write_text"),
            (images, "atomic_write_bytes"),
            (core_images, "atomic_write_text"),
            (core_images, "atomic_write_bytes"),
        ):
            mp.setattr(module, attr, _guarded(getattr(module, attr), "overwrite", 0, "path"))
        yield


@pytest.fixture
def env_sandbox(monkeypatch):
    """A private `os.environ` for tests that load a settings file.

    `load_dotenv` writes the loaded file's keys into the real environment, and an
    inherited key out-ranks the file under test.
    """
    settings = ("INCOMING_DIR", "CRAWL_STALL_AFTER_S")
    monkeypatch.setattr(os, "environ", {k: v for k, v in os.environ.items() if k not in settings})
