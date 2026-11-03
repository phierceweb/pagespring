"""The Python gates in ``bin/`` re-run under the project venv when a bare ``python3`` is too old."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATES = [ROOT / "bin" / "check-framework", ROOT / "bin" / "check-release"]
SYSTEM_PYTHON = Path("/usr/bin/python3")


def _load(path: Path) -> ModuleType:
    loader = importlib.machinery.SourceFileLoader(path.name.replace("-", "_"), str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class _Execd(Exception):
    pass


@pytest.mark.parametrize("gate", GATES, ids=lambda p: p.name)
def test_an_old_python_reruns_the_gate_under_the_venv(gate, monkeypatch, tmp_path):
    module = _load(gate)
    venv = tmp_path / ".venv" / "bin" / "python"
    venv.parent.mkdir(parents=True)
    venv.write_text("")
    calls: list[tuple[object, list[str]]] = []

    def execv(path: object, args: list[str]) -> None:
        calls.append((path, args))
        raise _Execd

    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setenv(module._RERUN_MARK, "")
    monkeypatch.setattr(module.os, "execv", execv)
    monkeypatch.setattr(module.sys, "version_info", (3, 9, 6))
    monkeypatch.setattr(module.sys, "argv", [str(gate), "v1.0.0"])
    with pytest.raises(_Execd):
        module._rerun_under_venv()
    assert calls == [(venv, [str(venv), str(gate), "v1.0.0"])]
    assert module.os.environ[module._RERUN_MARK] == "1"


@pytest.mark.parametrize("gate", GATES, ids=lambda p: p.name)
def test_an_old_python_without_a_venv_says_what_to_run(gate, monkeypatch, tmp_path):
    module = _load(gate)
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module.sys, "version_info", (3, 9, 6))
    with pytest.raises(SystemExit) as exited:
        module._rerun_under_venv()
    assert str(exited.value.code).endswith("needs Python 3.12+: run bin/setup")


@pytest.mark.parametrize("gate", GATES, ids=lambda p: p.name)
def test_an_old_venv_python_is_refused_instead_of_rerun(gate, monkeypatch, tmp_path):
    module = _load(gate)
    venv = tmp_path / ".venv" / "bin" / "python"
    venv.parent.mkdir(parents=True)
    venv.write_text("")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setenv(module._RERUN_MARK, "1")
    monkeypatch.setattr(module.sys, "version_info", (3, 9, 6))
    monkeypatch.setattr(module.os, "execv", lambda *_: pytest.fail("re-ran itself"))
    with pytest.raises(SystemExit) as exited:
        module._rerun_under_venv()
    assert "remove .venv, then run bin/setup" in str(exited.value.code)


@pytest.mark.parametrize("gate", GATES, ids=lambda p: p.name)
def test_a_current_python_runs_the_gate_itself(gate, monkeypatch):
    module = _load(gate)
    monkeypatch.setattr(module.os, "execv", lambda *_: pytest.fail("re-ran"))
    module._rerun_under_venv()


def _old_system_python() -> bool:
    if not SYSTEM_PYTHON.is_file() or not (ROOT / ".venv" / "bin" / "python").is_file():
        return False
    out = subprocess.run(
        [str(SYSTEM_PYTHON), "-c", "import sys; print(sys.version_info < (3, 12))"],
        capture_output=True,
        text=True,
        check=False,
    )
    return out.stdout.strip() == "True"


needs_old_python = pytest.mark.skipif(
    not _old_system_python(), reason="needs a system python3 older than 3.12 and a venv"
)


@needs_old_python
def test_check_release_runs_on_the_system_python():
    """Past ``import tomllib``, which only the re-run can reach on an old python."""
    result = subprocess.run(
        [str(SYSTEM_PYTHON), str(GATES[1]), "v0.0.0", "--main", "HEAD"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 1, result.stderr
    assert "tag v0.0.0 != packaged" in result.stderr
    assert "Traceback" not in result.stderr


@needs_old_python
def test_check_framework_runs_on_the_system_python(tmp_path):
    source = tmp_path / "generic.py"
    source.write_text("def first[T](items: list[T]) -> T:\n    return items[0]\n")
    result = subprocess.run(
        [str(SYSTEM_PYTHON), str(GATES[0]), str(source)],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0, result.stderr
