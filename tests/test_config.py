"""Config — the .env settings resolve from."""

import os
import subprocess
import sys
from pathlib import Path

from structlog.testing import capture_logs

from pagespring import config


def test_env_file_defaults_to_the_source_tree(monkeypatch):
    monkeypatch.delenv(config._ENV_FILE_VAR, raising=False)

    assert config._env_file() == config._project_root / ".env"


def test_env_file_honours_the_override(tmp_path, monkeypatch):
    """An installed CLI has no project root above the package, so the override is
    the only way to point it at a real file."""
    env = tmp_path / "prod.env"
    env.write_text("INCOMING_DIR=/srv/incoming\n", encoding="utf-8")
    monkeypatch.setenv(config._ENV_FILE_VAR, str(env))

    assert config._env_file() == env


def test_env_file_expands_a_user_path(monkeypatch):
    monkeypatch.setenv(config._ENV_FILE_VAR, "~/pagespring.env")

    assert config._env_file() == Path.home() / "pagespring.env"


def test_env_file_ignores_a_dot_env_in_the_working_directory(tmp_path, monkeypatch):
    """A stray ``.env``'s keys enter the environment and could flip the private-address guard."""
    (tmp_path / ".env").write_text("URL_FETCH_ALLOW_PRIVATE=1\n", encoding="utf-8")
    monkeypatch.delenv(config._ENV_FILE_VAR, raising=False)
    monkeypatch.chdir(tmp_path)

    assert config._env_file() != tmp_path / ".env"
    assert config._env_file() == config._project_root / ".env"


def test_a_missing_override_warns_rather_than_losing_settings_silently(tmp_path, monkeypatch):
    """The defaults that replace a typo'd override's settings look healthy, so the
    warning is the only signal that the file never loaded."""
    missing = tmp_path / "typo.env"
    monkeypatch.setenv(config._ENV_FILE_VAR, str(missing))

    with capture_logs() as logs:
        resolved = config._env_file()

    assert resolved == missing  # returned anyway: a missing .env is not fatal
    assert [(e["event"], e["log_level"], e["path"]) for e in logs] == [
        ("config.env_file_missing", "warning", str(missing))
    ]


def test_the_override_files_settings_reach_cfg(tmp_path):
    """The far end of the wire: `cfg` is built from the resolved file at import,
    so proving a setting in it lands on the live config takes its own process."""
    env = tmp_path / "prod.env"
    env.write_text("INCOMING_DIR=/srv/pagespring-incoming\n", encoding="utf-8")
    # An inherited INCOMING_DIR would out-rank the file under test.
    child_env = {k: v for k, v in os.environ.items() if k != "INCOMING_DIR"}
    child_env[config._ENV_FILE_VAR] = str(env)

    out = subprocess.run(
        [sys.executable, "-c", "from pagespring.config import cfg; print(cfg.INCOMING_DIR)"],
        env=child_env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert out.stdout.strip() == "/srv/pagespring-incoming"
