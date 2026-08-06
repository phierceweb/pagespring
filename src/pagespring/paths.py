"""Slug → ``incoming/<slug>/`` resolution.

The one folding point for every command that takes a slug on the command line.
A slug reaches ``shutil.rmtree`` and ``Path.unlink``, so a ``..`` component must
never survive into the path.
"""

from __future__ import annotations

from pathlib import Path

from pf_core.exceptions import InvalidInputError
from pf_core.utils.slugify import slugify

from pagespring.config import cfg


def slug_dir(slug: str) -> Path:
    """``incoming/<slug>/`` for the folded form of ``slug``.

    Raises:
        InvalidInputError: ``slug`` folds to nothing, so it names no directory.
    """
    folded = slugify(slug)
    if not folded:
        raise InvalidInputError(f"{slug!r} folds to an empty slug — it names no deliverable")
    return Path(cfg.INCOMING_DIR) / folded
