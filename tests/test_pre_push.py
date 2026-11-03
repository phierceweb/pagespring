"""`bin/pre-push` — a version tag is pushed only onto the main the remote will hold, which is what
publish checks. Every case pushes to a real bare remote; nothing touches the network."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "bin" / "pre-push"
_ZERO = "0" * 40


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


class Repo:
    """A clone whose remote ``origin`` is a bare repo holding main at ``old``; local main is ``tip``."""

    def __init__(self, tmp_path: Path) -> None:
        self.remote = tmp_path / "remote.git"
        _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(self.remote))
        self.path = tmp_path / "r"
        _git(tmp_path, "init", "-q", "-b", "main", str(self.path))
        for key, value in (("user.email", "t@t.test"), ("user.name", "t")):
            _git(self.path, "config", key, value)
        _git(self.path, "remote", "add", "origin", str(self.remote))
        _git(self.path, "commit", "-q", "--allow-empty", "-m", "one")
        self.old = _git(self.path, "rev-parse", "HEAD")
        _git(self.path, "push", "-q", "origin", "main")
        _git(self.path, "commit", "-q", "--allow-empty", "-m", "two")
        self.tip = _git(self.path, "rev-parse", "HEAD")

    def hook(self, *lines: str, url: str = "") -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(HOOK), "origin", url or str(self.remote)],
            input="".join(f"{line}\n" for line in lines),
            capture_output=True,
            text=True,
            cwd=self.path,
            check=False,
        )

    def tag_line(self, tag: str = "v1.0.0") -> str:
        return f"refs/tags/{tag} {_git(self.path, 'rev-parse', tag)} refs/tags/{tag} {_ZERO}"

    def main_line(self) -> str:
        return f"refs/heads/main {self.tip} refs/heads/main {self.old}"


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path)


def _advice(result: subprocess.CompletedProcess[str]) -> str:
    return [line.strip() for line in result.stderr.splitlines() if line.startswith("    git ")][-1]


_PUSH_BOTH = "git push --atomic origin main refs/tags/v1.0.0"


def test_a_tag_on_the_remotes_main_passes(repo):
    _git(repo.path, "tag", "-a", "v1.0.0", "-m", "v1.0.0", repo.old)

    result = repo.hook(repo.tag_line())

    assert result.returncode == 0, result.stderr


def test_a_tag_on_new_local_commits_pushed_alone_is_told_to_push_main_too(repo):
    _git(repo.path, "tag", "-a", "v1.0.0", "-m", "v1.0.0", repo.tip)

    result = repo.hook(repo.tag_line())

    assert result.returncode == 1
    assert _advice(result) == _PUSH_BOTH
    assert repo.hook(repo.main_line(), repo.tag_line()).returncode == 0


def test_a_tag_off_the_pushed_main_is_told_to_tag_it(repo):
    _git(repo.path, "tag", "v1.0.0", repo.old)

    result = repo.hook(repo.main_line(), repo.tag_line())

    assert result.returncode == 1
    assert repo.tip[:7] in result.stderr and "    git " not in result.stderr
    _git(repo.path, "tag", "-f", "-a", "v1.0.0", "-m", "v1.0.0", repo.tip)
    assert repo.hook(repo.main_line(), repo.tag_line()).returncode == 0


def test_the_remotes_main_is_asked_for_not_a_stale_tracking_ref(repo):
    """Another clone moved the remote's main; this one never fetched and tags what it last saw."""
    _git(repo.path, "push", "-q", "origin", "main")
    _git(repo.path, "update-ref", "refs/remotes/origin/main", repo.old)
    _git(repo.path, "update-ref", "refs/heads/main", repo.old)
    _git(repo.path, "tag", "v1.0.0", repo.old)

    result = repo.hook(repo.tag_line())

    assert result.returncode == 1
    assert repo.tip[:7] in result.stderr and "    git " not in result.stderr


def test_a_tag_on_a_local_main_that_diverged_is_told_to_integrate_first(repo):
    _git(repo.path, "push", "-q", "origin", "main")
    _git(repo.path, "reset", "-q", "--hard", repo.old)
    _git(repo.path, "commit", "-q", "--allow-empty", "-m", "other")
    _git(repo.path, "tag", "v1.0.0")

    result = repo.hook(repo.tag_line())

    assert result.returncode == 1
    assert "integrate" in result.stderr and "git push" not in result.stderr


def test_branches_other_tags_and_deletions_are_left_alone(repo):
    result = repo.hook(
        f"refs/heads/feature {repo.old} refs/heads/feature {_ZERO}",
        f"refs/tags/nightly {repo.old} refs/tags/nightly {_ZERO}",
        f"(delete) {_ZERO} refs/tags/v0.9.0 {repo.old}",
    )

    assert result.returncode == 0, result.stderr


def test_deleting_main_in_the_same_push_is_not_taken_for_its_new_tip(repo):
    _git(repo.path, "tag", "v1.0.0", repo.old)

    result = repo.hook(f"(delete) {_ZERO} refs/heads/main {repo.old}", repo.tag_line())

    assert result.returncode == 0, result.stderr


def test_a_real_push_to_a_url_follows_its_own_advice_to_success(repo):
    """git leaves an up-to-date main out of the hook's input, so only asking the remote can pass."""
    (repo.path / ".git" / "hooks" / "pre-push").symlink_to(HOOK)
    url = str(repo.remote)
    _git(repo.path, "tag", "-a", "v1.0.0", "-m", "v1.0.0", repo.tip)

    first = subprocess.run(
        ["git", "-C", str(repo.path), "push", url, "v1.0.0"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode != 0
    assert _advice(first) == f"git push --atomic {url} main refs/tags/v1.0.0", first.stderr

    _git(repo.path, "push", "-q", "--atomic", url, "main", "refs/tags/v1.0.0")
    _git(repo.path, "tag", "-a", "v1.0.1", "-m", "v1.0.1", repo.tip)
    _git(repo.path, "push", "-q", url, "v1.0.1")
    assert _git(repo.remote, "rev-parse", "v1.0.1^{commit}") == _git(
        repo.remote, "rev-parse", "main"
    )


def test_the_tracking_ref_stands_in_when_the_remote_cannot_be_asked(repo):
    _git(repo.path, "tag", "v1.0.0", repo.old)

    ok = repo.hook(repo.tag_line(), url=str(repo.path / "no-such-remote.git"))
    _git(repo.path, "update-ref", "refs/remotes/origin/main", repo.tip)
    refused = repo.hook(repo.tag_line(), url=str(repo.path / "no-such-remote.git"))

    assert (ok.returncode, refused.returncode) == (0, 1), refused.stderr


def test_a_remote_without_main_is_told_to_push_main_with_the_tag(tmp_path):
    repo = Repo(tmp_path)
    empty = tmp_path / "empty.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(empty))
    _git(repo.path, "tag", "v1.0.0", repo.tip)

    result = repo.hook(repo.tag_line(), url=str(empty))

    assert result.returncode == 1
    assert _advice(result) == _PUSH_BOTH


def test_a_push_without_a_release_tag_never_asks_the_remote(repo, tmp_path):
    trace = tmp_path / "trace.log"
    result = subprocess.run(
        ["sh", str(HOOK), "origin", str(repo.remote)],
        input=f"{repo.main_line()}\n",
        capture_output=True,
        text=True,
        cwd=repo.path,
        env={**os.environ, "GIT_TRACE": str(trace)},
        check=False,
    )

    assert result.returncode == 0
    assert "ls-remote" not in (trace.read_text() if trace.exists() else "")


def test_the_advised_push_names_the_tag_when_a_branch_shares_its_name(repo):
    _git(repo.path, "branch", "v1.0.0", repo.old)
    _git(repo.path, "tag", "-a", "v1.0.0", "-m", "v1.0.0", repo.tip)

    result = repo.hook(repo.tag_line())

    assert _advice(result) == _PUSH_BOTH
    _git(repo.path, "push", "-q", "--atomic", "origin", "main", "refs/tags/v1.0.0")


def test_an_older_tag_in_the_push_is_told_to_stay_out_not_to_move(repo):
    _git(repo.path, "tag", "-a", "v0.9.0", "-m", "v0.9.0", repo.old)
    _git(repo.path, "tag", "-a", "v1.0.0", "-m", "v1.0.0", repo.tip)

    result = repo.hook(repo.main_line(), repo.tag_line("v1.0.0"), repo.tag_line("v0.9.0"))

    assert result.returncode == 1
    assert "v0.9.0" in result.stderr and "v1.0.0" not in result.stderr
    assert "without it" in result.stderr and "tag -f" not in result.stderr
