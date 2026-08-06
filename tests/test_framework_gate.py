"""`bin/check-framework` — each rule against source that breaks it, and against
source that only mentions it.

The gate fails silently: a regex that stops matching leaves the package
unchecked with nothing else in the suite noticing.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "bin" / "check-framework"

BREACHES = [
    ("import logging\n", "logging"),
    ("import requests\n", "requests"),
    ("import httpx\n", "httpx"),
    ("def f():\n    raise RuntimeError('x')\n", "RuntimeError"),
    ("def f():\n    raise ValueError('x')\n", "ValueError"),
    ("def f():\n    raise Exception('x')\n", "Exception"),
    ("import os\n\n\ndef f():\n    return os.environ.get('X')\n", "environ"),
    ("def f():\n    print('x')\n", "print"),
    ("import os\n\n\ndef f(a, b):\n    os.replace(a, b)\n", "os.replace"),
    (
        "import json\n\n\ndef f(p, o):\n    p.write_text(json.dumps(o, indent=2) + '\\n')\n",
        "json-write_text",
    ),
]


def _run(*paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GATE), *(str(p) for p in paths)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


@pytest.mark.parametrize("source,needle", BREACHES, ids=[n for _, n in BREACHES])
def test_the_gate_refuses_each_hand_roll(source, needle, tmp_path):
    breach = tmp_path / "breach.py"
    breach.write_text(source, encoding="utf-8")
    result = _run(breach)
    assert result.returncode == 1, f"{needle} passed the gate:\n{result.stdout}"
    assert "use" in result.stdout, "the failure does not name a replacement"


def test_the_gate_does_not_fire_on_prose_that_merely_names_a_rule(tmp_path):
    """A docstring naming a banned call documents the rule; it does not break it."""
    prose = tmp_path / "prose.py"
    prose.write_text(
        '"""Reads ``os.environ`` at call time, and never calls print()."""\n'
        "\n"
        "\ndef f():\n"
        '    return "os.replace is the dance we do not hand-roll"\n',
        encoding="utf-8",
    )
    result = _run(prose)
    assert result.returncode == 0, result.stdout


def test_the_package_passes_its_own_gate():
    result = _run()
    assert result.returncode == 0, result.stdout


def test_every_exemption_states_a_reason():
    """An exemption without a reason is a blanket skip wearing a key."""
    loader = importlib.machinery.SourceFileLoader("check_framework", str(GATE))
    spec = importlib.util.spec_from_loader("check_framework", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)

    assert module.EXEMPT, "no exemptions at all is suspicious — the file shape changed"
    for key, reason in module.EXEMPT.items():
        assert len(key) == 2, f"an exemption must name (rule, path): {key}"
        assert len(reason.split()) >= 4, f"{key} is exempt for no stated reason: {reason!r}"
