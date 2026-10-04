"""Docstrings and comment blocks stay within two lines of prose, so they do not regrow.

A ``Raises:`` section is a caller's contract, and a Typer command's docstring is its ``--help``.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

from pagespring.cli import app

_ROOT = Path(__file__).resolve().parents[1]
_MAX_LINES = 2
_SECTION = re.compile(r"^[A-Z][a-z]+:$")
_DIRECTIVES = ("#!", "# type:", "# noqa", "# pragma", "# fmt")
_HOLDERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _sources() -> list[Path]:
    package = sorted((_ROOT / "src" / "pagespring").rglob("*.py"))
    return [*package, *sorted((_ROOT / "tests").glob("*.py")), _ROOT / "bin" / "check-framework"]


def _module(path: Path) -> str:
    return ".".join(path.relative_to(_ROOT / "src").with_suffix("").parts)


def _help_text() -> set[tuple[str, str]]:
    return {
        (c.callback.__module__, c.callback.__name__) for c in app.registered_commands if c.callback
    }


def _docstring_problems(path: Path) -> list[str]:
    module = _module(path) if path.is_relative_to(_ROOT / "src") else ""
    problems = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        doc = ast.get_docstring(node) if isinstance(node, _HOLDERS) else None
        if doc is None or (module, getattr(node, "name", "")) in _help_text():
            continue
        prose = 0
        for line in doc.splitlines():
            if _SECTION.match(line.strip()):
                if line.strip() != "Raises:":
                    problems.append(f"{path.name}:{node.body[0].lineno} has section {line.strip()}")
                break
            prose += bool(line.strip())
        if prose > _MAX_LINES:
            problems.append(f"{path.name}:{node.body[0].lineno} runs {prose} lines")
    return problems


def _long_comment_blocks(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    starts: list[list[int]] = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        row = tok.start[0]
        if tok.type != tokenize.COMMENT or tok.string.startswith(_DIRECTIVES):
            continue
        if not lines[row - 1].lstrip().startswith("#"):
            continue
        if starts and starts[-1][0] + starts[-1][1] == row:
            starts[-1][1] += 1
        else:
            starts.append([row, 1])
    return [f"{path.name}:{row} runs {n} lines" for row, n in starts if n > _MAX_LINES]


def test_docstrings_stay_within_budget():
    problems = [p for path in _sources() for p in _docstring_problems(path)]
    assert not problems, "trim to two lines (a Raises: section may follow):\n" + "\n".join(problems)


def test_comment_blocks_stay_within_budget():
    problems = [p for path in _sources() for p in _long_comment_blocks(path)]
    assert not problems, "trim to two lines:\n" + "\n".join(problems)


def test_the_help_text_exemption_finds_the_commands():
    assert ("pagespring._cli_ingest", "ingest") in _help_text()
