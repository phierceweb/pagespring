"""Slug to ``incoming/<slug>/``, the one folding point: a slug reaches ``rmtree`` and ``unlink``, so
a ``..`` component must never survive into the path."""

from __future__ import annotations

from pathlib import Path

from pf_core.exceptions import InvalidInputError
from pf_core.utils.slugify import slugify

from pagespring.config import cfg

_MAX_SLUG = 100  # leaves room for the deliverable's ".<ext>" under NAME_MAX (255)


def fold_slug(slug: str) -> str:
    """``slug`` slugified and capped, since a remote title can name a file no filesystem will
    create; ``""`` when it folds to nothing."""
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
