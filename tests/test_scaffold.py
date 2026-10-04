"""Smoke tests: pf-core wiring imports, the CLI app and AppConfig subclass construct, and the
release strings agree."""

import tomllib
from pathlib import Path

from typer.testing import CliRunner

from pagespring import __version__
from pagespring.cli import app
from pagespring.config import PagespringConfig, cfg

ROOT = Path(__file__).resolve().parents[1]


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


def test_version_matches_pyproject():
    """Nothing else compares the pair: the manifest and the UA read `__version__`,
    the installed distribution reads pyproject."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert data["project"]["version"] == __version__


def test_changelog_documents_the_current_version():
    heads = [
        line
        for line in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
        if line.startswith("## [")
    ]

    assert heads[0].startswith((f"## [{__version__}]", "## [Unreleased]"))
