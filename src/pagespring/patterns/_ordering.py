"""Reading-order helpers shared by patterns that order files by name."""

from __future__ import annotations

import re
from pathlib import Path


def natural_key(path: Path) -> tuple[object, ...]:
    """Sort key where embedded digits compare numerically, so ch2 precedes ch10."""
    return tuple(
        int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", str(path))
    )
