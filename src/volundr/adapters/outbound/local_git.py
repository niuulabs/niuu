"""Local git operations adapter.

Executes git and gh CLI commands in session workspace directories via
asyncio subprocesses. Designed for mini/local mode where workspaces
are accessible on the host filesystem.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from volundr.domain.ports import GitWorkspacePort

logger = logging.getLogger(__name__)

# Null byte delimiter for git log — cannot appear in commit messages.
_LOG_DELIM = "\x00"


async def _run(
    *cmd: str,
    cwd: str,
    timeout: float,
) -> tuple[int, str, str]:
    """Run a subprocess and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        return -1, "", f"Command timed out after {timeout}s"
    return (
        proc.returncode or 0,
        stdout_bytes.decode(errors="replace"),
        stderr_bytes.decode(errors="replace"),
    )


def parse_numstat(raw: str) -> list[dict[str, str | int]]:
    """Parse ``git diff --numstat`` output into structured records.

    Each line is ``<additions>\\t<deletions>\\t<path>``.
    Binary files show ``-`` for additions/deletions.
    """
    results: list[dict[str, str | int]] = []
    for line in raw.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        adds, dels, path = parts
        results.append(
            {
                "path": path,
                "additions": int(adds) if adds != "-" else 0,
                "deletions": int(dels) if dels != "-" else 0,
            }
        )
    return results


def parse_log(raw: str) -> list[dict[str, str]]:
    """Parse ``git log --pretty=format:%h\\x00%s\\x00%H`` output."""
    results: list[dict[str, str]] = []
    for line in raw.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split(_LOG_DELIM, 2)
        if len(parts) < 3:
            continue
        short_hash, message, full_hash = parts
        results.append(
            {
                "hash": full_hash,
                "short_hash": short_hash,
                "message": message,
            }
        )
    return results


def parse_pr_view(raw: str) -> dict[str, Any] | None:
    """Parse ``gh pr view --json ...`` JSON output."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None

    checks: list[dict[str, str]] = []
    for node in data.get("statusCheckRollup", []):
        checks.append(
            {
                "name": node.get("name", node.get("context", "")),
                "status": node.get("conclusion", node.get("state", "unknown")),
            }
        )

    return {
        "number": data.get("number"),
        "url": data.get("url", ""),
        "state": data.get("state", ""),
        "mergeable": data.get("mergeable", "UNKNOWN"),
        "checks": checks,
    }


def resolve_repo_dir(workspace_dir: str) -> str:
    """Resolve the actual git checkout directory for a workspace.

    Git-backed mini-mode sessions clone the repository into ``workspace/repo``.
    Other workspace types operate directly in the workspace root.
    """
    workspace = Path(workspace_dir)
    repo_dir = workspace / "repo"
    if (repo_dir / ".git").exists():
        return str(repo_dir)
    return workspace_dir


class LocalGitService(GitWorkspacePort):
    """GitWorkspacePort implementation that shells out to git/gh CLIs."""

    def __init__(self, subprocess_timeout: float = 30.0) -> None:
        self._timeout = subprocess_timeout

    async def diff_files(self, workspace_dir: str) -> list[dict[str, str | int]]:
        repo_dir = resolve_repo_dir(workspace_dir)
        rc, stdout, stderr = await _run(
            "git",
            "diff",
            "HEAD",
            "--numstat",
            cwd=repo_dir,
            timeout=self._timeout,
        )
        if rc != 0:
            logger.warning("git diff --numstat failed (rc=%d): %s", rc, stderr)
            return []
        return parse_numstat(stdout)

    async def file_diff(
        self,
        workspace_dir: str,
        path: str,
        base_branch: str = "main",
    ) -> str | None:
        repo_dir = resolve_repo_dir(workspace_dir)
        rc, stdout, stderr = await _run(
            "git",
            "diff",
            f"{base_branch}...HEAD",
            "--",
            path,
            cwd=repo_dir,
            timeout=self._timeout,
        )
        if rc != 0:
            logger.warning("git diff failed for %s (rc=%d): %s", path, rc, stderr)
            return None
        return stdout if stdout.strip() else None

    async def commit_log(
        self,
        workspace_dir: str,
        since: str | None = None,
    ) -> list[dict[str, str]]:
        repo_dir = resolve_repo_dir(workspace_dir)
        cmd = ["git", "log", f"--pretty=format:%h{_LOG_DELIM}%s{_LOG_DELIM}%H"]
        if since:
            cmd.append(f"--since={since}")
        rc, stdout, stderr = await _run(*cmd, cwd=repo_dir, timeout=self._timeout)
        if rc != 0:
            logger.warning("git log failed (rc=%d): %s", rc, stderr)
            return []
        return parse_log(stdout)

    async def pr_status(self, workspace_dir: str) -> dict[str, Any] | None:
        repo_dir = resolve_repo_dir(workspace_dir)
        rc, stdout, stderr = await _run(
            "gh",
            "pr",
            "view",
            "--json",
            "number,url,state,mergeable,statusCheckRollup",
            cwd=repo_dir,
            timeout=self._timeout,
        )
        if rc != 0:
            # gh not installed or no PR — graceful None
            logger.debug("gh pr view failed (rc=%d): %s", rc, stderr)
            return None
        return parse_pr_view(stdout)

    async def current_branch(self, workspace_dir: str) -> str | None:
        repo_dir = resolve_repo_dir(workspace_dir)
        rc, stdout, stderr = await _run(
            "git",
            "rev-parse",
            "--abbrev-ref",
            "HEAD",
            cwd=repo_dir,
            timeout=self._timeout,
        )
        if rc != 0:
            logger.warning("git rev-parse failed (rc=%d): %s", rc, stderr)
            return None
        return stdout.strip() or None
