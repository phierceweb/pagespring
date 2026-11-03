"""`bin/check-release` — the tag, the packaged version and the CHANGELOG agree."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "bin" / "check-release"

_NOTES = "# Changelog\n\nIntro.\n\n## [1.2.0] — 2026-09-22\n\n### Fixed\n\n- x\n\n## [1.1.0] — 2026-09-01\n"


def _gate() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("check_release", str(GATE))
    spec = importlib.util.spec_from_loader("check_release", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _problems(**over: str) -> list[str]:
    args = {
        "tag": "v1.2.0",
        "pyproject_version": "1.2.0",
        "init_version": "1.2.0",
        "changelog": _NOTES,
        "commit_date": "2026-09-22",
        **over,
    }
    tag = args.pop("tag")
    result: list[str] = _gate().problems(tag, **args)
    return result


def test_a_consistent_release_passes():
    assert _problems() == []


@pytest.mark.parametrize(
    ("over", "needle"),
    [
        ({"tag": "v1.2.1"}, "pyproject.toml"),
        ({"init_version": "1.1.0"}, "__version__"),
        ({"changelog": _NOTES.replace("## [1.2.0]", "## [Unreleased]\n\n## [1.2.0]")}, "top"),
        ({"changelog": _NOTES.replace(" — 2026-09-22", "")}, "undated"),
        ({"commit_date": "2026-09-23"}, "2026-09-23"),
        ({"changelog": "# Changelog\n"}, "no ## ["),
        ({"changelog": _NOTES.replace("## [1.2.0]", "## Unreleased\n\n## [1.2.0]")}, "Unreleased"),
    ],
    ids=[
        "tag",
        "init",
        "unreleased-on-top",
        "undated",
        "wrong-date",
        "no-sections",
        "bare-heading",
    ],
)
def test_each_disagreement_is_named(over, needle):
    found = _problems(**over)
    assert len(found) == 1 and needle in found[0], found


def test_the_gate_needs_the_tag():
    result = subprocess.run(
        [sys.executable, str(GATE)], capture_output=True, text=True, cwd=ROOT, check=False
    )
    assert result.returncode == 2
    assert "usage" in result.stderr


def test_a_tag_off_mains_tip_is_refused():
    """Publish builds the tagged commit, so a tag behind main would ship code main has replaced."""
    found = _gate().problems(
        "v1.2.0",
        pyproject_version="1.2.0",
        init_version="1.2.0",
        changelog=_NOTES,
        commit_date="2026-09-22",
        release_sha="7b3bc59" + "0" * 33,
        main_sha="aa61759" + "0" * 33,
    )
    assert len(found) == 1 and "7b3bc59" in found[0] and "aa61759" in found[0], found


def test_a_tag_on_mains_tip_passes():
    sha = "aa61759" + "0" * 33
    found = _gate().problems(
        "v1.2.0",
        pyproject_version="1.2.0",
        init_version="1.2.0",
        changelog=_NOTES,
        commit_date="2026-09-22",
        release_sha=sha,
        main_sha=sha,
    )
    assert found == []


def test_the_gate_compares_with_the_named_main():
    result = subprocess.run(
        [sys.executable, str(GATE), "v0.0.0", "--main", "refs/heads/no-such-branch"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 2
    assert "no-such-branch" in result.stderr


def test_the_gate_refuses_a_head_that_is_not_the_named_mains_tip(monkeypatch, capsys):
    gate = _gate()
    shas = {"HEAD": "a" * 40, "release-main^{commit}": "b" * 40}

    def git(*args: str) -> str:
        if args[0] == "rev-parse":
            return shas[args[-1]]
        return "2026-09-22"

    monkeypatch.setattr(gate, "_git", git)
    pyproject = gate.ROOT / "pyproject.toml"
    version = re.search(r'^version = "([^"]+)"', pyproject.read_text(), re.M)[1]

    rc = gate.main(["check-release", f"v{version}", "--main", "release-main"])

    err = capsys.readouterr().err
    assert rc == 1 and "is not main's tip" in err, err
