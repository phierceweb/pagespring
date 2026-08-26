"""Smoke tests — proves the pf-core consumer wiring is sound.

Verifies that pf-core[cli] and pf_core.config are importable and that the CLI
app and the AppConfig subclass both construct.
"""

from typer.testing import CliRunner

from pagespring.cli import app
from pagespring.config import PagespringConfig, cfg


def test_cli_help_runs():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Usage" in result.output


def test_config_loads_defaults(tmp_path, env_sandbox):
    assert cfg.APP_NAME == "pagespring"
    # A config built here, not the live cfg: conftest redirects that one's
    # INCOMING_DIR away from the real corpus.
    assert PagespringConfig(env_file=tmp_path / "absent.env").INCOMING_DIR == "incoming"


def test_config_loads_a_settings_file(tmp_path, env_sandbox):
    (tmp_path / "prod.env").write_text("INCOMING_DIR=/srv/incoming\n", encoding="utf-8")

    assert PagespringConfig(env_file=tmp_path / "prod.env").INCOMING_DIR == "/srv/incoming"
