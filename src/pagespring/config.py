"""pagespring configuration — a pf_core.config.AppConfig subclass.

All settings are overridable via environment variables / .env.
"""

from pathlib import Path

from pf_core.config import AppConfig

# src/pagespring/config.py → parents[2] is the project root (editable install).
_project_root = Path(__file__).resolve().parents[2]


class PagespringConfig(AppConfig):
    """pagespring settings."""

    APP_NAME: str = "pagespring"

    # The deliverable: one incoming/<slug>/ per manual — the clean
    # acquired+normalized file. A separate step (pagespeak) consumes these.
    INCOMING_DIR: str = "incoming"


cfg = PagespringConfig(env_file=_project_root / ".env")
