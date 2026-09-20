"""Exact branch publisher command-boundary tests."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from niuu.adapters.outbound.git_branch_publisher import (
    AuthenticatedGitBranchPublisher,
    GitBranchPublicationError,
)


@pytest.mark.asyncio
async def test_requires_configured_credential() -> None:
    publisher = AuthenticatedGitBranchPublisher()
    with pytest.raises(GitBranchPublicationError, match="configured credential"):
        await publisher.publish(
            source_repository="/trusted/integration",
            source_sha="b" * 40,
            remote_url="https://gitlab.example/org/repo.git",
            branch="campaign/integration",
            expected_remote_sha=None,
            username="oauth2",
            token="",
        )


@pytest.mark.asyncio
async def test_publishes_exact_sha_from_trusted_bare_staging_without_token_in_argv(
    tmp_path: Path,
) -> None:
    source_sha = "b" * 40
    log = tmp_path / "git-calls.jsonl"
    fake_git = tmp_path / "fake-git"
    fake_git.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        f"log = {str(log)!r}\n"
        "with open(log, 'a', encoding='utf-8') as stream:\n"
        "    stream.write(json.dumps({'argv': sys.argv[1:], "
        "'has_password': bool(os.environ.get('NIUU_GIT_PASSWORD'))}) + '\\n')\n"
        f"if 'rev-parse' in sys.argv: print({source_sha!r})\n",
        encoding="utf-8",
    )
    fake_git.chmod(0o700)
    publisher = AuthenticatedGitBranchPublisher(
        git_binary=str(fake_git),
        temporary_root=str(tmp_path),
    )
    await publisher.publish(
        source_repository="/trusted/integration",
        source_sha=source_sha,
        remote_url="https://gitlab.example/org/repo.git",
        branch="campaign/integration",
        expected_remote_sha="a" * 40,
        username="oauth2",
        token="secret-token",
    )
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    push = next(call for call in calls if "push" in call["argv"])
    assert "--force-with-lease=refs/heads/campaign/integration:" + "a" * 40 in push["argv"]
    assert f"{source_sha}:refs/heads/campaign/integration" in push["argv"]
    assert push["has_password"] is True
    assert all("secret-token" not in argument for call in calls for argument in call["argv"])


def _git(binary: str, directory: Path, *arguments: str) -> str:
    result = subprocess.run(
        [binary, "-C", str(directory), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.mark.asyncio
async def test_real_git_publishes_to_local_bare_remote_and_rejects_stale_lease(
    tmp_path: Path,
) -> None:
    binary = shutil.which("git") or ""
    if not binary or subprocess.run([binary, "--version"], capture_output=True).returncode:
        pytest.skip("Git executable is unavailable")
    source = tmp_path / "source"
    remote = tmp_path / "remote.git"
    source.mkdir()
    remote.mkdir()
    _git(binary, source, "init", "-q", "-b", "main")
    _git(binary, remote, "init", "-q", "--bare")
    (source / "value.txt").write_text("one\n", encoding="utf-8")
    _git(binary, source, "add", "value.txt")
    _git(
        binary,
        source,
        "-c",
        "user.name=Proof",
        "-c",
        "user.email=proof@example.invalid",
        "commit",
        "-q",
        "-m",
        "one",
    )
    first = _git(binary, source, "rev-parse", "HEAD")
    publisher = AuthenticatedGitBranchPublisher(
        git_binary=binary,
        temporary_root=str(tmp_path),
    )
    await publisher.publish(
        source_repository=str(source),
        source_sha=first,
        remote_url=str(remote),
        branch="campaign/integration",
        expected_remote_sha=None,
        username="unused",
        token="unused",
    )
    assert _git(binary, remote, "rev-parse", "refs/heads/campaign/integration") == first

    (source / "value.txt").write_text("two\n", encoding="utf-8")
    _git(binary, source, "add", "value.txt")
    _git(
        binary,
        source,
        "-c",
        "user.name=Proof",
        "-c",
        "user.email=proof@example.invalid",
        "commit",
        "-q",
        "-m",
        "two",
    )
    second = _git(binary, source, "rev-parse", "HEAD")
    with pytest.raises(GitBranchPublicationError, match="publication failed"):
        await publisher.publish(
            source_repository=str(source),
            source_sha=second,
            remote_url=str(remote),
            branch="campaign/integration",
            expected_remote_sha="f" * 40,
            username="unused",
            token="unused",
        )
    assert _git(binary, remote, "rev-parse", "refs/heads/campaign/integration") == first
