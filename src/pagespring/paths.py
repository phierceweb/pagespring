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

_MAX_SLUG = 100  # leaves room for the deliverable's ".<ext>" under NAME_MAX (255)


def fold_slug(slug: str) -> str:
    """``slug`` in its on-disk form: slugified, then capped.

    The fold is fed by remote titles, and a 300-character one names a file no
    filesystem will create. Returns ``""`` when it folds to nothing.
    """
    folded = slugify(slug)
    return folded[:_MAX_SLUG].rstrip("-") if len(folded) > _MAX_SLUG else folded


def slug_dir(slug: str) -> Path:
    """``incoming/<slug>/`` for the folded form of ``slug``.

    Raises:
        InvalidInputError: ``slug`` folds to nothing, so it names no directory.
    """
    folded = fold_slug(slug)
    if not folded:
        raise InvalidInputError(f"{slug!r} folds to an empty slug — it names no deliverable")
    return Path(cfg.INCOMING_DIR) / folded
