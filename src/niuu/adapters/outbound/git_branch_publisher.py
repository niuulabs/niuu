"""Credential-safe exact-SHA branch publication through a trusted bare repository."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path


class GitBranchPublicationError(RuntimeError):
    """Git refused an exact, compare-and-swap branch publication."""


class AuthenticatedGitBranchPublisher:
    """Push one validated object without loading candidate repository configuration."""

    def __init__(
        self,
        *,
        git_binary: str = "git",
        temporary_root: str | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self._git_binary = git_binary
        self._temporary_root = temporary_root
        self._timeout = timeout_seconds

    async def publish(
        self,
        *,
        source_repository: str,
        source_sha: str,
        remote_url: str,
        branch: str,
        expected_remote_sha: str | None,
        username: str,
        token: str,
    ) -> None:
        if not token:
            raise GitBranchPublicationError("Branch publication requires a configured credential")
        with tempfile.TemporaryDirectory(
            prefix="niuu-publish-", dir=self._temporary_root
        ) as directory:
            root = Path(directory)
            bare = root / "staging.git"
            askpass = root / "askpass.sh"
            askpass.write_text(
                "#!/bin/sh\n"
                'case "$1" in\n'
                '  *Username*) printf "%s\\n" "$NIUU_GIT_USERNAME" ;;\n'
                '  *) printf "%s\\n" "$NIUU_GIT_PASSWORD" ;;\n'
                "esac\n",
                encoding="utf-8",
            )
            askpass.chmod(0o700)
            await self._git("init", "--bare", str(bare))
            await self._git(
                "-c",
                "core.hooksPath=/dev/null",
                "--git-dir",
                str(bare),
                "fetch",
                "--no-tags",
                "--",
                source_repository,
                source_sha,
            )
            fetched = await self._git(
                "--git-dir", str(bare), "rev-parse", f"{source_sha}^{{commit}}"
            )
            if fetched != source_sha:
                raise GitBranchPublicationError("Trusted staging fetch resolved another commit")
            lease = expected_remote_sha or ""
            environment = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "LANG": "C.UTF-8",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ASKPASS": str(askpass),
                "NIUU_GIT_USERNAME": username,
                "NIUU_GIT_PASSWORD": token,
            }
            await self._git(
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "credential.helper=",
                "--git-dir",
                str(bare),
                "push",
                "--porcelain",
                f"--force-with-lease=refs/heads/{branch}:{lease}",
                "--",
                remote_url,
                f"{source_sha}:refs/heads/{branch}",
                environment=environment,
            )

    async def _git(
        self,
        *arguments: str,
        environment: dict[str, str] | None = None,
    ) -> str:
        process = await asyncio.create_subprocess_exec(
            self._git_binary,
            *arguments,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment
            or {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "LANG": "C.UTF-8",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_TERMINAL_PROMPT": "0",
            },
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self._timeout)
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise GitBranchPublicationError("Git branch publication timed out") from exc
        if process.returncode:
            message = stderr.decode("utf-8", errors="replace")[-2000:]
            raise GitBranchPublicationError(f"Git branch publication failed: {message}")
        return stdout.decode("utf-8", errors="replace").strip()
