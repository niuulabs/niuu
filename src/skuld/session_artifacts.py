"""Session artifact accumulation and git-workspace completion evidence."""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _resolve_git_workspace_root(workspace_dir: str) -> Path:
    """Resolve the actual checkout root for git-backed workspaces."""
    workspace = Path(workspace_dir).resolve()
    repo_dir = workspace / "repo"
    if (repo_dir / ".git").exists():
        return repo_dir
    return workspace


# ---------------------------------------------------------------------------
# Session artifacts & summary prompt (Part: Chronicle Summary Generation)
# ---------------------------------------------------------------------------

_GIT_COMMIT_PREFIXES = ("git commit", "git -c ", "git -C ")

# Matches git commit output like: [main e4f7a21] fix: some message
_GIT_COMMIT_OUTPUT_RE = re.compile(r"\[[\w/-]+\s+([a-f0-9]{7,})\]\s+(.+)")


def _is_git_commit(cmd: str) -> bool:
    """Return True if a Bash command is a git commit invocation."""
    stripped = cmd.lstrip()
    if stripped.startswith(_GIT_COMMIT_PREFIXES):
        return True
    # Handle chained commands: git add . && git commit -m "..."
    return "git commit" in stripped


def _is_git_push(cmd: str) -> bool:
    """Return True if a Bash command is a git push invocation."""
    stripped = cmd.lstrip()
    if stripped.startswith("git push"):
        return True
    return "git push" in stripped


def _extract_git_commit_info(output: str) -> tuple[str, str] | None:
    """Extract commit hash and message from git commit output.

    Returns (hash, message) tuple or None if not found.
    """
    match = _GIT_COMMIT_OUTPUT_RE.search(output)
    if not match:
        return None
    return match.group(1), match.group(2)


@dataclass
class GitWorkspaceCheckpoint:
    """Snapshot of the workspace repo at broker startup."""

    repo_root: Path
    initial_head: str
    initial_upstream_head: str | None = None


def _git_command_output(repo_root: Path, *args: str) -> str | None:
    """Return trimmed git command output or None when the command fails."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    output = result.stdout.strip()
    return output or None


def _capture_git_workspace_checkpoint(workspace_dir: str) -> GitWorkspaceCheckpoint | None:
    """Capture the startup git state for a workspace-backed session."""
    repo_root = _resolve_git_workspace_root(workspace_dir)
    if not (repo_root / ".git").exists():
        return None
    head = _git_command_output(repo_root, "rev-parse", "HEAD")
    if not head:
        return None
    upstream_head = _git_command_output(repo_root, "rev-parse", "@{u}")
    return GitWorkspaceCheckpoint(
        repo_root=repo_root,
        initial_head=head,
        initial_upstream_head=upstream_head,
    )


def _git_workspace_checkpoint_status(
    checkpoint: GitWorkspaceCheckpoint | None,
) -> tuple[bool, bool]:
    """Return (commit_ok, push_ok) relative to the startup workspace checkpoint."""
    if checkpoint is None:
        return (False, False)

    current_head = _git_command_output(checkpoint.repo_root, "rev-parse", "HEAD")
    if not current_head or current_head == checkpoint.initial_head:
        return (False, False)

    upstream_head = _git_command_output(checkpoint.repo_root, "rev-parse", "@{u}")
    push_ok = upstream_head == current_head if upstream_head else False
    return (True, push_ok)


@dataclass
class SessionArtifacts:
    """In-memory accumulator for session activity during the broker's lifetime.

    Populated passively from events flowing through ``_handle_cli_event``.
    """

    files_changed: list[str] = field(default_factory=list)
    turn_count: int = 0
    started_at: float = field(default_factory=time.monotonic)
    total_tokens: int = 0
    structured_outcome: dict[str, Any] | None = None
    outcome_valid: bool = False
    saga_id: str | None = None
    run_id: str | None = None
    git_commit_count: int = 0
    git_push_count: int = 0
    _known_files: set[str] = field(default_factory=set)
    _pending_tool_results: dict[str, dict] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> int:
        return int(time.monotonic() - self.started_at)

    def _classify_tool(self, tool_name: str, tool_input: dict) -> dict | None:
        """Classify a single tool_use block into a timeline event dict.

        Returns None when the tool doesn't map to a timeline event.
        """
        file_path = tool_input.get("file_path") or tool_input.get("path")

        if tool_name in ("Edit", "Write", "NotebookEdit"):
            if tool_name == "Edit":
                # Edit always modifies an existing file
                action = "modified"
                if file_path:
                    self._known_files.add(file_path)
            elif file_path and file_path in self._known_files:
                action = "modified"
            elif file_path:
                action = "created"
                self._known_files.add(file_path)
            else:
                action = "created"
            return {"type": "file", "label": file_path or tool_name, "action": action}

        if tool_name == "Read":
            # Track files we've seen for created/modified classification
            if file_path:
                self._known_files.add(file_path)
            return None

        if tool_name != "Bash":
            return None

        cmd = tool_input.get("command", "")
        if _is_git_commit(cmd):
            # Store pending; will be enriched by tool_result
            return {"type": "git", "label": cmd[:80] or "git commit", "_pending_git": True}
        if _is_git_push(cmd):
            return {"type": "git_push", "label": cmd[:80] or "git push"}

        return {"type": "terminal", "label": cmd[:80] or "bash"}

    def record_tool_use(self, data: dict) -> list[dict]:
        """Extract file paths from tool_use events (Write, Edit, etc.).

        Returns a list of timeline-reportable tool events extracted
        from the content blocks.

        Handles both the HTTP streaming format (``data["content"]``)
        and the SDK WebSocket format (``data["message"]["content"]``).
        """
        tool_events: list[dict] = []
        content = data.get("content", [])
        if not isinstance(content, list) or not content:
            # SDK WebSocket transport nests content under message.content
            msg = data.get("message")
            if isinstance(msg, dict):
                content = msg.get("content", [])
        if not isinstance(content, list):
            return tool_events

        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue

            tool_name = block.get("name", "")
            tool_input = block.get("input", {})
            tool_use_id = block.get("id", "")
            file_path = tool_input.get("file_path") or tool_input.get("path")

            if file_path and file_path not in self.files_changed:
                self.files_changed.append(file_path)

            event = self._classify_tool(tool_name, tool_input)
            if event:
                # Store tool_use_id for matching with tool_result
                if tool_use_id:
                    event["_tool_use_id"] = tool_use_id
                tool_events.append(event)

        return tool_events

    def enrich_from_tool_result(self, data: dict, tool_events: list[dict]) -> None:
        """Enrich pending tool events with data from tool_result blocks.

        Extracts exit codes for terminal events and commit info for git events
        from the corresponding tool_result content blocks.
        """
        content = data.get("content", [])
        if not isinstance(content, list):
            return

        # Build a map of tool_result blocks by tool_use_id
        result_map: dict[str, dict] = {}
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            use_id = block.get("tool_use_id", "")
            if use_id:
                result_map[use_id] = block

        for event in tool_events:
            use_id = event.pop("_tool_use_id", "")
            if not use_id or use_id not in result_map:
                continue

            result_block = result_map[use_id]
            result_content = result_block.get("content", "")
            if isinstance(result_content, list):
                # Extract text from content blocks
                result_content = " ".join(
                    b.get("text", "") for b in result_content if isinstance(b, dict)
                )

            if event.get("type") == "git" and event.pop("_pending_git", False):
                commit_info = _extract_git_commit_info(result_content)
                if commit_info:
                    event["hash"] = commit_info[0]
                    event["label"] = commit_info[1]
                event["exit"] = 1 if result_block.get("is_error") else 0

            if event.get("type") in {"terminal", "git_push"}:
                # Extract exit code — look for explicit exit code in result
                exit_code = self._extract_exit_code(result_block)
                if exit_code is not None:
                    event["exit"] = exit_code

    def observe_tool_event(self, event: dict[str, Any]) -> None:
        """Track durable git activity from a classified/enriched tool event."""
        if event.get("type") == "git" and int(event.get("exit", 0)) == 0:
            self.git_commit_count += 1
        if event.get("type") == "git_push" and int(event.get("exit", 0)) == 0:
            self.git_push_count += 1

    @staticmethod
    def _extract_exit_code(result_block: dict) -> int | None:
        """Extract exit code from a tool_result block.

        The SDK transport includes exit code info in the result block.
        """
        # Check for explicit exit_code field
        if "exit_code" in result_block:
            return result_block["exit_code"]

        # Check content for exit code pattern
        content = result_block.get("content", "")
        if isinstance(content, str):
            # Check for error indicator — if tool_result has is_error
            if result_block.get("is_error"):
                return 1
            return 0

        # For list content, check is_error flag
        if result_block.get("is_error"):
            return 1
        return 0

    def record_result(self) -> None:
        """Increment turn counter on each result event."""
        self.turn_count += 1
