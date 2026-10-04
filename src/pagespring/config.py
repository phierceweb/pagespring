"""pagespring configuration — a pf_core.config.AppConfig subclass.

All settings are overridable via environment variables / .env.
"""

from pathlib import Path

from pf_core.config import AppConfig
from pf_core.log import get_logger
from pf_core.utils.env import resolve_str

# src/pagespring/config.py → parents[2] is the project root (editable install).
_project_root = Path(__file__).resolve().parents[2]

_ENV_FILE_VAR = "PAGESPRING_ENV_FILE"


def _env_file() -> Path:
    """The ``.env`` settings resolve from: ``PAGESPRING_ENV_FILE``, else the editable project root.
    Never the working directory, since every key in the file enters the environment."""
    override = resolve_str(None, _ENV_FILE_VAR)
    if not override:
        return _project_root / ".env"
    path = Path(override).expanduser()
    if not path.is_file():
        # A typo'd override silently loses every setting the file carries.
        get_logger(__name__).warning("config.env_file_missing", path=str(path))
    return path


class PagespringConfig(AppConfig):
    """pagespring settings."""

    APP_NAME: str = "pagespring"

    # The deliverable: one incoming/<slug>/ per manual — the clean
    # acquired+normalized file.
    INCOMING_DIR: str = "incoming"

    # Idle seconds before a queue-driven crawl counts as stalled and bails as `truncated`: a crawl
    # can fetch healthily while producing nothing. 0 disables.
    CRAWL_STALL_AFTER_S: int = 300

    # A same-source re-crawl below COLLAPSE_KEEP_PCT percent of the staged pages is refused unless
    # --replace (0 disables); manuals under COLLAPSE_MIN_PAGES pages are exempt.
    COLLAPSE_KEEP_PCT: int = 50
    COLLAPSE_MIN_PAGES: int = 10


cfg = PagespringConfig(env_file=_env_file())
