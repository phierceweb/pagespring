# Code Style

## General

- Python 3.11+ — use modern syntax (type unions with `|`, `match` where clearer).
- No star imports (`from x import *`).
- Imports grouped: stdlib → third-party → pf_core → project. One blank line between groups.
- Use `from __future__ import annotations` in files with forward references.

## File size

- Target: under 300 lines per file. Hard limit: 500 lines.
- If a file grows past 300 lines, split by concern — one function or class per file if needed.
- Small, focused files are easier for AI assistants to read and edit without introducing bugs.
- **Enforced** by the `pf_core.guards` build gate (pre-commit + CI): over 500 lines is a hard FAIL, over 300 a non-blocking WARN. Pre-existing offenders are grandfathered in `.ai/guards/file_size_baseline.json`; the gate fails on new violations or growth of a baselined file. See `docs/guards.md`.

## Naming

- Files: `snake_case.py`. Private modules prefixed with `_` (e.g. `_util.py`).
- Classes: `PascalCase`. Exceptions end with `Error` or `Exception`.
- Functions/methods: `snake_case`. Private prefixed with `_`.
- Constants: `UPPER_SNAKE_CASE`.

## Functions

- Prefer pure functions that take inputs and return outputs.
- Service functions return plain dicts, lists, or primitives — not ORM objects or stateful instances.
- Use keyword-only arguments for functions with more than 2 parameters.

## Type hints

- All public function signatures must have type hints.
- Use `dict`, `list`, `tuple` (lowercase) not `Dict`, `List`, `Tuple`.
- Use `X | None` not `Optional[X]`.

## Docstrings

- Required on: modules, public classes, public functions with non-obvious behavior.
- Not required on: private helpers, obvious getters/setters, test functions.
- Use Google-style docstrings (Args/Returns/Raises sections).

## Error handling

- See `error-handling.md` — never raise bare `Exception` from service code.
- Never swallow exceptions silently (`except Exception: pass`).

## Logging

- Use `pf_core.log.get_logger(__name__)` — never raw `print()` for operational output.
- `print()` is acceptable only in CLI entry points for user-facing output.
