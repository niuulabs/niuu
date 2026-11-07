"""Git operation tools for Ravn agents."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from niuu.domain.tool import ToolResult
from niuu.ports.tool import ToolPort

logger = logging.getLogger(__name__)

_PERMISSION_READ = "git:read"
_PERMISSION_WRITE = "git:write"


async def _run_git(args: list[str], cwd: Path, timeout: float = 30.0) -> tuple[int, str, str]:
    """Run a git subcommand and return (returncode, stdout, stderr).

    Raises no exceptions; a stuck process is killed and (-1, "", error_msg) is returned.
    """
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(cwd),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    coro = proc.communicate()
    try:
        stdout_b, stderr_b = await asyncio.wait_for(coro, timeout=timeout)
    except TimeoutError:
        coro.close()
        proc.kill()
        await proc.wait()
        return -1, "", f"Command timed out after {timeout}s"
    returncode = proc.returncode if proc.returncode is not None else 0
    return returncode, stdout_b.decode(errors="replace"), stderr_b.decode(errors="replace")


async def _gh_available() -> bool:
    """Return True if the gh CLI is installed and callable."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh",
            "version",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return proc.returncode == 0
    except FileNotFoundError:
        return False


async def _run_gh(*args: str, cwd: Path, timeout: float = 30.0) -> tuple[int, str, str]:
    """Run a gh subcommand and return (returncode, stdout, stderr).

    Raises no exceptions; a stuck process is killed and (-1, "", error_msg) is returned.
    """
    proc = await asyncio.create_subprocess_exec(
        "gh",
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    coro = proc.communicate()
    try:
        stdout_b, stderr_b = await asyncio.wait_for(coro, timeout=timeout)
    except TimeoutError:
        coro.close()
        proc.kill()
        await proc.wait()
        return -1, "", f"Command timed out after {timeout}s"
    returncode = proc.returncode if proc.returncode is not None else 0
    return returncode, stdout_b.decode(errors="replace"), stderr_b.decode(errors="replace")


def _ok(data: dict | str) -> ToolResult:
    content = json.dumps(data) if isinstance(data, dict) else data
    return ToolResult(tool_call_id="", content=content)


def _err(message: str) -> ToolResult:
    return ToolResult(tool_call_id="", content=message, is_error=True)


def _parse_status_output(output: str) -> dict:
    """Parse ``git status --porcelain=v2 --branch`` output into a structured dict.

    Returned keys:
    - branch (str): current branch name or "(detached)"
    - upstream (str | None): tracking remote, absent if not set
    - ahead (int): commits ahead of upstream
    - behind (int): commits behind upstream
    - staged (list[str]): paths with staged changes
    - unstaged (list[str]): paths with unstaged working-tree changes
    - untracked (list[str]): untracked paths
    - clean (bool): True when all three lists are empty
    """
    branch = "unknown"
    upstream: str | None = None
    ahead = 0
    behind = 0
    staged: list[str] = []
    unstaged: list[str] = []
    untracked: list[str] = []

    for line in output.splitlines():
        if line.startswith("# branch.head "):
            branch = line[len("# branch.head ") :]
        elif line.startswith("# branch.upstream "):
            upstream = line[len("# branch.upstream ") :]
        elif line.startswith("# branch.ab "):
            parts = line.split()
            for part in parts[2:]:
                if part.startswith("+"):
                    ahead = int(part[1:])
                elif part.startswith("-"):
                    behind = int(part[1:])
        elif line.startswith("1 ") or line.startswith("2 "):
            # Ordinary (1) or renamed/copied (2) changed entry.
            # Split on spaces up to 9 times so the path is always the last element(s).
            parts = line.split(" ", 9)
            xy = parts[1]
            # Renamed entries: parts[9] is "newpath\torigpath"; take new path.
            path = parts[8] if line.startswith("1 ") else parts[9].split("\t")[0]
            x, y = xy[0], xy[1]
            if x != ".":
                staged.append(path)
            if y != ".":
                unstaged.append(path)
        elif line.startswith("? "):
            untracked.append(line[2:])

    return {
        "branch": branch,
        "upstream": upstream,
        "ahead": ahead,
        "behind": behind,
        "staged": staged,
        "unstaged": unstaged,
        "untracked": untracked,
        "clean": not staged and not unstaged and not untracked,
    }


class GitStatusTool(ToolPort):
    """Return the current working-tree state as structured data."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "git_status"

    @property
    def description(self) -> str:
        return (
            "Get the current git working-tree state: branch name, ahead/behind count "
            "relative to the remote, staged files, unstaged files, and untracked files."
        )

    @property
    def input_schema(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def required_permission(self) -> str:
        return _PERMISSION_READ

    async def execute(self, input: dict) -> ToolResult:
        code, stdout, stderr = await _run_git(
            ["status", "--porcelain=v2", "--branch"],
            self._workspace,
        )
        if code != 0:
            return _err(f"git status failed: {stderr.strip()}")
        return _ok(_parse_status_output(stdout))


class GitDiffTool(ToolPort):
    """Show a unified diff of working-tree or staged changes."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "git_diff"

    @property
    def description(self) -> str:
        return (
            "Show a unified diff of changes. "
            "Pass staged=true to see staged (index) changes. "
            "Pass paths to limit the diff to specific files."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "staged": {
                    "type": "boolean",
                    "description": (
                        "Show staged (index) diff instead of working-tree diff (default: false)."
                    ),
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Limit the diff to these file paths.",
                },
            },
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_READ

    async def execute(self, input: dict) -> ToolResult:
        staged = input.get("staged", False)
        paths: list[str] = input.get("paths") or []

        args = ["diff"]
        if staged:
            args.append("--staged")
        if paths:
            args.append("--")
            args.extend(paths)

        code, stdout, stderr = await _run_git(args, self._workspace)
        if code != 0:
            return _err(f"git diff failed: {stderr.strip()}")
        return _ok(stdout)


class GitAddTool(ToolPort):
    """Stage files for the next commit."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "git_add"

    @property
    def description(self) -> str:
        return "Stage one or more file paths (or glob patterns) for the next commit."

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "File paths or glob patterns to stage.",
                },
            },
            "required": ["paths"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_WRITE

    async def execute(self, input: dict) -> ToolResult:
        paths: list[str] = input.get("paths") or []
        if not paths:
            return _err("paths must not be empty")

        code, _stdout, stderr = await _run_git(["add", "--", *paths], self._workspace)
        if code != 0:
            return _err(f"git add failed: {stderr.strip()}")
        return _ok({"staged": paths, "message": f"Staged {len(paths)} path(s)"})


class GitCommitTool(ToolPort):
    """Create a commit from currently staged changes."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "git_commit"

    @property
    def description(self) -> str:
        return (
            "Create a git commit with the given message. "
            "Refuses if there are no staged changes. "
            "Never force-pushes."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Commit message (required).",
                },
            },
            "required": ["message"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_WRITE

    async def execute(self, input: dict) -> ToolResult:
        message = (input.get("message") or "").strip()
        if not message:
            return _err("commit message must not be empty")

        # Refuse to commit if nothing is staged.
        code, staged_names, _ = await _run_git(["diff", "--staged", "--name-only"], self._workspace)
        if code != 0:
            return _err("failed to check staged changes")
        if not staged_names.strip():
            return _err("nothing to commit — no staged changes")

        code, stdout, stderr = await _run_git(["commit", "-m", message], self._workspace)
        if code != 0:
            return _err(f"git commit failed: {stderr.strip()}")

        _code, sha, _ = await _run_git(["rev-parse", "--short", "HEAD"], self._workspace)
        return _ok({"sha": sha.strip(), "message": message, "output": stdout.strip()})


class GitCheckoutTool(ToolPort):
    """Switch to an existing branch or create a new one."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "git_checkout"

    @property
    def description(self) -> str:
        return (
            "Switch to a branch or create a new one. "
            "Warns when the working tree has uncommitted changes but still attempts the checkout."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "branch": {
                    "type": "string",
                    "description": "Branch name to switch to.",
                },
                "create": {
                    "type": "boolean",
                    "description": "Create the branch if it does not exist (default: false).",
                },
            },
            "required": ["branch"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_WRITE

    async def execute(self, input: dict) -> ToolResult:
        branch = (input.get("branch") or "").strip()
        create = input.get("create", False)

        if not branch:
            return _err("branch name must not be empty")

        if branch.startswith("-"):
            return _err("branch name must not start with '-'")

        # Warn when working tree is dirty, but do not block the checkout.
        warning: str | None = None
        code, stdout, _ = await _run_git(["status", "--porcelain=v2", "--branch"], self._workspace)
        if code == 0:
            status = _parse_status_output(stdout)
            if not status["clean"]:
                warning = "working tree has uncommitted changes"

        args = ["checkout", "-b", branch] if create else ["checkout", branch]
        code, stdout, stderr = await _run_git(args, self._workspace)
        if code != 0:
            return _err(f"git checkout failed: {stderr.strip()}")

        result: dict = {"branch": branch, "created": create, "output": stdout.strip()}
        if warning:
            result["warning"] = warning
        return _ok(result)


class GitLogTool(ToolPort):
    """Return recent commit history as structured data."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "git_log"

    @property
    def description(self) -> str:
        return (
            "Show recent commits with hash, author, date, and subject. "
            "Use depth to control how many commits to return (default: 10)."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "depth": {
                    "type": "integer",
                    "description": "Number of commits to return (default: 10).",
                },
                "branch": {
                    "type": "string",
                    "description": "Branch or ref to log (default: HEAD).",
                },
            },
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_READ

    async def execute(self, input: dict) -> ToolResult:
        depth = input.get("depth", 10)
        branch = (input.get("branch") or "HEAD").strip() or "HEAD"

        # Reject leading dashes to prevent flag injection (e.g. "--all").
        if branch != "HEAD" and branch.startswith("-"):
            return _err("branch name must not start with '-'")

        sep = "\x00"
        # Use git's %x00 escape so the argv string contains no NUL bytes, but
        # the output is NUL-delimited.  NUL cannot appear in git commit data.
        fmt = "%H%x00%h%x00%an%x00%ae%x00%ai%x00%s"

        # Place branch BEFORE "--" so git treats it as a revision, not a
        # pathspec.  The trailing "--" tells git no path filters follow.
        code, stdout, stderr = await _run_git(
            ["log", f"-n{depth}", f"--format={fmt}", branch, "--"],
            self._workspace,
        )
        if code != 0:
            return _err(f"git log failed: {stderr.strip()}")

        commits: list[dict] = []
        for line in stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split(sep, 5)
            if len(parts) != 6:
                continue
            commits.append(
                {
                    "sha": parts[0],
                    "short_sha": parts[1],
                    "author": parts[2],
                    "email": parts[3],
                    "date": parts[4],
                    "message": parts[5],
                }
            )

        return _ok({"commits": commits, "count": len(commits)})


class GitPrTool(ToolPort):
    """Create a pull request via the gh CLI."""

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace

    @property
    def name(self) -> str:
        return "git_pr"

    @property
    def description(self) -> str:
        return (
            "Create a pull request using the gh CLI. "
            "Falls back to printing the remote URL with instructions when gh is unavailable."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Pull request title (required).",
                },
                "body": {
                    "type": "string",
                    "description": "Pull request description in markdown.",
                },
                "base": {
                    "type": "string",
                    "description": (
                        "Base branch to merge into (defaults to the repo default branch)."
                    ),
                },
                "draft": {
                    "type": "boolean",
                    "description": "Open as a draft pull request (default: false).",
                },
            },
            "required": ["title"],
        }

    @property
    def required_permission(self) -> str:
        return _PERMISSION_WRITE

    async def execute(self, input: dict) -> ToolResult:
        title = (input.get("title") or "").strip()
        if not title:
            return _err("PR title must not be empty")

        body: str = input.get("body") or ""
        base: str | None = input.get("base")
        draft: bool = input.get("draft", False)

        if not await _gh_available():
            _code, remote_url, _ = await _run_git(["remote", "get-url", "origin"], self._workspace)
            return _ok(
                {
                    "url": None,
                    "fallback": True,
                    "remote": remote_url.strip(),
                    "message": ("gh CLI not available; push your branch and open a PR manually"),
                }
            )

        args = ["pr", "create", "--title", title, "--body", body]
        if base:
            args.extend(["--base", base])
        if draft:
            args.append("--draft")

        code, stdout, stderr = await _run_gh(*args, cwd=self._workspace)
        if code != 0:
            return _err(f"gh pr create failed: {stderr.strip()}")

        return _ok({"url": stdout.strip(), "message": "Pull request created"})
