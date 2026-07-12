"""Smoke tests — proves the pf-core consumer wiring is sound.

Verifies that pf-core[cli] and pf_core.config are importable and that the CLI
app and the AppConfig subclass both construct.
"""

from typer.testing import CliRunner

from pagespring.cli import app
from pagespring.config import cfg


def test_cli_help_runs():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Usage" in result.output


def test_config_loads_defaults():
    assert cfg.APP_NAME == "pagespring"
    assert cfg.INCOMING_DIR == "incoming"  # default present even with no .env
