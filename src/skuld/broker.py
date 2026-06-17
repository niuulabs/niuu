"""Skuld Broker - WebSocket proxy for Claude Code CLI.

Supports two transport modes (selected via config):
- "sdk": long-lived CLI process connected via --sdk-url WebSocket (default)
- "subprocess": spawns claude -p per message, reads stdout (legacy fallback)
"""

import asyncio
import base64
import collections
import inspect
import json
import logging
import os
import re
import shutil
import subprocess
import time
import urllib.parse
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import (
    FastAPI,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from niuu.domain.logging import LoggingConfig
from niuu.domain.outcome import parse_outcome_block
from niuu.mesh.cluster import read_cluster_pub_addresses
from niuu.mesh.discovery_builder import build_discovery_adapters
from niuu.mesh.identity import MeshIdentity
from niuu.ports.cli import CLITransport
from niuu.utils import import_class
from skuld.channels import (
    ChannelRegistry,
    TelegramChannel,
    WebSocketChannel,
    _is_expected_ws_disconnect,
)
from skuld.chronicle_watcher import ChronicleWatcher
from skuld.config import SkuldSettings
from skuld.room_bridge import RoomBridge
from skuld.room_mesh_bridge import RoomMeshBridge
from skuld.service_manager import (
    ServiceCreateRequest,
    ServiceManager,
    ServiceStatus,
)
from sleipnir.adapters.in_process import InProcessBus
from sleipnir.domain.catalog import ravn_session_ended
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirPublisher
from volundr.log_aggregate import aggregate_workspace_logs

# ---------------------------------------------------------------------------
# In-memory log buffer (Part 2: Pod Log Retrieval)
# ---------------------------------------------------------------------------

_log_buffer: collections.deque[dict] = collections.deque(maxlen=2000)


class _BufferHandler(logging.Handler):
    """Logging handler that appends structured records to an in-memory ring buffer."""

    def emit(self, record: logging.LogRecord) -> None:
        _log_buffer.append(
            {
                "time": self.format(record) if not hasattr(record, "asctime") else "",
                "timestamp": record.created,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
        )


def _configure_logging() -> None:
    """Configure logging from LoggingConfig (reads LOG_LEVEL, LOG_FORMAT env vars)."""
    config = LoggingConfig()
    level_name = config.level.upper()
    log_format = config.format.lower()

    level = getattr(logging, level_name, logging.INFO)

    if log_format == "json":
        fmt = (
            '{"time":"%(asctime)s","level":"%(levelname)s",'
            '"logger":"%(name)s","message":"%(message)s"}'
        )
    else:
        fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    logging.basicConfig(level=level, format=fmt)

    # Attach ring buffer handler to root logger so all loggers feed into it
    buffer_handler = _BufferHandler()
    buffer_handler.setLevel(level)
    logging.getLogger().addHandler(buffer_handler)


_configure_logging()
logger = logging.getLogger("skuld.broker")
FORGE_SESSIONS_PATH = "/api/v1/forge/sessions"
FORGE_CHRONICLES_PATH = "/api/v1/forge/chronicles"
FORGE_EVENTS_PATH = "/api/v1/forge/events"
FORGE_TRACE_SPANS_START_PATH = "/api/v1/forge/spans/start"
FORGE_TRACE_SPANS_COMPLETE_PATH = "/api/v1/forge/spans/complete"
FORGE_PERMISSION_AUTO_APPROVAL_EVALUATE_PATH = (
    f"{FORGE_SESSIONS_PATH}/{{session_id}}/permissions/auto-approval/evaluate"
)
WORKFLOW_GATE_INTENT_HEADER = "x-niuu-workflow-gate-intent"
WORKFLOW_GATE_INTENT_RESOLVE = "resolve"


def _sanitize_log(value: object) -> str:
    """Sanitize a value for safe log output (prevent log injection)."""
    return str(value).replace("\n", "\\n").replace("\r", "\\r")


def _non_empty_str(value: object) -> str:
    """Return a stripped string or an empty string for absent/non-string values."""
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _describe_browser_content_block(block: dict[str, Any]) -> str | None:
    """Return a short human-readable description for a browser content block."""
    block_type = str(block.get("type") or "").strip()
    if not block_type:
        return None
    if block_type == "image":
        return "image attachment"
    if block_type == "file":
        return "file attachment"
    return f"{block_type} attachment"


def _normalize_browser_message_content(content: object) -> str:
    """Convert browser message content into a compact text prompt.

    The browser can send either a plain string or structured content blocks,
    including large base64-encoded image attachments. Codex transports only
    accept text, so attachment payloads are summarized instead of stringified.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")

    text_parts: list[str] = []
    attachment_descriptions: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "").strip()
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())
            continue
        description = _describe_browser_content_block(block)
        if description:
            attachment_descriptions.append(description)

    lines: list[str] = []
    if text_parts:
        lines.append("\n\n".join(text_parts))
    if attachment_descriptions:
        counts = collections.Counter(attachment_descriptions)
        attachment_summary = ", ".join(
            f"{count} {label}" if count != 1 else f"1 {label}"
            for label, count in sorted(counts.items())
        )
        lines.append(f"[User attached {attachment_summary}. This transport forwards text only.]")
    return "\n\n".join(lines).strip()


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


@dataclass
class PeerWatchState:
    """Tracks one active flock peer task for Skuld's silence watchdog."""

    peer_id: str
    task_id: str
    title: str
    started_at: float
    last_progress_at: float
    last_status: str = "busy"
    warned: bool = False


_PASSING_VERDICTS = {
    "approve",
    "approved",
    "clean",
    "complete",
    "completed",
    "ok",
    "pass",
    "passed",
    "success",
    "succeeded",
}
_FAILING_VERDICTS = {
    "blocked",
    "changes_requested",
    "error",
    "errors",
    "fail",
    "failed",
    "needs_changes",
    "reject",
    "rejected",
}


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _split_workflow_edge_label(label: object) -> tuple[str, str]:
    if not isinstance(label, str):
        return "", ""
    parts = label.split("->", 1)
    if len(parts) != 2:
        stripped = label.strip()
        return stripped, stripped
    return parts[0].strip(), parts[1].strip()


@dataclass(frozen=True)
class WorkflowTerminalNode:
    node_id: str
    label: str
    event_types: list[str]
    join_mode: str
    completion_event_type: str
    require_git_commit: bool = False
    require_git_push: bool = False


@dataclass(frozen=True)
class WorkflowGateNode:
    node_id: str
    label: str
    condition: str
    event_types: list[str]
    mode: str = "human_approval"
    approval_event_type: str = "gate.approved"
    changes_requested_event_type: str = "gate.changes_requested"
    pending_behavior: str = "help_needed"
    instructions: str = ""
    auto_forward_after: str = "30m"


@dataclass
class WorkflowGateState:
    id: str
    node_id: str
    activation_id: str
    label: str
    condition: str
    status: str
    mode: str
    pending_behavior: str
    instructions: str
    auto_forward_after: str
    requested_at: str
    updated_at: str
    triggered_by_event_type: str
    approval_event_type: str
    changes_requested_event_type: str
    attempt: int = 1
    decision: str | None = None
    notes: str = ""
    source: str = "workflow"
    summary: str = ""


def _workflow_terminal_nodes(graph: dict[str, Any] | None) -> list[WorkflowTerminalNode]:
    if not isinstance(graph, dict):
        return []

    nodes = [node for node in graph.get("nodes", []) if isinstance(node, dict)]
    edges = [edge for edge in graph.get("edges", []) if isinstance(edge, dict)]
    terminal_nodes: list[WorkflowTerminalNode] = []

    for node in nodes:
        if str(node.get("kind") or "") != "end":
            continue
        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue
        event_types = _dedupe_preserve_order(
            [
                source_event
                for edge in edges
                if str(edge.get("target") or "").strip() == node_id
                for source_event, _target_event in [_split_workflow_edge_label(edge.get("label"))]
                if source_event and source_event != "complete"
            ]
        )
        if not event_types:
            continue
        terminal_nodes.append(
            WorkflowTerminalNode(
                node_id=node_id,
                label=str(node.get("label") or node_id),
                event_types=event_types,
                join_mode=str(node.get("joinMode") or "all"),
                completion_event_type=str(node.get("completionEvent") or "ravn.task.completed"),
                require_git_commit=bool(
                    (node.get("completionRules") or {}).get("requireGitCommit")
                ),
                require_git_push=bool((node.get("completionRules") or {}).get("requireGitPush")),
            )
        )

    return terminal_nodes


def _workflow_gate_nodes(graph: dict[str, Any] | None) -> list[WorkflowGateNode]:
    if not isinstance(graph, dict):
        return []

    nodes = [node for node in graph.get("nodes", []) if isinstance(node, dict)]
    edges = [edge for edge in graph.get("edges", []) if isinstance(edge, dict)]
    gate_nodes: list[WorkflowGateNode] = []

    for node in nodes:
        if str(node.get("kind") or "") != "gate":
            continue

        node_id = str(node.get("id") or "").strip()
        if not node_id:
            continue

        incoming_event_types = _dedupe_preserve_order(
            [
                source_event
                for edge in edges
                if str(edge.get("target") or "").strip() == node_id
                for source_event, _target_event in [_split_workflow_edge_label(edge.get("label"))]
                if source_event and source_event != "complete"
            ]
        )
        if not incoming_event_types:
            continue

        outgoing_event_types = _dedupe_preserve_order(
            [
                source_event
                for edge in edges
                if str(edge.get("source") or "").strip() == node_id
                for source_event, _target_event in [_split_workflow_edge_label(edge.get("label"))]
                if source_event and source_event != "complete"
            ]
        )
        explicit_approval_event_type = str(
            node.get("approvalEvent") or node.get("approval_event") or ""
        ).strip()
        explicit_changes_requested_event_type = str(
            node.get("changesRequestedEvent") or node.get("changes_requested_event") or ""
        ).strip()
        approval_event_type = explicit_approval_event_type or next(
            (
                event_type
                for event_type in outgoing_event_types
                if "approved" in event_type or event_type.endswith(".approve")
            ),
            outgoing_event_types[0] if outgoing_event_types else "gate.approved",
        )
        changes_requested_event_type = explicit_changes_requested_event_type or next(
            (
                event_type
                for event_type in outgoing_event_types
                if "changes_requested" in event_type or "changes-requested" in event_type
            ),
            next(
                (
                    event_type
                    for event_type in outgoing_event_types
                    if "changes" in event_type or "rework" in event_type
                ),
                outgoing_event_types[1]
                if len(outgoing_event_types) > 1
                else "gate.changes_requested",
            ),
        )
        pending_behavior = (
            str(
                node.get("pendingBehavior") or node.get("pending_behavior") or "help_needed"
            ).strip()
            or "help_needed"
        )
        mode = str(node.get("mode") or "human_approval").strip() or "human_approval"
        instructions = str(node.get("instructions") or "").strip()

        gate_nodes.append(
            WorkflowGateNode(
                node_id=node_id,
                label=str(node.get("label") or node_id),
                condition=str(node.get("condition") or ""),
                event_types=incoming_event_types,
                mode=mode,
                approval_event_type=approval_event_type,
                changes_requested_event_type=changes_requested_event_type,
                pending_behavior=pending_behavior,
                instructions=instructions,
                auto_forward_after=str(node.get("autoForwardAfter") or "30m"),
            )
        )

    return gate_nodes


def _workflow_outcome_passed(payload: dict[str, Any]) -> bool:
    if not bool(payload.get("valid", True)):
        return False

    verdict = str(payload.get("verdict") or "").strip().lower()
    if verdict in _FAILING_VERDICTS:
        return False
    if verdict in _PASSING_VERDICTS:
        return True

    fields = payload.get("fields")
    if isinstance(fields, dict):
        approved = fields.get("approved")
        if approved is False:
            return False
        if approved is True:
            return True
        tests_passing = fields.get("tests_passing")
        if tests_passing is False:
            return False

    tests_passing = payload.get("tests_passing")
    if tests_passing is False:
        return False

    return True


def _workflow_join_satisfied(join_mode: str, outcomes: list[dict[str, Any]]) -> bool:
    if not outcomes:
        return False
    passed = [_workflow_outcome_passed(outcome) for outcome in outcomes]
    match join_mode:
        case "any":
            return any(passed)
        case "merge":
            return all(passed)
        case _:
            return all(passed)
    raise AssertionError("Unreachable _workflow_join_satisfied fallthrough")


def _merge_workflow_terminal_outcomes(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    summaries: list[str] = []
    files_changed: list[str] = []
    seen_files: set[str] = set()
    tests: list[bool] = []
    scope_values: list[float] = []
    checks: list[dict[str, Any]] = []

    for outcome in outcomes:
        event_type = str(outcome.get("event_type") or "").strip()
        persona = str(outcome.get("persona") or "").strip()
        verdict = str(outcome.get("verdict") or "").strip()
        summary = str(outcome.get("summary") or "").strip()
        if summary:
            label = persona or event_type or "outcome"
            summaries.append(f"{label}: {summary}")

        raw_files = outcome.get("files_changed")
        if isinstance(raw_files, list):
            for file_path in raw_files:
                if not isinstance(file_path, str):
                    continue
                normalized = file_path.strip()
                if not normalized or normalized in seen_files:
                    continue
                seen_files.add(normalized)
                files_changed.append(normalized)

        candidate_tests = outcome.get("tests_passing")
        if isinstance(candidate_tests, bool):
            tests.append(candidate_tests)

        candidate_scope = outcome.get("scope_adherence")
        if isinstance(candidate_scope, (int, float)):
            scope_values.append(float(candidate_scope))

        checks.append(
            {
                "persona": persona,
                "event_type": event_type,
                "verdict": verdict,
                "summary": summary,
            }
        )

    merged: dict[str, Any] = {
        "verdict": "approve",
        "summary": " | ".join(summaries) if summaries else "Workflow checks passed",
        "checks": checks,
        "authoritative": True,
    }
    if files_changed:
        merged["files_changed"] = files_changed
    if tests:
        merged["tests_passing"] = all(tests)
    if scope_values:
        merged["scope_adherence"] = min(scope_values)
    return merged


def _workflow_terminal_requirements_satisfied(
    node: WorkflowTerminalNode,
    artifacts: SessionArtifacts,
    *,
    git_checkpoint: GitWorkspaceCheckpoint | None = None,
) -> bool:
    """Return True when deterministic completion rules are satisfied."""
    commit_ok = artifacts.git_commit_count > 0
    push_ok = artifacts.git_push_count > 0
    if git_checkpoint is not None:
        checkpoint_commit_ok, checkpoint_push_ok = _git_workspace_checkpoint_status(git_checkpoint)
        commit_ok = commit_ok or checkpoint_commit_ok
        push_ok = push_ok or checkpoint_push_ok

    if node.require_git_commit and not commit_ok:
        return False
    if node.require_git_push and not push_ok:
        return False
    return True


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

_AUTH_HEADER = "authorization"
_BEARER_PREFIX = "bearer "


def _decode_jwt_claims(token: str) -> dict:
    """Decode JWT payload without signature verification.

    Skuld does not verify signatures — that is Envoy's / the API gateway's
    job.  We only decode to extract user identity claims for API forwarding.
    """
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        # JWT base64url → standard base64
        payload_b64 = parts[1]
        # Add padding
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload_bytes = base64.urlsafe_b64decode(payload_b64)
        return json.loads(payload_bytes)
    except Exception:
        return {}


def _extract_bearer_token(headers: dict[str, str]) -> str | None:
    """Extract Bearer token from an Authorization header value."""
    auth = headers.get(_AUTH_HEADER, "")
    if auth.lower().startswith(_BEARER_PREFIX):
        return auth[len(_BEARER_PREFIX) :].strip()
    return None


def _extract_token_from_websocket(websocket: WebSocket) -> str | None:
    """Extract JWT from WebSocket connection.

    Checks (in order):
    1. Authorization header (Bearer token) — preferred, works with Envoy
    2. Sec-WebSocket-Protocol auth token — browser-safe, avoids URL logging
    3. x-auth-* headers injected by Envoy sidecar
    4. access_token query parameter — legacy browser fallback
    """
    header_items = websocket.headers.items()
    if inspect.iscoroutine(header_items):
        header_items.close()
        header_items = ()
    elif inspect.isawaitable(header_items):
        header_items = ()
    headers = {k.lower(): v for k, v in header_items}

    # 1. Bearer token from Authorization header
    token = _extract_bearer_token(headers)
    if token:
        return token

    # 2. Browser WebSocket cannot set Authorization headers, but it can pass
    # subprotocol tokens. Keep the raw bearer out of the request URL so access
    # logs cannot accidentally capture it.
    protocol_header = headers.get("sec-websocket-protocol", "")
    for protocol in protocol_header.split(","):
        protocol = protocol.strip()
        if protocol.startswith("volundr.bearer."):
            token = protocol.removeprefix("volundr.bearer.").strip()
            if token:
                return urllib.parse.unquote(token)

    # 3. If Envoy x-auth-* headers are present, we don't have the raw JWT
    #    but we have the validated claims — return None (caller uses headers).

    # 4. Query parameter fallback for old clients. New UI code uses
    # Sec-WebSocket-Protocol to avoid logging bearer tokens in URLs.
    query_get = getattr(websocket.query_params, "get", None)
    if not callable(query_get):
        return None
    query_token = query_get("access_token")
    if inspect.iscoroutine(query_token):
        query_token.close()
        return None
    if inspect.isawaitable(query_token):
        return None
    return query_token


CONVERSATION_HISTORY_DIR = ".skuld"
CONVERSATION_HISTORY_FILE = "conversation.json"


@dataclass
class ConversationTurn:
    """A single turn in the conversation history."""

    id: str
    role: str  # "user" | "assistant"
    content: str
    parts: list[dict] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict = field(default_factory=dict)
    # Multi-participant fields (None in single-agent mode)
    participant_id: str | None = None
    participant_meta: dict | None = None
    thread_id: str | None = None
    visibility: str = "public"  # "public" | "internal"


CHRONICLE_SUMMARY_PROMPT = """\
Summarize this coding session in JSON format. Be concise.
Respond ONLY with the JSON object, no markdown fencing, no commentary.

{
  "summary": "One paragraph describing what was accomplished in this session.",
  "key_changes": ["file_or_component: brief description of change", ...],
  "unfinished_work": "Description of anything left incomplete, or null if done."
}
"""

SUMMARY_TIMEOUT_SECONDS = 15


class Broker:
    """WebSocket broker for Claude Code sessions.

    Transport-agnostic: delegates CLI communication to a CLITransport
    implementation selected by configuration.
    """

    def __init__(
        self,
        settings: SkuldSettings | None = None,
        sleipnir_publisher: SleipnirPublisher | None = None,
    ):
        self._settings = settings or SkuldSettings()
        self.session_id = self._settings.session.id
        self.model = self._settings.session.model
        self.workspace_dir = self._settings.workspace_path
        archive_store_cls = import_class(self._settings.archive_store.adapter)
        self._archive_store = archive_store_cls(**self._settings.archive_store.kwargs)
        self.volundr_api_url = self._settings.volundr_api_url
        self._transport: CLITransport | None = None
        self.service_manager: ServiceManager | None = None
        self._channels = ChannelRegistry()
        self._http_client: httpx.AsyncClient | None = None
        self._http_client_jwt: str | None = None  # JWT used to create _http_client
        self._sleipnir_publisher: SleipnirPublisher = sleipnir_publisher or InProcessBus()
        self._artifacts = SessionArtifacts(
            saga_id=self._settings.session.saga_id,
            run_id=self._settings.session.run_id,
        )
        self._flock_completion_reported = False
        self._session_start_reported = False
        self._event_sequence = 0
        # Durable full-fidelity event log (session_event_log). Every CLI frame is
        # buffered here and flushed to Volundr by a background worker, independent
        # of any attached channel — so nothing is dropped when no client is
        # connected, and any client can replay the full transcript from a cursor.
        self._event_log_seq = 0
        self._event_log_buffer: list[dict] = []
        self._event_log_lock = asyncio.Lock()
        self._event_log_task: asyncio.Task[None] | None = None
        self._event_log_stopping = False
        self._activity_state: str = "idle"
        self._last_activity_report: float = 0.0
        self._conversation_turns: list[ConversationTurn] = []
        self._pending_assistant_content: str = ""
        self._pending_assistant_parts: list[dict] = []
        self._pending_block_type: str = ""
        self._pending_reasoning_text: str = ""
        self._pending_explicit_human_messages: list[tuple[str, str]] = []
        self._pending_explicit_human_response_count = 0
        self._chronicle_watcher: ChronicleWatcher | None = None
        self._peer_watchdog_task: asyncio.Task[None] | None = None
        self._workflow_trigger_task: asyncio.Task[None] | None = None
        self._peer_watches: dict[str, PeerWatchState] = {}
        self._peer_pending_commands: dict[str, list[str]] = {}
        self._git_workspace_checkpoint: GitWorkspaceCheckpoint | None = None
        self._workflow_gate_nodes = _workflow_gate_nodes(self._settings.workflow.graph)
        self._workflow_gate_index: dict[str, list[WorkflowGateNode]] = {}
        for node in self._workflow_gate_nodes:
            for event_type in node.event_types:
                self._workflow_gate_index.setdefault(event_type, []).append(node)
        self._workflow_gate_slots: dict[tuple[str, str], set[str]] = {}
        self._workflow_gate_attempts: dict[tuple[str, str], int] = {}
        self._workflow_gate_state_ids_by_key: dict[tuple[str, str], str] = {}
        self._workflow_gate_states: dict[str, WorkflowGateState] = {}
        self._workflow_terminal_nodes = _workflow_terminal_nodes(self._settings.workflow.graph)
        self._workflow_terminal_index: dict[str, list[WorkflowTerminalNode]] = {}
        for node in self._workflow_terminal_nodes:
            for event_type in node.event_types:
                self._workflow_terminal_index.setdefault(event_type, []).append(node)
        self._workflow_terminal_slots: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
        self._workflow_terminal_emitted: set[tuple[str, str]] = set()
        self._trace_id = self._parse_trace_uuid(self.session_id)
        self._trace_session_span_id: uuid.UUID | None = None
        self._trace_workflow_span_id: uuid.UUID | None = None
        self._trace_workflow_gate_spans: dict[str, uuid.UUID] = {}
        self._trace_assistant_span_id: uuid.UUID | None = None
        self._trace_assistant_tool_spans: dict[str, uuid.UUID] = {}
        self._trace_assistant_tool_order: list[str] = []
        self._assistant_pending_commands: dict[str, str] = {}
        self._trace_peer_turn_spans: dict[str, uuid.UUID] = {}
        self._trace_peer_tool_spans: dict[str, list[uuid.UUID]] = {}
        self._pending_permission_requests: dict[str, dict[str, Any]] = {}
        self._permission_auto_approval_tasks: dict[str, asyncio.Task[None]] = {}

        # Mesh adapter — only active when mesh.enabled is True
        self._mesh_adapter: Any = None

        # Room mesh bridge — translates ravn.mesh.* Sleipnir events to room wire events.
        # Active when both mesh.enabled and room.enabled are True.
        self._room_mesh_bridge: RoomMeshBridge | None = None

        # Room bridge — only active when room.enabled is True
        self._room_bridge: RoomBridge | None = (
            RoomBridge(
                config=self._settings.room,
                channels=self._channels,
                append_turn=self._append_turn,
                report_timeline_event=self._report_timeline_event,
                observe_peer_event=self._observe_room_peer_event,
                publish_presence_event=self._publish_room_presence_event,
            )
            if self._settings.room.enabled
            else None
        )

        # Retrieval reflex (NIU-1059) — lazily built from settings.reflex on
        # first forwarded user message; None when disabled.
        self._reflex_injector: Any = None
        self._reflex_initialised = False

        # JWT identity state — populated on first browser WebSocket connection
        self._user_jwt: str | None = None
        self._user_claims: dict = {}

    @staticmethod
    def _parse_trace_uuid(value: object) -> uuid.UUID:
        """Return a stable UUID for arbitrary identifiers."""
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError, AttributeError):
            return uuid.uuid5(uuid.NAMESPACE_URL, str(value))

    def _conversation_history_path(self) -> Path:
        """Return the path to the conversation history file, scoped to session ID."""
        filename = f"conversation_{self.session_id}.json"
        return Path(self.workspace_dir) / CONVERSATION_HISTORY_DIR / filename

    def _load_conversation_history(self) -> None:
        """Load conversation history from disk if it exists."""
        path = self._conversation_history_path()
        if not path.exists():
            return

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            turns = data.get("turns", [])
            self._conversation_turns = [
                ConversationTurn(
                    id=t.get("id", str(uuid.uuid4())),
                    role=t.get("role", "user"),
                    content=t.get("content", ""),
                    parts=t.get("parts", []),
                    created_at=t.get("created_at", ""),
                    metadata=t.get("metadata", {}),
                )
                for t in turns
            ]
            logger.info(
                "Loaded %d conversation turns from %s",
                len(self._conversation_turns),
                path,
            )
        except Exception:
            logger.warning("Failed to load conversation history from %s", path, exc_info=True)

    def _save_conversation_history(self) -> None:
        """Persist conversation history to disk."""
        path = self._conversation_history_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            data = {"turns": [asdict(t) for t in self._conversation_turns]}
            path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            logger.warning("Failed to save conversation history to %s", path, exc_info=True)

    def _append_turn(self, turn: ConversationTurn) -> None:
        """Append a turn and persist to disk."""
        self._conversation_turns.append(turn)
        self._save_conversation_history()
        self._enqueue_event_log(
            {
                "type": "conversation.turn",
                "turn": asdict(turn),
            }
        )

    def _flush_pending_assistant_turn(self, metadata: dict | None = None) -> None:
        """Save any accumulated assistant content as a conversation turn."""
        content = self._pending_assistant_content
        # Save when there's text OR captured parts (tool_use/tool_result/reasoning)
        # — a tool-only assistant turn (no prose) must still persist its tool cards.
        if not content and not self._pending_assistant_parts:
            return

        # Flush remaining reasoning
        if self._pending_reasoning_text:
            summary = self._pending_reasoning_text[-500:]
            self._pending_assistant_parts.append({"type": "reasoning", "text": summary})

        parts = self._pending_assistant_parts if self._pending_assistant_parts else []
        self._append_turn(
            ConversationTurn(
                id=str(uuid.uuid4()),
                role="assistant",
                content=content,
                parts=parts,
                metadata=metadata or {},
            )
        )
        self._pending_assistant_content = ""
        self._pending_assistant_parts = []
        self._pending_reasoning_text = ""

    async def _ensure_workflow_prompt_turn(self) -> None:
        """Persist the workflow trigger prompt without executing it locally."""
        prompt = self._settings.session.initial_prompt
        if not prompt:
            return

        if any(turn.role == "user" and turn.content == prompt for turn in self._conversation_turns):
            return

        turn_id = str(uuid.uuid4())
        self._append_turn(
            ConversationTurn(
                id=turn_id,
                role="user",
                content=prompt,
            )
        )
        await self._complete_trace_span(
            kind="turn.user",
            name=prompt[:120] or "workflow prompt",
            parent_span_id=self._trace_session_span_id,
            actor_type="user",
            actor_id="workflow-trigger",
            actor_label="workflow trigger",
            attributes={"source": "workflow_trigger"},
            started_at=datetime.now(UTC),
            ended_at=datetime.now(UTC),
        )
        await self._channels.broadcast(
            {
                "type": "user_confirmed",
                "id": turn_id,
                "content": prompt,
            }
        )

    async def _start_mesh_adapter(self) -> None:
        """Build and start the mesh adapter when mesh.enabled is True.

        Uses niuu.mesh functions to build transport and discovery, then wraps
        them in a MeshParticipant for unified lifecycle management (NIU-634).
        """
        from niuu.mesh import build_in_process_mesh, resolve_peer_id
        from niuu.mesh.participant import MeshParticipant
        from niuu.mesh.transport_builder import build_nng_transport
        from skuld.mesh_adapter import SkuldMeshAdapter

        mesh_cfg = self._settings.mesh
        own_peer_id = resolve_peer_id(mesh_cfg.peer_id)

        # Build mesh transport (nng preferred, in-process fallback)
        mesh = None
        if mesh_cfg.transport != "in_process":
            try:
                from ravn.adapters.mesh.sleipnir_mesh import SleipnirMeshAdapter  # noqa: PLC0415

                nng_cfg = getattr(mesh_cfg, "nng", None)
                address = (
                    getattr(nng_cfg, "pub_sub_address", "tcp://127.0.0.1:0")
                    if nng_cfg
                    else "tcp://127.0.0.1:0"
                )
                peer_addresses = read_cluster_pub_addresses(mesh_cfg.adapters)
                nng = build_nng_transport(
                    address=address,
                    service_id=f"skuld:{own_peer_id}",
                    peer_addresses=peer_addresses or None,
                )
                if nng is not None:
                    mesh = SleipnirMeshAdapter(
                        publisher=nng,
                        subscriber=nng,
                        own_peer_id=own_peer_id,
                        rpc_timeout_s=mesh_cfg.rpc_timeout_s,
                    )
            except ImportError:
                logger.warning("mesh: nng transport not available, falling back to in-process")

        if mesh is None:
            mesh = build_in_process_mesh(own_peer_id, mesh_cfg.rpc_timeout_s)

        # Build discovery adapter using shared niuu.mesh.discovery_builder
        own_identity = MeshIdentity(
            peer_id=mesh_cfg.peer_id or self.session_id or "skuld",
            realm_id="",
            persona=mesh_cfg.persona,
            capabilities=list(mesh_cfg.capabilities),
            permission_mode="full_access",
            version="0.1.0",
        )
        discovery = build_discovery_adapters(
            adapters_config=mesh_cfg.adapters,
            own_identity=own_identity,
        )

        participant = MeshParticipant(
            mesh=mesh,
            discovery=discovery,
            peer_id=own_peer_id,
        )

        self._mesh_adapter = SkuldMeshAdapter(
            participant=participant,
            transport=self._transport,
            config=mesh_cfg,
            session_id=self.session_id,
        )

        try:
            await self._mesh_adapter.start()
            logger.info(
                "Mesh adapter started (peer_id=%s)",
                self._mesh_adapter.peer_id,
            )

            # Register Skuld + discovered mesh peers as room participants
            # so the browser UI shows them without a direct WebSocket.
            if self._room_bridge is not None:
                await self._room_bridge.register_mesh_peer(
                    peer_id=self._mesh_adapter.peer_id,
                    persona="Skuld",
                    display_name="Skuld",
                    subscribes_to=list(mesh_cfg.consumes_event_types),
                    emits=["code.changed"],
                    tools=list(mesh_cfg.tools),
                    participant_type="skuld",
                    participant_kind="mesh",
                    heartbeat_ttl_s=0.0,
                )
            has_peers = discovery is not None and hasattr(discovery, "peers")
            if self._room_bridge is not None and has_peers:
                for peer in discovery.peers().values():
                    await self._room_bridge.register_mesh_peer(
                        peer_id=peer.peer_id,
                        persona=peer.persona,
                        display_name=peer.persona,
                        subscribes_to=list(getattr(peer, "consumes_event_types", [])),
                        emits=list(getattr(peer, "emits_event_types", [])),
                        tools=list(getattr(peer, "capabilities", [])),
                        participant_kind="mesh",
                        heartbeat_ttl_s=0.0,
                    )

            # Start room mesh bridge so outcomes from any mesh peer flow to the
            # room UI via Sleipnir — eliminates the dual-publish pattern.
            if self._room_bridge is not None:
                from sleipnir.ports.events import SleipnirSubscriber

                sleipnir_subscriber = self._mesh_adapter.sleipnir_subscriber
                if sleipnir_subscriber is None:
                    # Fallback: InProcessBus implements both Publisher and Subscriber.
                    # Only use it if it actually satisfies the Subscriber interface.
                    if isinstance(self._sleipnir_publisher, SleipnirSubscriber):
                        sleipnir_subscriber = self._sleipnir_publisher
                    else:
                        logger.warning(
                            "Sleipnir publisher does not implement SleipnirSubscriber"
                            " — RoomMeshBridge disabled"
                        )
                if sleipnir_subscriber is not None:
                    self._room_mesh_bridge = RoomMeshBridge(
                        subscriber=sleipnir_subscriber,
                        room_bridge=self._room_bridge,
                        session_id=self.session_id,
                    )
                    await self._room_mesh_bridge.start()
                    logger.info("RoomMeshBridge started (session_id=%s)", self.session_id)

        except Exception as exc:
            logger.error("Mesh adapter start failed: %r", exc, exc_info=True)
            self._mesh_adapter = None

    def _has_workflow_trigger(self) -> bool:
        cfg = self._settings.workflow_trigger
        return bool(cfg.enabled and cfg.event_type and self._settings.session.initial_prompt)

    def _is_room_only_workflow_session(self) -> bool:
        """Return True when flock workflow sessions should stay room-only.

        In these sessions, browser traffic should observe and direct mesh peers
        through the room bridge. Lazy-starting Skuld's own CLI transport on
        browser connect would spawn a second agent and confuse session state.
        """
        return bool(self._has_workflow_trigger() and self._settings.room.enabled)

    def _workflow_trigger_consumer_peer_ids(self, event_type: str) -> set[str]:
        """Return flock peer ids that subscribe to the workflow trigger event."""
        if self._room_bridge is None or not event_type:
            return set()
        peer_ids: set[str] = set()
        for participant in self._room_bridge.participants.values():
            if participant.participant_type == "skuld":
                continue
            if event_type in participant.subscribes_to:
                peer_ids.add(participant.peer_id)
        return peer_ids

    def _workflow_trigger_peer_ready(self, peer_id: str) -> bool:
        """Return True when a workflow-trigger consumer can receive mesh events."""
        if self._room_bridge is None:
            return True
        participant = self._room_bridge.participants.get(peer_id)
        if participant is not None and getattr(participant, "participant_kind", "") == "mesh":
            return True
        return bool(self._room_bridge.is_connected(peer_id))

    async def _wait_for_workflow_trigger_consumers(
        self,
        event_type: str,
        timeout_s: float,
        *,
        poll_interval_s: float = 0.05,
    ) -> bool:
        """Wait until the initial workflow-trigger consumers are connected.

        Workflow kickoff events are pub/sub outcomes. If we publish them before
        the first consumer peer finishes its WebSocket registration, the event
        can be dropped and the flock stalls indefinitely. We treat the trigger
        subscribers as required startup dependencies and wait briefly for them
        to connect before dispatching the initial event.
        """
        required_peers = self._workflow_trigger_consumer_peer_ids(event_type)
        if not required_peers or self._room_bridge is None:
            return True

        deadline = time.monotonic() + max(0.0, timeout_s)
        missing = {
            peer_id for peer_id in required_peers if not self._workflow_trigger_peer_ready(peer_id)
        }
        if missing:
            logger.info(
                "Workflow trigger waiting for consumers event_type=%s peers=%s timeout=%.1fs",
                event_type,
                sorted(missing),
                max(0.0, timeout_s),
            )

        while missing and time.monotonic() < deadline:
            await asyncio.sleep(poll_interval_s)
            missing = {
                peer_id
                for peer_id in required_peers
                if not self._workflow_trigger_peer_ready(peer_id)
            }

        if missing:
            logger.error(
                "Workflow trigger consumers failed to connect before dispatch "
                "event_type=%s peers=%s",
                event_type,
                sorted(missing),
            )
            return False

        # Give newly connected peers one short beat to finish their channel
        # registration/subscription handshake before the first pub/sub event.
        await asyncio.sleep(poll_interval_s)
        logger.info(
            "Workflow trigger consumers ready event_type=%s peers=%s",
            event_type,
            sorted(required_peers),
        )
        return True

    async def _publish_workflow_trigger(self) -> None:
        """Publish the initial Ting task into the flock as a mesh outcome event."""
        if self._mesh_adapter is None or not self._has_workflow_trigger():
            return

        from ravn.domain.events import RavnEvent, RavnEventType

        cfg = self._settings.workflow_trigger
        # Workflow kickoff is a required dependency for flock-backed workflows.
        # Large flocks can take several seconds to finish peer startup, so we
        # give them a more realistic readiness window and fail closed rather
        # than silently dropping the first workflow event.
        wait_timeout_s = max(float(cfg.startup_delay_s or 0.0), 20.0)
        consumers_ready = await self._wait_for_workflow_trigger_consumers(
            cfg.event_type,
            wait_timeout_s,
        )
        if not consumers_ready:
            raise RuntimeError(f"workflow trigger consumers for {cfg.event_type} did not connect")

        delay_s = max(0.0, float(cfg.startup_delay_s or 0.0))
        if delay_s and not self._workflow_trigger_consumer_peer_ids(cfg.event_type):
            logger.info(
                "Workflow trigger waiting %.1fs for flock subscribers before mesh dispatch",
                delay_s,
            )
            await asyncio.sleep(delay_s)
        event = RavnEvent(
            type=RavnEventType.OUTCOME,
            source=f"skuld:{self._mesh_adapter.peer_id}",
            payload={
                "event_type": cfg.event_type,
                "session_id": self.session_id,
                "persona": "skuld",
                "summary": f"Workflow dispatch: {cfg.label or cfg.event_type}",
                "task_description": self._settings.session.initial_prompt,
                "trigger_source": cfg.source,
                "workflow_trigger_label": cfg.label,
                "workflow_trigger_node_id": cfg.node_id,
                "workspace_path": self.workspace_dir,
            },
            timestamp=datetime.now(UTC),
            urgency=0.8,
            correlation_id=self.session_id,
            session_id=self.session_id,
            root_correlation_id=self.session_id,
        )
        await self._mesh_adapter.publish(event, cfg.event_type)
        logger.info(
            "Workflow trigger dispatched onto mesh event_type=%s node_id=%s",
            cfg.event_type,
            cfg.node_id,
        )

    async def _run_workflow_trigger_task(self) -> None:
        """Dispatch the initial workflow trigger after broker startup completes."""
        cfg = self._settings.workflow_trigger
        workflow_name = (
            str(getattr(self._settings.workflow, "name", "") or "").strip()
            or cfg.label
            or "workflow"
        )
        if self._trace_workflow_span_id is None:
            self._trace_workflow_span_id = await self._start_trace_span(
                kind="session.workflow",
                name=workflow_name,
                parent_span_id=self._trace_session_span_id,
                actor_type="workflow",
                actor_id=cfg.node_id or cfg.event_type or "workflow",
                actor_label=cfg.label or workflow_name,
                attributes={
                    "event_type": cfg.event_type,
                    "node_id": cfg.node_id,
                    "source": cfg.source,
                },
            )
        try:
            await self._publish_workflow_trigger()
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._finish_trace_span(
                self._trace_workflow_span_id,
                status="failed",
                attributes={"reason": "workflow_trigger_failed"},
            )
            self._trace_workflow_span_id = None
            logger.exception("Workflow trigger dispatch failed")
            raise

    def _build_transport_kwargs(self) -> dict:
        """Return superset of kwargs that any transport constructor might need."""
        return {
            "workspace_dir": self.workspace_dir,
            "model": self.model,
            "sdk_port": self._settings.port,
            "session_id": self.session_id,
            "skip_permissions": self._settings.skip_permissions,
            "approval_policy": self._settings.approval_policy,
            "sandbox": self._settings.sandbox,
            "agent_teams": self._settings.agent_teams,
            "system_prompt": self._settings.session.system_prompt,
            "initial_prompt": (
                "" if self._has_workflow_trigger() else self._settings.session.initial_prompt
            ),
            "mcp_servers": self._settings.mcp_servers,
            "resume_session_id": self._settings.session.resume_session_id,
            "ask_user_question_enabled": self._settings.ask_user_question_enabled,
            "acp_prompt_timeout_s": self._settings.acp_prompt_timeout_s,
        }

    def _create_transport(self) -> CLITransport:
        """Create the configured CLI transport via dynamic import.

        Uses ``transport_adapter`` from settings (a fully-qualified class path).
        Legacy ``cli_type`` / ``transport`` fields are resolved to the correct
        adapter path by the config validator before this method is called.
        """
        adapter_path = self._settings.transport_adapter
        if "." not in adapter_path:
            raise ValueError(
                f"Invalid transport_adapter '{adapter_path}': "
                "must be a fully-qualified class path "
                "(e.g. 'skuld.transports.sdk_websocket.SdkWebSocketTransport')"
            )

        try:
            cls = import_class(adapter_path)
        except (ImportError, AttributeError) as exc:
            raise ValueError(f"Cannot load transport adapter '{adapter_path}': {exc}") from exc

        kwargs = self._build_transport_kwargs()
        sig = inspect.signature(cls)
        filtered = {k: v for k, v in kwargs.items() if k in sig.parameters}
        logger.info("Using %s (adapter: %s)", cls.__name__, adapter_path)
        return cls(**filtered)

    async def _auto_start_transport(self) -> None:
        """Background-task wrapper around ``self._transport.start()``.

        Claude subprocess transports do not echo the user's own prompt back as
        an event. Without an explicit synthesis the chat UI sees the
        assistant's reply with no user turn before it. We append a user turn
        to conversation history (so late-joining browsers see it via replay)
        and broadcast ``user_confirmed`` so any already-connected channel
        renders it immediately.
        """
        prompt = self._settings.session.initial_prompt
        if prompt and not any(
            t.role == "user" and t.content == prompt for t in self._conversation_turns
        ):
            turn_id = str(uuid.uuid4())
            self._append_turn(ConversationTurn(id=turn_id, role="user", content=prompt))
            # The initial prompt is a human turn too — persist it to the durable
            # log so log-only replay opens with the operator's first message.
            self._enqueue_human_turn_event(prompt, turn_id)
            try:
                await self._channels.broadcast(
                    {"type": "user_confirmed", "id": turn_id, "content": prompt}
                )
            except Exception:
                logger.debug("Initial-prompt user_confirmed broadcast failed", exc_info=True)

        try:
            await self._transport.start()
            logger.info("Transport auto-started successfully")
        except Exception:
            logger.error("Transport auto-start failed", exc_info=True)

    async def startup(self) -> None:
        """Initialize the broker on startup."""
        logger.info("Broker starting for session %s", self.session_id)
        logger.info("Transport adapter: %s", self._settings.transport_adapter)
        await self._ensure_session_trace_started()

        if self.volundr_api_url:
            logger.info("Token usage reporting enabled: %s", self.volundr_api_url)
        else:
            logger.warning("SKULD__VOLUNDR_API_URL not set — token usage will not be reported")

        # Ensure workspace directory exists
        os.makedirs(self.workspace_dir, exist_ok=True)

        # Load conversation history from disk
        self._load_conversation_history()

        # Evict participants whose heartbeats lapse (room mode only)
        if self._room_bridge is not None:
            self._room_bridge.start_presence_sweep()

        # Initialize transport
        self._transport = self._create_transport()
        self._transport.on_event(self._handle_cli_event)

        # Initialize service manager
        self.service_manager = ServiceManager(self.workspace_dir)
        await self.service_manager.init()
        logger.info("Service manager initialized")
        self._git_workspace_checkpoint = _capture_git_workspace_checkpoint(self.workspace_dir)

        # Initialize Telegram channel if configured
        await self._init_telegram_channel()

        # Start chronicle watcher (tails JSONL session files for terminal mode)
        if self._settings.chronicle_watcher_enabled and self.volundr_api_url:
            workspace_slug = self.workspace_dir.replace("/", "-")
            watch_dir = Path.home() / ".claude" / "projects" / workspace_slug
            self._chronicle_watcher = ChronicleWatcher(
                session_id=self.session_id,
                watch_dir=watch_dir,
                api_base_url=self.volundr_api_url,
                http_headers=self._build_auth_headers(),
                debounce_ms=self._settings.chronicle_watcher_debounce_ms,
            )
            asyncio.create_task(self._chronicle_watcher.start())
            logger.info("Chronicle watcher started for %s", watch_dir)

        # Start the durable event-log worker (full-fidelity transcript capture)
        await self._init_event_log()

        # Auto-start transport when an initial prompt is configured
        # (dispatched sessions should begin work immediately, not wait
        # for a browser to connect). Run as a background task so the
        # lifespan returns promptly and uvicorn binds — otherwise the
        # transport's first turn (which can take seconds to minutes)
        # blocks the HTTP listener and the chat UI gets 502s.
        if self._settings.session.initial_prompt:
            if self._has_workflow_trigger():
                logger.info(
                    "Workflow trigger configured — holding initial prompt for mesh dispatch"
                )
                await self._ensure_workflow_prompt_turn()
            else:
                logger.info("Initial prompt configured — auto-starting transport in background")
                asyncio.create_task(self._auto_start_transport())
        elif self._conversation_turns:
            # Resumed/restarted session (no new initial prompt, but prior history
            # was loaded). Warm the transport eagerly so it is already alive when
            # the next user message arrives. The message-delivery path connects a
            # WebSocket and closes immediately after sending; a cold transport's
            # ~280ms lazy-start outlasts that connection and the first message is
            # dropped (no reply). Warming here makes a restarted session behave
            # like a never-stopped one, where steering works.
            logger.info("Resumed session with prior history — warming transport in background")
            asyncio.create_task(self._auto_start_transport())
        elif "RemoteControl" in (self._settings.transport_adapter or ""):
            # Remote-control sessions take no initial prompt (the native app
            # drives them), so neither branch above fires — but we still want the
            # RC server launched immediately so the pairing URL is ready without
            # waiting for a browser to connect.
            logger.info("Remote-control session — launching the RC server in background")
            asyncio.create_task(self._auto_start_transport())

        # Start mesh adapter if enabled (after transport is ready)
        if self._settings.mesh.enabled:
            await self._start_mesh_adapter()
            if self._has_workflow_trigger():
                self._workflow_trigger_task = asyncio.create_task(self._run_workflow_trigger_task())
        elif self._has_workflow_trigger():
            logger.warning("Workflow trigger configured but mesh is disabled — skipping dispatch")

        if (
            self._room_bridge is not None
            and self._settings.mesh.enabled
            and self._settings.peer_watchdog.enabled
        ):
            self._peer_watchdog_task = asyncio.create_task(self._peer_watchdog_loop())

    async def shutdown(self) -> None:
        """Clean up on shutdown.

        Reports chronicle summary to Volundr API before stopping the
        transport, so the CLI process is still alive for summary generation.
        """
        logger.info("Broker shutting down")

        if self._room_bridge is not None:
            await self._room_bridge.stop_presence_sweep()

        # Stop chronicle watcher first (flush pending events)
        if self._chronicle_watcher:
            await self._chronicle_watcher.stop()

        # Drain and stop the durable event-log worker so the last turn persists
        await self._stop_event_log()

        if self._peer_watchdog_task is not None:
            self._peer_watchdog_task.cancel()
            await asyncio.gather(self._peer_watchdog_task, return_exceptions=True)
            self._peer_watchdog_task = None

        if self._workflow_trigger_task is not None:
            self._workflow_trigger_task.cancel()
            await asyncio.gather(self._workflow_trigger_task, return_exceptions=True)
            self._workflow_trigger_task = None

        if self._permission_auto_approval_tasks:
            for task in list(self._permission_auto_approval_tasks.values()):
                task.cancel()
            await asyncio.gather(
                *self._permission_auto_approval_tasks.values(),
                return_exceptions=True,
            )
            self._permission_auto_approval_tasks.clear()
        await self._finish_pending_assistant_tool_trace_spans(
            status="cancelled",
            attributes={"reason": "shutdown"},
        )
        await self._finish_trace_span(
            self._trace_assistant_span_id,
            status="cancelled",
            attributes={"reason": "shutdown"},
        )
        self._trace_assistant_span_id = None
        for gate_span_id in list(self._trace_workflow_gate_spans.values()):
            await self._finish_trace_span(
                gate_span_id,
                status="cancelled",
                attributes={"reason": "shutdown"},
            )
        self._trace_workflow_gate_spans.clear()
        for peer_id, tool_span_ids in list(self._trace_peer_tool_spans.items()):
            for tool_span_id in tool_span_ids:
                await self._finish_trace_span(
                    tool_span_id,
                    status="cancelled",
                    attributes={"reason": "shutdown", "peer_id": peer_id},
                )
        self._trace_peer_tool_spans.clear()
        for peer_id, peer_span_id in list(self._trace_peer_turn_spans.items()):
            await self._finish_trace_span(
                peer_span_id,
                status="cancelled",
                attributes={"reason": "shutdown", "peer_id": peer_id},
            )
        self._trace_peer_turn_spans.clear()
        await self._finish_trace_span(
            self._trace_workflow_span_id,
            status="completed",
            attributes={
                "duration_seconds": self._artifacts.duration_seconds,
                "turn_count": self._artifacts.turn_count,
            },
        )
        self._trace_workflow_span_id = None

        # Report chronicle BEFORE stopping the transport (CLI must be alive)
        await self._report_chronicle()
        await self._write_workspace_archive()

        # Stop room mesh bridge before mesh adapter
        if self._room_mesh_bridge is not None:
            await self._room_mesh_bridge.stop()
            self._room_mesh_bridge = None

        # Stop mesh adapter before transport (deregister from discovery)
        if self._mesh_adapter is not None:
            await self._mesh_adapter.stop()
            self._mesh_adapter = None

        # Close all message channels (browser WebSockets, Telegram, etc.)
        await self._channels.close_all()

        # Stop transport
        if self._transport:
            await self._transport.stop()

        await self._finish_trace_span(
            self._trace_session_span_id,
            status="completed",
            attributes={
                "duration_seconds": self._artifacts.duration_seconds,
                "turn_count": self._artifacts.turn_count,
                "files_changed": len(self._artifacts.files_changed),
            },
        )
        self._trace_session_span_id = None

        # Close HTTP client
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

    def _observer_peer_id(self) -> str:
        """Return Skuld's room participant id when available."""
        if self._mesh_adapter is not None and getattr(self._mesh_adapter, "peer_id", ""):
            return str(self._mesh_adapter.peer_id)
        if self._room_bridge is None:
            return ""
        for participant in self._room_bridge.participants.values():
            if participant.participant_type == "skuld":
                return participant.peer_id
        return ""

    def _refresh_git_workspace_artifacts(self) -> None:
        """Fold deterministic git workspace state into the session artifact counters."""
        commit_ok, push_ok = _git_workspace_checkpoint_status(self._git_workspace_checkpoint)
        if commit_ok:
            self._artifacts.git_commit_count = max(self._artifacts.git_commit_count, 1)
        if push_ok:
            self._artifacts.git_push_count = max(self._artifacts.git_push_count, 1)

    async def _observe_room_peer_event(
        self,
        peer_id: str,
        event_type: str,
        frame: dict[str, Any],
    ) -> None:
        """Track flock peer progress so Skuld can surface silent failures."""
        if self._room_bridge is None:
            return
        observer_peer_id = self._observer_peer_id()
        if peer_id == observer_peer_id:
            return
        participant = self._room_bridge.participants.get(peer_id)
        peer_label = (
            participant.persona
            if participant is not None and getattr(participant, "persona", "")
            else peer_id
        )

        if event_type == "outcome":
            await self._emit_peer_outcome_pipeline_event(peer_id, frame)
        elif event_type == "help_needed":
            await self._report_peer_help_needed_activity(peer_id, frame)
            await self._emit_peer_help_needed_sleipnir_event(peer_id, frame)

        now = time.monotonic()
        watch = self._peer_watches.get(peer_id)

        if event_type == "task_started":
            metadata = frame.get("metadata", {})
            title = str(metadata.get("title") or frame.get("data") or "task")
            task_id = str(metadata.get("task_id") or frame.get("task_id") or peer_id)
            existing_turn_span_id = self._trace_peer_turn_spans.get(peer_id)
            if existing_turn_span_id is None:
                self._trace_peer_turn_spans[peer_id] = await self._start_trace_span(
                    kind="turn.peer",
                    name=title or peer_label,
                    parent_span_id=self._trace_session_span_id,
                    actor_type="peer",
                    actor_id=peer_id,
                    actor_label=peer_label,
                    attributes={"task_id": task_id},
                )
            self._peer_watches[peer_id] = PeerWatchState(
                peer_id=peer_id,
                task_id=task_id,
                title=title,
                started_at=now,
                last_progress_at=now,
                last_status="busy",
            )
            return

        if event_type == "tool_start":
            metadata = frame.get("metadata", {})
            tool_input = metadata.get("input") if isinstance(metadata, dict) else {}
            tool_name = (
                str(metadata.get("tool_name") or frame.get("data") or "tool").strip() or "tool"
            )
            if peer_id not in self._trace_peer_turn_spans:
                self._trace_peer_turn_spans[peer_id] = await self._start_trace_span(
                    kind="turn.peer",
                    name=peer_label,
                    parent_span_id=self._trace_session_span_id,
                    actor_type="peer",
                    actor_id=peer_id,
                    actor_label=peer_label,
                    attributes={"source": "tool_start"},
                )
            tool_span_id = await self._start_trace_span(
                kind="tool.call",
                name=tool_name,
                parent_span_id=self._trace_peer_turn_spans.get(peer_id),
                actor_type="peer",
                actor_id=peer_id,
                actor_label=peer_label,
                attributes={"tool_input": tool_input if isinstance(tool_input, dict) else {}},
            )
            if tool_span_id is not None:
                self._trace_peer_tool_spans.setdefault(peer_id, []).append(tool_span_id)
            command = ""
            if isinstance(tool_input, dict):
                command = str(tool_input.get("command") or "").strip()
            if command:
                self._peer_pending_commands.setdefault(peer_id, []).append(command)

        if event_type == "tool_result":
            metadata = frame.get("metadata", {})
            pending = self._peer_pending_commands.get(peer_id, [])
            command = pending.pop(0) if pending else ""
            if not pending:
                self._peer_pending_commands.pop(peer_id, None)
            if command and not bool(metadata.get("is_error")):
                if _is_git_commit(command):
                    self._artifacts.git_commit_count += 1
                elif _is_git_push(command):
                    self._artifacts.git_push_count += 1
            tool_spans = self._trace_peer_tool_spans.get(peer_id, [])
            tool_span_id = tool_spans.pop(0) if tool_spans else None
            if not tool_spans:
                self._trace_peer_tool_spans.pop(peer_id, None)
            await self._finish_trace_span(
                tool_span_id,
                status="failed" if bool(metadata.get("is_error")) else "completed",
                attributes={
                    "command": command,
                    "is_error": bool(metadata.get("is_error")),
                },
            )
        elif event_type in {"response", "task_complete"}:
            tool_spans = self._trace_peer_tool_spans.pop(peer_id, [])
            pending_commands = self._peer_pending_commands.pop(peer_id, [])
            for index, tool_span_id in enumerate(tool_spans):
                command = pending_commands[index] if index < len(pending_commands) else ""
                await self._finish_trace_span(
                    tool_span_id,
                    status="completed",
                    attributes={
                        "command": command,
                        "peer_id": peer_id,
                        "terminal_event": event_type,
                    },
                )
        elif event_type == "error":
            tool_spans = self._trace_peer_tool_spans.pop(peer_id, [])
            pending_commands = self._peer_pending_commands.pop(peer_id, [])
            for index, tool_span_id in enumerate(tool_spans):
                command = pending_commands[index] if index < len(pending_commands) else ""
                await self._finish_trace_span(
                    tool_span_id,
                    status="failed",
                    attributes={
                        "command": command,
                        "peer_id": peer_id,
                        "terminal_event": event_type,
                    },
                )

        if watch is None:
            if event_type == "outcome":
                await self._maybe_report_flock_completion(peer_id, frame)
            if event_type in {"response", "error", "task_complete"}:
                peer_span_id = self._trace_peer_turn_spans.pop(peer_id, None)
                await self._finish_trace_span(
                    peer_span_id,
                    status="failed" if event_type == "error" else "completed",
                    attributes={"event_type": event_type, "peer_id": peer_id},
                )
            return

        watch.last_progress_at = now
        watch.warned = False
        if event_type == "tool_start":
            watch.last_status = "tool_executing"
        elif event_type in {"thought", "thinking"}:
            watch.last_status = "thinking"
        elif event_type in {"tool_result", "task_complete"}:
            watch.last_status = "idle"
        elif event_type == "error":
            watch.last_status = "error"
            self._peer_watches.pop(peer_id, None)
        elif event_type in {"response", "outcome", "help_needed"}:
            self._peer_watches.pop(peer_id, None)

        if event_type in {"response", "error", "task_complete"}:
            peer_span_id = self._trace_peer_turn_spans.pop(peer_id, None)
            await self._finish_trace_span(
                peer_span_id,
                status="failed" if event_type == "error" else "completed",
                attributes={"event_type": event_type, "peer_id": peer_id},
            )

        if event_type == "outcome":
            await self._maybe_report_flock_completion(peer_id, frame)

    async def _emit_peer_outcome_pipeline_event(
        self,
        peer_id: str,
        frame: dict[str, Any],
    ) -> None:
        """Persist a canonical peer outcome into the outer session event stream."""
        data = frame.get("data", {})
        if not isinstance(data, dict):
            return

        metadata = frame.get("metadata", {})
        event_type = str(metadata.get("event_type") or data.get("event_type") or "").strip()
        if not event_type:
            return

        participant = self._room_bridge.participants.get(peer_id)
        persona = (
            participant.persona if participant is not None else str(data.get("persona") or "")
        ).strip()

        fields = data.get("fields")
        if not isinstance(fields, dict):
            nested_outcome = data.get("outcome")
            fields = nested_outcome if isinstance(nested_outcome, dict) else {}

        payload: dict[str, Any] = {
            "peer_id": peer_id,
            "persona": persona,
            "event_type": event_type,
            "canonical_event_type": str(data.get("canonical_event_type") or event_type),
            "fields": fields,
            "valid": bool(data.get("valid", True)),
        }
        verdict = data.get("verdict") or fields.get("verdict")
        if verdict:
            payload["verdict"] = verdict
        summary = data.get("summary") or fields.get("summary")
        if summary:
            payload["summary"] = summary
        files_changed = data.get("files_changed") or fields.get("files_changed")
        if isinstance(files_changed, list) and files_changed:
            payload["files_changed"] = files_changed

        if not data.get("routing_only") and data.get("bubble_up") is not False:
            await self._emit_pipeline_event("outcome", payload)
        await self._maybe_activate_workflow_gate(frame, payload)
        await self._maybe_emit_workflow_terminal_outcome(peer_id, frame, payload)

    async def _emit_peer_help_needed_sleipnir_event(
        self,
        peer_id: str,
        frame: dict[str, Any],
    ) -> None:
        """Publish a canonical help-needed event so Ting can request human input."""
        payload = self._build_peer_help_needed_payload(peer_id, frame)
        if payload is None:
            return
        session_id = str(payload.get("session_id") or "").strip()

        event = SleipnirEvent(
            event_type="ravn.help.needed",
            source=f"ravn:{peer_id}",
            payload=payload,
            urgency=float(frame.get("metadata", {}).get("urgency", 0.85)),
            correlation_id=session_id or self.session_id,
            summary=f"Help needed ({payload.get('persona') or peer_id}): {payload['summary']}",
            domain="code",
            timestamp=SleipnirEvent.now(),
        )
        try:
            await self._sleipnir_publisher.publish(event)
            logger.info(
                "Peer help-needed event emitted: peer=%s session=%s run=%s",
                peer_id,
                session_id or "-",
                self._artifacts.run_id or "-",
            )
        except Exception:
            logger.warning("Failed to emit peer help-needed event", exc_info=True)

    def _build_peer_help_needed_payload(
        self,
        peer_id: str,
        frame: dict[str, Any],
    ) -> dict[str, Any] | None:
        data = frame.get("data", {})
        if isinstance(data, str):
            data = {"summary": data}
        if not isinstance(data, dict):
            return None

        participant = self._room_bridge.participants.get(peer_id)
        persona = (
            participant.persona if participant is not None else str(data.get("persona") or "")
        ).strip()
        context = data.get("context")
        if not isinstance(context, dict):
            context = {}

        session_id = str(
            data.get("session_id")
            or context.get("session_id")
            or frame.get("session_id")
            or self.session_id
        ).strip()
        payload: dict[str, Any] = {
            "session_id": session_id,
            "persona": persona,
            "summary": str(data.get("summary") or "Agent requested human feedback."),
            "reason": str(data.get("reason") or "needs_context"),
            "recommendation": str(data.get("recommendation") or ""),
            "context": context,
            "target_peer_id": peer_id,
        }
        attempted = data.get("attempted")
        if isinstance(attempted, list):
            payload["attempted"] = [str(item) for item in attempted if str(item).strip()]
        if self._artifacts.run_id:
            payload["run_id"] = self._artifacts.run_id
        if self._artifacts.saga_id:
            payload["saga_id"] = self._artifacts.saga_id
        return payload

    def list_workflow_gates(self) -> list[dict[str, Any]]:
        """Return workflow gate states ordered from newest to oldest."""
        states = sorted(
            self._workflow_gate_states.values(),
            key=lambda state: (state.updated_at, state.requested_at),
            reverse=True,
        )
        return [asdict(state) for state in states]

    def _workflow_activation_id_from_frame(self, frame: dict[str, Any]) -> str:
        data = frame.get("data", {})
        if not isinstance(data, dict):
            data = {}
        metadata = frame.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        return (
            str(
                data.get("workflow_parent_event_id")
                or data.get("workflow_activation_id")
                or metadata.get("task_id")
                or frame.get("root_correlation_id")
                or self.session_id
            ).strip()
            or self.session_id
        )

    def _build_workflow_gate_help_needed_payload(
        self,
        state: WorkflowGateState,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "session_id": self.session_id,
            "persona": "workflow-gate",
            "summary": state.summary
            or f"{state.label} is waiting for human approval before the workflow can continue.",
            "reason": "needs_human_approval",
            "recommendation": state.condition
            or ("Review the pending gate and decide whether to approve or request changes."),
            "context": {
                "gate_id": state.id,
                "gate_label": state.label,
                "gate_node_id": state.node_id,
                "gate_status": state.status,
                "workflow_activation_id": state.activation_id,
                "mode": state.mode,
                "pending_behavior": state.pending_behavior,
                "instructions": state.instructions,
                "auto_forward_after": state.auto_forward_after,
            },
            "target_peer_id": self._observer_peer_id() or "workflow-gate",
        }
        if self._artifacts.run_id:
            payload["run_id"] = self._artifacts.run_id
        if self._artifacts.saga_id:
            payload["saga_id"] = self._artifacts.saga_id
        return payload

    async def _emit_workflow_gate_help_needed_sleipnir_event(
        self,
        state: WorkflowGateState,
    ) -> None:
        payload = self._build_workflow_gate_help_needed_payload(state)
        event = SleipnirEvent(
            event_type="ravn.help.needed",
            source=f"workflow-gate:{state.node_id}",
            payload=payload,
            urgency=0.85,
            correlation_id=state.activation_id or self.session_id,
            summary=f"Workflow gate pending ({state.label}): {payload['summary']}",
            domain="code",
            timestamp=SleipnirEvent.now(),
        )
        try:
            await self._sleipnir_publisher.publish(event)
            logger.info(
                "Workflow gate help-needed emitted: gate=%s activation=%s",
                state.node_id,
                state.activation_id,
            )
        except Exception:
            logger.warning("Failed to emit workflow gate help-needed event", exc_info=True)

    async def _activate_workflow_gate(
        self,
        node: WorkflowGateNode,
        activation_id: str,
        triggering_event_type: str,
        *,
        summary: str = "",
    ) -> WorkflowGateState:
        key = (node.node_id, activation_id)
        existing_state_id = self._workflow_gate_state_ids_by_key.get(key)
        if existing_state_id is not None:
            existing_state = self._workflow_gate_states.get(existing_state_id)
            if existing_state is not None and existing_state.status == "pending":
                return existing_state

        attempt = self._workflow_gate_attempts.get(key, 0) + 1
        self._workflow_gate_attempts[key] = attempt
        now = datetime.now(UTC).isoformat()
        gate_id = f"{node.node_id}:{activation_id}:{attempt}"
        state = WorkflowGateState(
            id=gate_id,
            node_id=node.node_id,
            activation_id=activation_id,
            label=node.label,
            condition=node.condition,
            status="pending",
            mode=node.mode,
            pending_behavior=node.pending_behavior,
            instructions=node.instructions,
            auto_forward_after=node.auto_forward_after,
            requested_at=now,
            updated_at=now,
            triggered_by_event_type=triggering_event_type,
            approval_event_type=node.approval_event_type,
            changes_requested_event_type=node.changes_requested_event_type,
            attempt=attempt,
            summary=summary,
        )
        self._workflow_gate_states[gate_id] = state
        self._workflow_gate_state_ids_by_key[key] = gate_id
        self._trace_workflow_gate_spans[gate_id] = await self._start_trace_span(
            kind="wait.workflow_gate",
            name=state.label or state.node_id,
            parent_span_id=self._trace_session_span_id,
            actor_type="workflow",
            actor_id=state.node_id,
            actor_label=state.label or state.node_id,
            attributes={
                "gate_id": state.id,
                "activation_id": state.activation_id,
                "pending_behavior": state.pending_behavior,
                "triggered_by_event_type": state.triggered_by_event_type,
            },
        ) or self._trace_workflow_gate_spans.get(gate_id)
        await self._emit_pipeline_event(
            "workflow.gate.pending",
            {
                "gate_id": state.id,
                "gate_node_id": state.node_id,
                "workflow_activation_id": state.activation_id,
                "label": state.label,
                "condition": state.condition,
                "mode": state.mode,
                "pending_behavior": state.pending_behavior,
                "instructions": state.instructions,
                "auto_forward_after": state.auto_forward_after,
                "triggered_by_event_type": state.triggered_by_event_type,
                "status": state.status,
                "summary": state.summary,
            },
        )
        if state.pending_behavior == "help_needed":
            await self._emit_workflow_gate_help_needed_sleipnir_event(state)
            await self._report_activity_state(
                "idle",
                extra_metadata={
                    "help_needed": self._build_workflow_gate_help_needed_payload(state)
                },
            )
        return state

    async def resolve_workflow_gate(
        self,
        gate_id: str,
        decision: str,
        *,
        notes: str = "",
        source: str = "human",
    ) -> dict[str, Any]:
        state = self._workflow_gate_states.get(gate_id)
        if state is None:
            raise LookupError(f"Workflow gate not found: {gate_id}")
        if state.status != "pending":
            raise ValueError(f"Workflow gate {gate_id} is already resolved")
        normalized = decision.strip().upper()
        if normalized not in {"APPROVE", "CHANGES_REQUESTED"}:
            raise ValueError("decision must be APPROVE or CHANGES_REQUESTED")
        if self._mesh_adapter is None:
            raise RuntimeError("Workflow gate cannot resolve because mesh is not active")

        human_message = normalized if not notes.strip() else f"{normalized}\n\n{notes.strip()}"
        await self.handle_human_room_message(
            human_message,
            source=source,
            metadata={
                "workflow_gate_id": state.id,
                "workflow_gate_node_id": state.node_id,
                "workflow_activation_id": state.activation_id,
            },
            deliver_to_transport=False,
        )

        now = datetime.now(UTC)
        state.status = "approved" if normalized == "APPROVE" else "changes_requested"
        state.updated_at = now.isoformat()
        state.decision = normalized
        state.notes = notes.strip()
        state.source = source
        event_type = (
            state.approval_event_type
            if normalized == "APPROVE"
            else state.changes_requested_event_type
        )
        verdict = "approved" if normalized == "APPROVE" else "changes_requested"
        summary = (
            f"{state.label} approved by human reviewer."
            if normalized == "APPROVE"
            else f"{state.label} sent back with requested changes."
        )
        state.summary = summary
        payload: dict[str, Any] = {
            "event_type": event_type,
            "session_id": self.session_id,
            "persona": "workflow-gate",
            "peer_id": f"workflow-gate:{state.node_id}",
            "workflow_node_id": state.node_id,
            "workflow_activation_id": state.activation_id,
            "summary": summary,
            "verdict": verdict,
            "valid": True,
            "fields": {
                "verdict": verdict,
                "summary": summary,
                "approved": normalized == "APPROVE",
                "gate_id": state.id,
                "gate_node_id": state.node_id,
                "gate_label": state.label,
                "decision_source": source,
                "notes": state.notes,
            },
        }
        from ravn.domain.events import RavnEvent, RavnEventType

        event = RavnEvent(
            type=RavnEventType.OUTCOME,
            source=f"workflow-gate:{state.node_id}",
            payload=payload,
            timestamp=now,
            urgency=0.7,
            correlation_id=state.activation_id,
            session_id=self.session_id,
            root_correlation_id=state.activation_id,
        )
        await self._mesh_adapter.publish(event, event_type)
        await self._emit_pipeline_event("outcome", payload)
        gate_span_id = self._trace_workflow_gate_spans.pop(gate_id, None)
        await self._finish_trace_span(
            gate_span_id,
            status="completed",
            attributes={
                "decision": normalized,
                "source": source,
                "notes": state.notes,
                "event_type": event_type,
            },
        )
        return asdict(state)

    async def _report_peer_help_needed_activity(
        self,
        peer_id: str,
        frame: dict[str, Any],
    ) -> None:
        payload = self._build_peer_help_needed_payload(peer_id, frame)
        if payload is None:
            return
        await self._report_activity_state("idle", extra_metadata={"help_needed": payload})

    async def _maybe_activate_workflow_gate(
        self,
        frame: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        """Evaluate native gate nodes against bubbled-up workflow outcomes."""
        event_type = str(payload.get("event_type") or "").strip()
        if not event_type:
            return

        matching_nodes = self._workflow_gate_index.get(event_type) or []
        if not matching_nodes:
            return

        activation_id = self._workflow_activation_id_from_frame(frame)
        summary = str(payload.get("summary") or "").strip()

        for node in matching_nodes:
            key = (node.node_id, activation_id)
            slot = self._workflow_gate_slots.setdefault(key, set())
            slot.add(event_type)
            if not all(required in slot for required in node.event_types):
                continue
            self._workflow_gate_slots.pop(key, None)
            await self._activate_workflow_gate(
                node,
                activation_id,
                event_type,
                summary=summary,
            )

    async def _maybe_emit_workflow_terminal_outcome(
        self,
        peer_id: str,
        frame: dict[str, Any],
        payload: dict[str, Any],
    ) -> None:
        """Evaluate deterministic end nodes against bubbled-up peer outcomes."""
        event_type = str(payload.get("event_type") or "").strip()
        if not event_type or event_type == "ravn.task.completed":
            return

        matching_nodes = self._workflow_terminal_index.get(event_type) or []
        if not matching_nodes:
            return

        data = frame.get("data", {})
        if not isinstance(data, dict):
            return

        metadata = frame.get("metadata", {})
        activation_id = str(
            data.get("workflow_parent_event_id")
            or data.get("workflow_activation_id")
            or metadata.get("task_id")
            or frame.get("root_correlation_id")
            or self.session_id
        ).strip()
        if not activation_id:
            return

        for node in matching_nodes:
            key = (node.node_id, activation_id)
            slot = self._workflow_terminal_slots.setdefault(key, {})
            slot[event_type] = dict(payload)

            if not all(required in slot for required in node.event_types):
                continue

            collected = [slot[required] for required in node.event_types if required in slot]
            self._workflow_terminal_slots.pop(key, None)

            if not _workflow_join_satisfied(node.join_mode, collected):
                logger.info(
                    "workflow runtime: terminal node %s rejected activation=%s outcomes=%s",
                    node.node_id,
                    activation_id,
                    [outcome.get("event_type", "") for outcome in collected],
                )
                self._workflow_terminal_emitted.discard(key)
                continue

            self._refresh_git_workspace_artifacts()
            if not _workflow_terminal_requirements_satisfied(
                node,
                self._artifacts,
                git_checkpoint=self._git_workspace_checkpoint,
            ):
                logger.info(
                    "workflow runtime: terminal node %s waiting for durable checkpoint "
                    "(commit=%d push=%d activation=%s)",
                    node.node_id,
                    self._artifacts.git_commit_count,
                    self._artifacts.git_push_count,
                    activation_id,
                )
                self._workflow_terminal_emitted.discard(key)
                continue

            if key in self._workflow_terminal_emitted:
                continue
            self._workflow_terminal_emitted.add(key)
            await self._emit_workflow_terminal_completion(peer_id, node, activation_id, collected)

    async def _emit_workflow_terminal_completion(
        self,
        _peer_id: str,
        node: WorkflowTerminalNode,
        activation_id: str,
        outcomes: list[dict[str, Any]],
    ) -> None:
        """Emit the deterministic terminal completion for a satisfied end node."""
        fields = _merge_workflow_terminal_outcomes(outcomes)
        terminal_peer_id = f"workflow-stop:{node.node_id}"
        payload: dict[str, Any] = {
            "peer_id": terminal_peer_id,
            "persona": "workflow-runtime",
            "event_type": node.completion_event_type,
            "canonical_event_type": node.completion_event_type,
            "workflow_node_id": node.node_id,
            "workflow_activation_id": activation_id,
            "fields": fields,
            "valid": True,
            "verdict": fields.get("verdict", "approve"),
            "summary": fields.get("summary", ""),
        }
        files_changed = fields.get("files_changed")
        if isinstance(files_changed, list) and files_changed:
            payload["files_changed"] = files_changed
        if "tests_passing" in fields:
            payload["tests_passing"] = fields["tests_passing"]
        if "scope_adherence" in fields:
            payload["scope_adherence"] = fields["scope_adherence"]

        await self._emit_pipeline_event("outcome", payload)
        await self._finish_trace_span(
            self._trace_workflow_span_id,
            status="completed",
            attributes={
                "workflow_node_id": node.node_id,
                "workflow_activation_id": activation_id,
                "completion_event_type": node.completion_event_type,
                "fields": fields,
            },
        )
        self._trace_workflow_span_id = None
        logger.info(
            "workflow runtime: terminal node %s emitted %s activation=%s",
            node.node_id,
            node.completion_event_type,
            activation_id,
        )

        frame = {
            "metadata": {"event_type": node.completion_event_type},
            "data": {
                "event_type": node.completion_event_type,
                "fields": fields,
                "valid": True,
            },
        }
        await self._maybe_report_flock_completion(terminal_peer_id, frame)

    async def _maybe_report_flock_completion(
        self,
        peer_id: str,
        frame: dict[str, Any],
    ) -> None:
        """Report authoritative flock completion through Volundr activity metadata.

        Local multi-peer workflows already flow through Skuld -> Volundr SSE -> Ting.
        Use that existing path for ravn_flock completion instead of relying on an
        out-of-process Sleipnir transport during co-hosted development runs.
        """
        if self._flock_completion_reported or not self._is_room_only_workflow_session():
            return
        if self._room_bridge is None:
            return

        participant = self._room_bridge.participants.get(peer_id)
        persona = (participant.persona if participant is not None else "").strip()

        metadata = frame.get("metadata", {})
        outcome_event_type = str(metadata.get("event_type") or "")
        is_workflow_terminal = str(peer_id).startswith("workflow-stop:")
        if not is_workflow_terminal and outcome_event_type != "ravn.task.completed":
            return

        data = frame.get("data", {})
        if isinstance(data, str):
            return

        fields = data.get("fields", data)
        if not isinstance(fields, dict) or not fields:
            return
        structured_outcome = (
            fields.get("outcome") if isinstance(fields.get("outcome"), dict) else fields
        )
        if not isinstance(structured_outcome, dict) or not structured_outcome:
            return

        extra_metadata: dict[str, Any] = {
            "completion_source": "ravn_flock",
            "completion_event_type": outcome_event_type,
            "completion_persona": persona,
            "completion_peer_id": peer_id,
            "structured_outcome": structured_outcome,
            "outcome_valid": bool(data.get("valid", True)),
        }
        files_changed = structured_outcome.get("files_changed") or fields.get("files_changed")
        if isinstance(files_changed, list) and files_changed:
            extra_metadata["files_changed"] = files_changed

        self._flock_completion_reported = True
        await self._report_activity_state("idle", extra_metadata=extra_metadata)

    async def _peer_watchdog_loop(self) -> None:
        """Warn in chat when a flock peer accepted work but goes quiet."""
        try:
            while True:
                await asyncio.sleep(self._settings.peer_watchdog.poll_seconds)
                await self._check_peer_watchdog_once()
        except asyncio.CancelledError:
            return

    async def _check_peer_watchdog_once(self) -> None:
        """Run one silence-watchdog pass for active flock peers."""
        if not self._settings.peer_watchdog.enabled:
            return
        now = time.monotonic()
        for peer_id, watch in list(self._peer_watches.items()):
            threshold = (
                self._settings.peer_watchdog.tool_silence_seconds
                if watch.last_status == "tool_executing"
                else self._settings.peer_watchdog.silence_seconds
            )
            silence_seconds = now - watch.last_progress_at
            if silence_seconds < threshold or watch.warned:
                continue
            watch.warned = True
            await self._emit_peer_silence_warning(watch, int(silence_seconds))

    async def _emit_peer_silence_warning(
        self,
        watch: PeerWatchState,
        silence_seconds: int,
    ) -> None:
        """Surface a stalled-peer warning in chat and peer status."""
        if self._room_bridge is None:
            return

        participant = self._room_bridge.participants.get(watch.peer_id)
        peer_name = (
            (participant.display_name or participant.persona or watch.peer_id)
            if participant is not None
            else watch.peer_id
        )
        status_hint = (
            "while waiting on a tool result"
            if watch.last_status == "tool_executing"
            else "after accepting work"
        )
        content = (
            f"Skuld watchdog: `{peer_name}` has shown no visible progress for "
            f"{silence_seconds}s {status_hint} on `{watch.title}`. "
            "The agent may be blocked on an upstream LLM/backend failure or a stalled tool."
        )
        await self._room_bridge.broadcast_cli_activity(
            watch.peer_id,
            "blocked",
            f"silent for {silence_seconds}s",
        )
        observer_peer_id = self._observer_peer_id()
        if observer_peer_id:
            await self._room_bridge.broadcast_cli_message(
                observer_peer_id,
                content,
                is_error=True,
            )
        await self._report_timeline_event(
            {
                "t": self._artifacts.duration_seconds,
                "type": "error",
                "label": content[:120],
            }
        )

    @staticmethod
    def _permission_request_id(data: dict[str, Any]) -> str:
        request_id = data.get("request_id")
        return str(request_id or "").strip()

    @staticmethod
    def _permission_input(data: dict[str, Any]) -> dict[str, Any]:
        input_payload = data.get("input")
        return input_payload if isinstance(input_payload, dict) else {}

    @classmethod
    def _permission_command(cls, data: dict[str, Any]) -> str | None:
        direct_command = data.get("command")
        if isinstance(direct_command, str) and direct_command.strip():
            return direct_command.strip()

        input_payload = cls._permission_input(data)
        for key in ("command", "cmd", "shell_command"):
            value = input_payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _permission_auto_approval_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        command = self._permission_command(data)
        description = data.get("description")
        if not isinstance(description, str) or not description.strip():
            description = command or ""

        tool_name = data.get("tool_name") or data.get("tool")
        if not isinstance(tool_name, str):
            tool_name = ""

        return {
            "request_id": self._permission_request_id(data),
            "tool_name": tool_name,
            "description": description,
            "command": command,
            "input": self._permission_input(data),
        }

    async def _evaluate_permission_auto_approval(
        self,
        data: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Ask Forge whether this pending permission may be auto-approved."""
        if not self.volundr_api_url:
            return None

        try:
            client = await self._get_http_client()
            response = await client.post(
                FORGE_PERMISSION_AUTO_APPROVAL_EVALUATE_PATH.format(
                    session_id=self.session_id,
                ),
                json=self._permission_auto_approval_payload(data),
            )
            if response.status_code >= 300:
                logger.warning(
                    "Permission auto-approval policy check failed (%d): %s",
                    response.status_code,
                    response.text[:200],
                )
                return None
            payload = response.json()
        except Exception:
            logger.warning("Permission auto-approval policy check failed", exc_info=True)
            return None

        if not isinstance(payload, dict):
            return None
        return payload

    @staticmethod
    def _decision_delay_seconds(decision: dict[str, Any]) -> float:
        raw_delay = decision.get("delay_seconds", 0)
        try:
            delay = float(raw_delay)
        except (TypeError, ValueError):
            delay = 0.0
        return max(0.0, delay)

    def _clear_pending_permission_request(
        self,
        request_id: str,
        *,
        cancel_auto_approval: bool = True,
    ) -> None:
        self._pending_permission_requests.pop(request_id, None)
        if not cancel_auto_approval:
            return

        task = self._permission_auto_approval_tasks.pop(request_id, None)
        if task is not None and task is not asyncio.current_task():
            task.cancel()

    def _track_pending_permission_request(self, data: dict[str, Any]) -> None:
        request_id = self._permission_request_id(data)
        if not request_id:
            return

        self._pending_permission_requests[request_id] = dict(data)

        if not self.volundr_api_url:
            return

        existing_task = self._permission_auto_approval_tasks.pop(request_id, None)
        if existing_task is not None:
            existing_task.cancel()

        task = asyncio.create_task(self._auto_approve_permission_request(request_id))
        self._permission_auto_approval_tasks[request_id] = task

        def _cleanup(done: asyncio.Task[None]) -> None:
            current = self._permission_auto_approval_tasks.get(request_id)
            if current is done:
                self._permission_auto_approval_tasks.pop(request_id, None)

        task.add_done_callback(_cleanup)

    async def _send_permission_control_response(
        self,
        request_id: str,
        response: dict[str, Any],
        *,
        auto_approved: bool,
    ) -> None:
        if not self._transport:
            logger.warning("Cannot respond to permission request; transport is not initialized")
            return

        await self._transport.send_control_response(request_id, response)
        self._clear_pending_permission_request(
            request_id,
            cancel_auto_approval=not auto_approved,
        )
        await self._channels.broadcast(
            {
                "type": "permission_resolved",
                "request_id": request_id,
                "behavior": response.get("behavior", "deny"),
                "auto_approved": auto_approved,
            }
        )

    async def _auto_approve_permission_request(self, request_id: str) -> None:
        initial_request = self._pending_permission_requests.get(request_id)
        if initial_request is None:
            return

        decision = await self._evaluate_permission_auto_approval(initial_request)
        if not decision or decision.get("can_auto_approve") is not True:
            return

        delay_seconds = self._decision_delay_seconds(decision)
        await self._channels.broadcast(
            {
                "type": "permission_auto_approval_scheduled",
                "request_id": request_id,
                "decision": decision,
            }
        )
        await asyncio.sleep(delay_seconds)

        latest_request = self._pending_permission_requests.get(request_id)
        if latest_request is None:
            return

        # Re-check immediately before responding so policy changes or denylist
        # additions win over a stale countdown.
        latest_decision = await self._evaluate_permission_auto_approval(latest_request)
        if not latest_decision or latest_decision.get("can_auto_approve") is not True:
            await self._channels.broadcast(
                {
                    "type": "permission_auto_approval_cancelled",
                    "request_id": request_id,
                    "decision": latest_decision,
                }
            )
            return

        logger.info("Auto-approving permission request %s", _sanitize_log(request_id))
        await self._send_permission_control_response(
            request_id,
            {"behavior": "allow", "updatedInput": {}},
            auto_approved=True,
        )

    async def _handle_cli_event(self, data: dict) -> None:
        """Forward a CLI event to all connected channels."""
        event_type = data.get("type", "unknown")

        # Durably capture EVERY frame first — before any channel/broadcast logic —
        # so agent output is never lost when no client is attached.
        self._enqueue_event_log(data)

        if event_type == "remote_control":
            # A remote-control transport reporting its pairing URL. Surface it as
            # a real assistant turn (conversation history + durable-log replay +
            # live channels) so every client shows the link to hand off to the
            # native app.
            url = str(data.get("url") or "").strip()
            if url:
                await self._surface_remote_control_url(url)
            return

        if event_type == "control_request":
            self._track_pending_permission_request(data)

        tool_result_only_user_event = event_type == "user" and self._is_tool_result_only_user_event(
            data
        )
        suppress_channel_broadcast = (
            event_type in {"user", "assistant", "content_block_delta", "result"}
            and self._pending_explicit_human_response_count > 0
        )
        num_channels = self._channels.count
        logger.debug(
            "_handle_cli_event: forwarding to %d channel(s) suppress=%s",
            num_channels,
            suppress_channel_broadcast,
        )

        if num_channels == 0:
            logger.warning("_handle_cli_event: no channels to forward event")

        if not suppress_channel_broadcast:
            await self._channels.broadcast(data)

        # Record user messages that arrive via the transport (e.g. the
        # initial prompt flushed as a pending message) into conversation
        # history so late-joining browsers see them.
        if event_type == "user" and not tool_result_only_user_event:
            # A user event means the previous assistant turn is complete.
            # Flush any pending assistant content as a saved turn.
            self._flush_pending_assistant_turn()
            await self._finish_pending_assistant_tool_trace_spans(
                status="completed",
                attributes={"reason": "new_user_event"},
            )
            if self._trace_assistant_span_id is not None:
                await self._finish_trace_span(
                    self._trace_assistant_span_id,
                    status="completed",
                    attributes={"reason": "new_user_event"},
                )
                self._trace_assistant_span_id = None

            user_content = ""
            msg = data.get("message", {})
            if isinstance(msg, dict):
                user_content = msg.get("content", "")
            if isinstance(user_content, str) and user_content:
                if self._pending_explicit_human_messages:
                    raw_pending, outbound_pending = self._pending_explicit_human_messages[0]
                    if user_content in {raw_pending, outbound_pending}:
                        self._pending_explicit_human_messages.pop(0)
                        user_content = ""
                if user_content:
                    self._append_turn(
                        ConversationTurn(
                            id=str(uuid.uuid4()),
                            role="user",
                            content=user_content,
                        )
                    )

        # When CLI sends system/init, broadcast available commands to browsers
        if event_type == "system" and data.get("subtype") == "init":
            slash_commands = data.get("slash_commands", [])
            skills = data.get("skills", [])
            if slash_commands or skills:
                await self._channels.broadcast(
                    {
                        "type": "available_commands",
                        "slash_commands": slash_commands,
                        "skills": skills,
                    }
                )

        # Report activity state transitions to Volundr
        if event_type == "assistant":
            if self._trace_assistant_span_id is None:
                assistant_label = (
                    self._settings.mesh.persona
                    if getattr(self._settings, "mesh", None) is not None
                    else None
                ) or self._settings.session.name
                self._trace_assistant_span_id = await self._start_trace_span(
                    kind="turn.assistant",
                    name="assistant turn",
                    parent_span_id=self._trace_session_span_id,
                    actor_type="assistant",
                    actor_id=self._observer_peer_id() or self.session_id,
                    actor_label=assistant_label,
                    attributes={"model": self.model},
                )
            await self._start_assistant_tool_trace_spans(data)
            asyncio.create_task(self._report_activity_state("active"))
            # Emit room activity for CLI participant so room UI shows "thinking"
            if self._room_bridge is not None and self._mesh_adapter is not None:
                await self._room_bridge.broadcast_cli_activity(
                    self._mesh_adapter.peer_id, "thinking"
                )
        elif tool_result_only_user_event:
            await self._finish_assistant_tool_trace_spans_from_user_event(data)
            # Persist tool_result blocks onto the open assistant turn so the saved
            # conversation carries tool OUTPUT (not just the call) for read-back.
            tr_msg = data.get("message", {})
            tr_blocks = tr_msg.get("content", []) if isinstance(tr_msg, dict) else []
            for tr_block in tr_blocks or []:
                if (
                    isinstance(tr_block, dict)
                    and tr_block.get("type") == "tool_result"
                    and tr_block.get("tool_use_id")
                ):
                    self._pending_assistant_parts.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tr_block.get("tool_use_id"),
                            "content": tr_block.get("content"),
                            "is_error": bool(tr_block.get("is_error")),
                        }
                    )
        elif event_type == "result":
            asyncio.create_task(self._report_activity_state("idle"))
            if self._pending_explicit_human_response_count == 0:
                asyncio.create_task(self._on_result_publish_mesh())

        # Track conversation from assistant messages.
        # The SDK WebSocket protocol sends complete messages as type=assistant
        # with content blocks already resolved (not as streaming deltas).
        # We also handle the HTTP streaming format (content_block_delta, result)
        # for backward compatibility.
        if event_type == "assistant":
            # Extract content and ACCUMULATE parts across the messages of one turn
            # (a tool_use message, then the final text), so the SAVED conversation
            # turn carries tool calls + reasoning — not just text — and reloads with
            # its tool cards. Parts are reset on flush, not per message.
            message = data.get("message", {})
            content_blocks = message.get("content", [])
            if isinstance(content_blocks, list) and content_blocks:
                text_parts = []
                for block in content_blocks:
                    if not isinstance(block, dict):
                        continue
                    btype = block.get("type")
                    if btype == "text" and block.get("text"):
                        text_parts.append(block["text"])
                        self._pending_assistant_parts.append(
                            {"type": "text", "text": block["text"]}
                        )
                    elif btype == "thinking" and block.get("thinking"):
                        # Keep last 500 chars per reasoning block as a summary.
                        self._pending_assistant_parts.append(
                            {"type": "reasoning", "text": str(block["thinking"])[-500:]}
                        )
                    elif btype == "tool_use" and block.get("id"):
                        self._pending_assistant_parts.append(
                            {
                                "type": "tool_use",
                                "id": block.get("id"),
                                "name": block.get("name"),
                                "input": block.get("input") or {},
                            }
                        )
                text_content = "\n".join(text_parts)
                if text_content:
                    self._pending_assistant_content = (
                        f"{self._pending_assistant_content}\n{text_content}"
                        if self._pending_assistant_content
                        else text_content
                    )

        # HTTP streaming format: accumulate deltas
        if event_type == "content_block_delta":
            delta = data.get("delta", {})
            delta_type = delta.get("type", "")
            if delta_type == "thinking_delta":
                thinking = delta.get("thinking", "")
                if thinking:
                    self._pending_reasoning_text += thinking
            else:
                text = delta.get("text", "")
                if text:
                    self._pending_assistant_content += text

        # Accumulate artifacts from assistant tool_use events
        if event_type == "assistant":
            tool_events = self._artifacts.record_tool_use(data)
            if tool_events:
                asyncio.create_task(self._report_activity_state("tool_executing"))
            # Enrich tool events with tool_result data (exit codes, git info)
            self._artifacts.enrich_from_tool_result(data, tool_events)
            for tool_ev in tool_events:
                # Clean up internal fields before reporting
                tool_ev.pop("_pending_git", None)
                tool_ev["t"] = self._artifacts.duration_seconds
                self._artifacts.observe_tool_event(tool_ev)
                asyncio.create_task(self._report_timeline_event(tool_ev))

                # Emit to event pipeline
                pipeline_type = self._classify_pipeline_event(tool_ev)
                asyncio.create_task(
                    self._emit_pipeline_event(
                        pipeline_type,
                        tool_ev,
                    )
                )

        # Capture error events
        if event_type == "error":
            error_msg = (
                data.get("error", {}).get("message", "")
                if isinstance(data.get("error"), dict)
                else data.get("content", str(data.get("error", "Unknown error")))
            )
            visible_error = str(error_msg) or "Unknown error"
            if self._pending_assistant_content or self._pending_assistant_parts:
                self._flush_pending_assistant_turn(
                    metadata={"status": "error", "messageType": "error"}
                )
            self._append_turn(
                ConversationTurn(
                    id=str(uuid.uuid4()),
                    role="assistant",
                    content=visible_error,
                    parts=[{"type": "text", "text": visible_error}],
                    metadata={"status": "error", "messageType": "error"},
                )
            )
            asyncio.create_task(
                self._report_timeline_event(
                    {
                        "t": self._artifacts.duration_seconds,
                        "type": "error",
                        "label": visible_error[:120],
                    }
                )
            )

        # Report usage on result events and track turn count
        if event_type == "result":
            self._artifacts.record_result()
            asyncio.create_task(self._report_usage(data))
            explicit_room_reply = self._pending_explicit_human_response_count > 0

            # Flush pending assistant turn (HTTP streaming format sends result)
            if not self._pending_assistant_content:
                # Try to extract from result event itself
                self._pending_assistant_content = data.get("result", "")
                if not self._pending_assistant_content:
                    for block in data.get("content", []):
                        if isinstance(block, dict) and block.get("type") == "text":
                            self._pending_assistant_content = block.get("text", "")
                            break
            # Build metadata from result event
            model_usage_for_turn = data.get("modelUsage", {})
            result_cost = None
            result_model = None
            for model_id, usage in model_usage_for_turn.items():
                result_model = model_id
                if usage.get("costUSD") is not None:
                    result_cost = (result_cost or 0) + usage["costUSD"]

            # Capture content before flush clears it
            content = self._pending_assistant_content or data.get("result", "")
            if explicit_room_reply:
                self._pending_assistant_content = ""
                self._pending_assistant_parts = []
                self._pending_reasoning_text = ""
            else:
                self._flush_pending_assistant_turn(
                    metadata={
                        "usage": model_usage_for_turn,
                        "cost": result_cost,
                        "model": result_model,
                    }
                )

            # Emit CLI turn as room_message so it shows participant color
            if self._room_bridge is not None and self._mesh_adapter is not None and content:
                await self._room_bridge.broadcast_cli_message(self._mesh_adapter.peer_id, content)
                await self._room_bridge.broadcast_cli_activity(self._mesh_adapter.peer_id, "idle")
            if explicit_room_reply and self._pending_explicit_human_response_count > 0:
                self._pending_explicit_human_response_count -= 1

            first_line = ""
            if content:
                for line in content.strip().splitlines():
                    stripped = line.strip()
                    if stripped:
                        first_line = stripped[:80]
                        break
            message_label = first_line or f"Turn {self._artifacts.turn_count}"

            # Report message timeline event with total tokens
            model_usage = data.get("modelUsage", {})
            total_tokens = 0
            total_input = 0
            total_output = 0
            result_model = None
            result_cost = None
            for model_id, usage in model_usage.items():
                result_model = model_id
                inp = (
                    usage.get("inputTokens", 0)
                    + usage.get("cacheReadInputTokens", 0)
                    + usage.get("cacheCreationInputTokens", 0)
                )
                out = usage.get("outputTokens", 0)
                total_input += inp
                total_output += out
                total_tokens += inp + out
                if usage.get("costUSD") is not None:
                    result_cost = (result_cost or 0) + usage["costUSD"]

            if total_tokens > 0:
                self._artifacts.total_tokens += total_tokens
                asyncio.create_task(
                    self._report_timeline_event(
                        {
                            "t": self._artifacts.duration_seconds,
                            "type": "message",
                            "label": message_label,
                            "tokens": total_tokens,
                        }
                    )
                )

                # Emit message_assistant to event pipeline
                asyncio.create_task(
                    self._emit_pipeline_event(
                        "message_assistant",
                        {
                            "content_length": len(content),
                            "content_preview": content[:200],
                            "finish_reason": data.get("stop_reason", "end_turn"),
                            "turn": self._artifacts.turn_count,
                        },
                        tokens_in=total_input,
                        tokens_out=total_output,
                        cost=result_cost,
                        model=result_model,
                    )
                )

                # Emit token_usage to event pipeline
                asyncio.create_task(
                    self._emit_pipeline_event(
                        "token_usage",
                        {
                            "provider": "cloud",
                            "model": result_model or self.model,
                            "tokens_in": total_input,
                            "tokens_out": total_output,
                        },
                        tokens_in=total_input,
                        tokens_out=total_output,
                        cost=result_cost,
                        model=result_model or self.model,
                    )
                )
            if self._trace_assistant_span_id is not None:
                await self._finish_pending_assistant_tool_trace_spans(
                    status="completed",
                    attributes={"reason": "assistant_turn_completed"},
                )
                await self._finish_trace_span(
                    self._trace_assistant_span_id,
                    status="completed",
                    attributes={
                        "finish_reason": data.get("stop_reason", "end_turn"),
                        "tokens_in": total_input,
                        "tokens_out": total_output,
                        "model": result_model or self.model,
                    },
                )
                self._trace_assistant_span_id = None

    # Maps control message types to the TransportCapabilities field that
    # must be True for the control to be forwarded.
    _CONTROL_CAPABILITY_MAP: dict[str, str] = {
        "interrupt": "interrupt",
        "steer_active_turn": "steer",
        "set_model": "set_model",
        "set_max_thinking_tokens": "set_thinking_tokens",
        "set_permission_mode": "set_permission_mode",
        "rewind_files": "rewind_files",
        "mcp_set_servers": "mcp_set_servers",
        "terminal_input": "terminal_input",
        "terminal_key": "terminal_keys",
        "terminal_resize": "terminal_resize",
        "slash_command": "slash_commands",
        "discover_slash_commands": "slash_commands",
    }

    async def _dispatch_browser_message(
        self,
        data: dict,
        sender_ws: WebSocket | None = None,
    ) -> None:
        """Route a browser WebSocket message to the appropriate handler."""
        if not self._transport:
            logger.warning("_dispatch_browser_message: transport is None, dropping message")
            return

        msg_type = data.get("type")
        logger.info(
            "_dispatch_browser_message: type=%s, transport_alive=%s",
            _sanitize_log(msg_type),
            self._transport.is_alive,
        )

        # Guard: reject control messages the transport does not support.
        cap_field = self._CONTROL_CAPABILITY_MAP.get(msg_type or "")
        if cap_field and not getattr(self._transport.capabilities, cap_field):
            error_msg = f"{msg_type} not supported by this transport"
            logger.warning("_dispatch_browser_message: %s", _sanitize_log(error_msg))
            if sender_ws:
                await sender_ws.send_json({"type": "error", "content": error_msg})
            return

        match msg_type:
            # Phase 2: permission response from browser
            case "permission_response":
                request_id = data.get("request_id", "")
                behavior = data.get("behavior", "deny")
                response = {
                    "behavior": behavior,
                    "updatedInput": data.get("updated_input", {}),
                }
                if data.get("updated_permissions"):
                    response["updatedPermissions"] = data["updated_permissions"]
                await self._send_permission_control_response(
                    str(request_id),
                    response,
                    auto_approved=False,
                )

            # AskUserQuestion: a human answered a question the agent asked.
            # Resolves the blocking can_use_tool future in SDKTransport.
            case "ask_user_answer":
                await self._transport.send_control(
                    "ask_user_answer",
                    request_id=data.get("request_id", ""),
                    answers=data.get("answers", []),
                )

            # Phase 3: interrupt current turn
            case "interrupt":
                await self._transport.send_control("interrupt")

            # Phase 3: steer the current turn with additional user guidance
            case "steer_active_turn":
                content = str(data.get("content") or "").strip()
                if content:
                    await self._transport.send_control("steer", content=content)

            # Phase 3: change model mid-session
            case "set_model":
                model = data.get("model", "")
                if model:
                    await self._transport.send_control("set_model", model=model)

            # Phase 3: change thinking budget
            case "set_max_thinking_tokens":
                tokens = data.get("max_thinking_tokens", 0)
                await self._transport.send_control(
                    "set_max_thinking_tokens",
                    max_thinking_tokens=tokens,
                )

            # Per-channel filter: hide/show tool_use + tool_result blocks
            case "set_internal_visibility":
                visible = bool(data.get("visible", False))
                if sender_ws is not None:
                    for ch in self._channels.channels:
                        if (
                            isinstance(ch, WebSocketChannel)
                            and getattr(ch, "ws", None) is sender_ws
                        ):
                            ch.set_show_internal(visible)
                            break

            # Phase 3: change permission mode at runtime
            case "set_permission_mode":
                mode = data.get("mode", "")
                if mode:
                    await self._transport.send_control(
                        "set_permission_mode",
                        permissionMode=mode,
                    )

            # Phase 3: rewind file changes to checkpoint
            case "rewind_files":
                await self._transport.send_control("rewind_files")

            # Phase 3: inject or reconfigure MCP servers
            case "mcp_set_servers":
                servers = data.get("servers", [])
                await self._transport.send_control(
                    "mcp_set_servers",
                    servers=servers,
                )

            # Interactive terminal transports: raw input, key presses, and
            # resize events. These are intentionally transport controls, not
            # chat turns, so workflows can drive slash menus/history/editing.
            case "terminal_input":
                await self._transport.send_control(
                    "terminal_input",
                    data=data.get("data", data.get("text", "")),
                    enter=bool(data.get("enter", False)),
                    pane_id=data.get("pane_id", ""),
                )

            case "terminal_key":
                await self._transport.send_control(
                    "terminal_key",
                    key=data.get("key", ""),
                    keys=data.get("keys", []),
                    pane_id=data.get("pane_id", ""),
                )

            case "terminal_resize":
                await self._transport.send_control(
                    "terminal_resize",
                    cols=data.get("cols", data.get("columns", 0)),
                    rows=data.get("rows", 0),
                    pane_id=data.get("pane_id", ""),
                )

            case "slash_command":
                await self._transport.send_control(
                    "slash_command",
                    command=data.get("command", ""),
                    arguments=data.get("arguments", data.get("args", "")),
                    pane_id=data.get("pane_id", ""),
                )

            case "discover_slash_commands":
                commands = await self.discover_slash_commands(
                    refresh=bool(data.get("refresh", True))
                )
                if sender_ws is not None:
                    await sender_ws.send_json(
                        {
                            "type": "slash_commands",
                            "commands": commands,
                            "count": len(commands),
                        }
                    )

            # Room: forward a directed message to a specific Ravn participant
            case "directed_message":
                if self._room_bridge is None:
                    logger.warning("directed_message received but room mode is disabled")
                    if sender_ws:
                        await sender_ws.send_json(
                            {"type": "error", "content": "Room mode is not enabled"}
                        )
                    return
                target = data.get("targetPeerId", "")
                content = data.get("content", "")
                if not target or not content:
                    return
                await self._room_bridge.route_directed_message(
                    target,
                    content,
                    metadata=data.get("metadata"),
                )

            # Default: treat as user message (backward compat with {"content": "..."})
            case _:
                message = data.get("content", "")
                if not message:
                    return

                if self._is_room_only_workflow_session():
                    error_msg = (
                        "Direct chat is disabled for flock workflow sessions. "
                        "Target a mesh peer instead."
                    )
                    logger.info("_dispatch_browser_message: %s", error_msg)
                    if sender_ws:
                        await sender_ws.send_json({"type": "error", "content": error_msg})
                    return

                # Record user turn in conversation history
                content_str = _normalize_browser_message_content(message)
                if not content_str:
                    return
                msg_id = str(uuid.uuid4())
                request_id = data.get("request_id")
                request_id = request_id if isinstance(request_id, str) and request_id else None
                self._append_turn(
                    ConversationTurn(
                        id=msg_id,
                        role="user",
                        content=content_str,
                    )
                )
                # Mirror the human turn into the durable event log so log-only
                # transcript replay (web/iOS) includes it.
                self._enqueue_human_turn_event(content_str, msg_id)
                now = datetime.now(UTC)
                await self._complete_trace_span(
                    kind="turn.user",
                    name=content_str[:120] or "user turn",
                    parent_span_id=self._trace_session_span_id,
                    actor_type="user",
                    actor_id=request_id or msg_id,
                    actor_label="user",
                    attributes={"source": "browser"},
                    started_at=now,
                    ended_at=now,
                )

                # Echo back to all browsers so the message is confirmed
                # immediately (rendering as a user turn) — the actual transport
                # delivery happens asynchronously below.
                await self._channels.broadcast(
                    {
                        "type": "user_confirmed",
                        "id": msg_id,
                        "content": content_str,
                        "request_id": request_id,
                    }
                )

                if (
                    getattr(self._transport.capabilities, "steer", False) is True
                    and getattr(self._transport, "is_turn_active", False) is True
                ):
                    asyncio.create_task(
                        self._safe_transport_control(
                            self._transport,
                            "redirect",
                            content=content_str,
                        ),
                        name=f"transport-redirect-{msg_id}",
                    )
                    return

                # send_message holds a per-instance lock for the entire
                # turn. Awaiting inline blocks the WS receive loop, so the
                # user can't queue a follow-up while Claude is running.
                # Fire-and-forget: the transport's lock still serialises
                # invocations, so ordering is preserved; we just don't pin
                # the WS handler waiting for a turn that may take minutes.
                asyncio.create_task(
                    self._safe_transport_send(self._transport, content_str),
                    name=f"transport-send-{msg_id}",
                )

    async def _safe_transport_control(
        self,
        transport: object,
        subtype: str,
        **kwargs: object,
    ) -> None:
        """Wrap transport.send_control so background failures surface to the UI."""
        try:
            await transport.send_control(subtype, **kwargs)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.exception("Transport send_control failed in background task")
            try:
                await self._channels.broadcast({"type": "error", "content": str(exc)})
            except Exception:
                logger.debug("Failed to broadcast transport control error", exc_info=True)

    def _retrieval_reflex_injector(self) -> Any:
        """Lazily build the retrieval reflex injector from reflex config (NIU-1059)."""
        if self._reflex_initialised:
            return self._reflex_injector
        self._reflex_initialised = True
        from ravn.reflex import build_reflex_injector  # noqa: PLC0415

        self._reflex_injector = build_reflex_injector(self._settings)
        return self._reflex_injector

    async def _apply_retrieval_reflex(self, message: str) -> str:
        """Prefix known Mímir entity pointers onto a forwarded user message.

        Fail-open: any reflex failure logs an ERROR and returns the message
        unchanged — the reflex must never crash or block a turn.
        """
        try:
            injector = self._retrieval_reflex_injector()
            if injector is None:
                return message
            return await injector.apply(message, self.session_id)
        except Exception:
            logger.error(
                "retrieval reflex failed for session %s — forwarding message without pointers",
                self.session_id,
                exc_info=True,
            )
            return message

    async def _safe_transport_send(self, transport: object, message: str) -> None:
        """Wrap transport.send_message so background-task failures are surfaced.

        Used by the fire-and-forget path in ``_dispatch_browser_message`` —
        without this wrapper, a transport error would only show up as an
        ``asyncio.create_task`` exception. Any error here is logged AND
        broadcast to all currently connected channels so the user sees
        something in the chat UI rather than a silent stall.
        """
        try:
            outbound = await self._apply_retrieval_reflex(message)
            await transport.send_message(outbound)  # type: ignore[attr-defined]
        except Exception as exc:
            logger.exception("Transport send_message failed in background task")
            try:
                await self._channels.broadcast({"type": "error", "content": str(exc)})
            except Exception:
                logger.debug("Failed to broadcast transport error to channels", exc_info=True)

    async def handle_claude_hook(self, payload: dict[str, Any]) -> None:
        """Ingest a Claude Code hook payload into the normal event pipeline.

        Interactive tmux sessions can configure Claude Code HTTP hooks to POST
        here. The payload schema is owned by Claude Code, so we keep the raw
        body intact and add only stable routing fields.
        """
        transport_hook = getattr(self._transport, "handle_claude_hook", None)
        if callable(transport_hook):
            try:
                handled = await transport_hook(payload)
            except Exception:
                logger.warning("Transport Claude hook handler failed", exc_info=True)
            else:
                if handled:
                    return

        event_name = (
            payload.get("hook_event_name")
            or payload.get("hookEventName")
            or payload.get("event")
            or payload.get("hook_event")
            or "unknown"
        )
        await self._handle_cli_event(
            {
                "type": "claude_hook",
                "event_type": "claude.hook",
                "hook_event_name": str(event_name),
                "payload": payload,
            }
        )

    async def discover_slash_commands(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        """Return slash commands available through the current transport."""
        if self._transport is None:
            return []

        commands = await self._transport.discover_slash_commands(refresh=refresh)
        if commands:
            return self._normalize_slash_commands(commands)

        raw_commands = getattr(self._transport, "slash_commands", [])
        if callable(raw_commands):
            raw_commands = raw_commands()
        return self._normalize_slash_commands(raw_commands)

    @staticmethod
    def _normalize_slash_commands(raw_commands: Any) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        if not isinstance(raw_commands, list):
            return normalized
        for item in raw_commands:
            if isinstance(item, str):
                raw_name = item
                description = ""
                kind = "command"
                source = "transport"
            elif isinstance(item, dict):
                raw_name = str(item.get("name") or item.get("command") or "")
                description = str(item.get("description") or "")
                kind = str(item.get("kind") or "command")
                source = str(item.get("source") or "transport")
            else:
                continue
            raw_name = raw_name.strip()
            if not raw_name:
                continue
            name = raw_name if raw_name.startswith("/") else f"/{raw_name}"
            if name in seen:
                continue
            seen.add(name)
            normalized.append(
                {
                    "name": name,
                    "command": name[1:],
                    "description": description.strip(),
                    "kind": kind,
                    "source": source,
                }
            )
        return normalized

    async def handle_human_room_message(
        self,
        content: str,
        *,
        source: str = "external",
        metadata: dict[str, Any] | None = None,
        participant_id: str | None = None,
        deliver_to_transport: bool = True,
    ) -> str:
        """Record and broadcast a human-originated room message."""
        if not content.strip():
            raise ValueError("content is required")
        participant_meta = None
        participant = None
        if participant_id:
            if self._room_bridge is None:
                raise RuntimeError("Room mode is not enabled")
            participant = self._room_bridge.participants.get(participant_id)
            if participant is None:
                raise LookupError(f"Unknown room participant: {participant_id}")
            participant_meta = asdict(participant)

        msg_id = str(uuid.uuid4())
        metadata_payload = metadata or {}
        self._append_turn(
            ConversationTurn(
                id=msg_id,
                role="user",
                content=content,
                participant_id=participant_id,
                participant_meta=participant_meta,
                thread_id=_non_empty_str(metadata_payload.get("thread_id")),
                visibility=_non_empty_str(metadata_payload.get("visibility")) or "public",
                metadata=metadata_payload,
            )
        )
        now = datetime.now(UTC)
        await self._complete_trace_span(
            kind="turn.user",
            name=content[:120] or "human turn",
            parent_span_id=self._trace_session_span_id,
            actor_type="user",
            actor_id=source,
            actor_label=source,
            attributes={"source": source, "metadata": metadata_payload},
            started_at=now,
            ended_at=now,
        )
        event = {
            "type": "user_confirmed",
            "id": msg_id,
            "content": content,
            "source": source,
            "metadata": metadata_payload,
        }
        if participant_id:
            event["participantId"] = participant_id
            event["participant"] = participant_meta
        thread_id = _non_empty_str(metadata_payload.get("thread_id"))
        if thread_id:
            event["threadId"] = thread_id
        await self._channels.broadcast(event)
        if self._room_bridge is not None and participant is not None:
            for room_id in participant.room_ids:
                await self._room_bridge.record_huddle_message(
                    room_id=room_id,
                    environment_id=participant.environment_id,
                    message_id=msg_id,
                    participant_id=participant.peer_id,
                    role="user",
                    content=content,
                    visibility=event.get("visibility", "public"),
                    thread_id=thread_id,
                    metadata=metadata_payload,
                )
        if deliver_to_transport:
            await self._send_explicit_human_message_to_transport(content)
        return msg_id

    async def handle_directed_room_message(
        self,
        target_peer_id: str,
        content: str,
        *,
        source: str = "external",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Record and route a directed human message to a room participant."""
        if self._room_bridge is None:
            raise RuntimeError("Room mode is not enabled")
        if not target_peer_id.strip():
            raise ValueError("target_peer_id is required")
        if not content.strip():
            raise ValueError("content is required")

        participant = self._room_bridge.participants.get(target_peer_id)
        display_target = participant.persona if participant else target_peer_id
        rendered_content = f"@{display_target} {content}"
        msg_id = await self.handle_human_room_message(
            rendered_content,
            source=source,
            metadata=metadata,
            participant_id=_non_empty_str(metadata.get("participant_id")) if metadata else None,
            deliver_to_transport=False,
        )

        delivered = await self._room_bridge.route_directed_message(
            target_peer_id,
            content,
            metadata=metadata,
        )
        if not delivered:
            raise LookupError(f"Unknown room participant: {target_peer_id}")
        return msg_id

    async def join_human_environment(
        self,
        *,
        participant_id: str,
        display_name: str,
        environment_id: str,
        role: str = "observer",
        room_id: str = "",
        capabilities: list[str] | None = None,
        surfaces: list[str] | None = None,
        environment_action_authorities: list[str] | None = None,
    ) -> dict[str, Any]:
        """Join a human participant to the live Environment room state."""
        if self._room_bridge is None:
            raise RuntimeError("Room mode is not enabled")
        meta = await self._room_bridge.join_human_environment(
            participant_id,
            display_name=display_name,
            environment_id=environment_id,
            role=role,
            room_id=room_id,
            capabilities=capabilities,
            surfaces=surfaces,
            environment_action_authorities=environment_action_authorities,
        )
        return asdict(meta)

    async def heartbeat_human_environment(
        self,
        *,
        participant_id: str,
        status: str | None = None,
        wakefulness: str | None = None,
        attention_state: str | None = None,
    ) -> dict[str, Any]:
        """Record heartbeat/state for a human Environment participant."""
        if self._room_bridge is None:
            raise RuntimeError("Room mode is not enabled")
        meta = await self._room_bridge.heartbeat(
            participant_id,
            status=status,
            wakefulness=wakefulness,
            attention_state=attention_state,
        )
        if meta is None:
            raise LookupError(f"Unknown room participant: {participant_id}")
        return asdict(meta)

    async def leave_human_environment(
        self,
        *,
        participant_id: str,
        reason: str = "left",
    ) -> None:
        """Leave a human participant from the live Environment room state."""
        if self._room_bridge is None:
            raise RuntimeError("Room mode is not enabled")
        await self._room_bridge.leave_human_environment(participant_id, reason=reason)

    async def close_environment_huddle(
        self,
        *,
        room_id: str,
        reason: str = "closed",
        summary: str = "",
    ) -> dict[str, Any]:
        """Close a live Environment huddle and publish its transcript."""
        if self._room_bridge is None:
            raise RuntimeError("Room mode is not enabled")
        return await self._room_bridge.close_environment_huddle(
            room_id=room_id,
            reason=reason,
            summary=summary,
        )

    def require_room_capability(self, participant_id: str, capability: str) -> None:
        """Raise when a room participant cannot perform a control action."""
        if self._room_bridge is None:
            raise RuntimeError("Room mode is not enabled")
        self._room_bridge.require_participant_capability(participant_id, capability)

    async def _send_explicit_human_message_to_transport(self, content: str) -> None:
        """Deliver an explicit human room message to Skuld's own transport."""
        if self._transport is None:
            logger.warning("No transport available for explicit human room message")
            return
        outbound = self._format_room_message_for_skuld(content)
        self._pending_explicit_human_messages.append((content, outbound))
        self._pending_explicit_human_response_count += 1
        if not self._transport.is_alive:
            logger.info("Starting transport for explicit human room message")
            await self._transport.start()
        await self._transport.send_message(outbound)

    def _format_room_message_for_skuld(self, content: str) -> str:
        """Wrap an explicit human room message with flock context for Skuld."""
        if self._room_bridge is None:
            return content

        participants = sorted(
            self._room_bridge.participants.values(),
            key=lambda participant: (
                0 if participant.participant_type == "skuld" else 1,
                participant.persona.lower(),
            ),
        )
        lines = [
            "You are Skuld, the observer/coordinator for an active flock session.",
            (
                "Answer as the session observer using the live flock context below, "
                "not as a generic standalone chat."
            ),
            "",
            "Current room participants:",
        ]
        for participant in participants:
            display = participant.display_name or participant.persona or participant.peer_id
            status = participant.status or "idle"
            lines.append(
                f"- {display} (peer_id={participant.peer_id}, "
                f"type={participant.participant_type}, status={status})"
            )
        lines.extend(
            [
                "",
                "Human message from an external communication interface:",
                content,
            ]
        )
        return "\n".join(lines)

    async def _publish_room_presence_event(self, event: SleipnirEvent) -> None:
        """Publish canonical room presence events through the existing Sleipnir bus."""
        await self._sleipnir_publisher.publish(event)

    def get_room_participants(self, environment_id: str | None = None) -> list[dict[str, Any]]:
        """Return the current room participants as plain dictionaries."""
        if self._room_bridge is None:
            return []
        return self._room_bridge.environment_roster(environment_id=environment_id)

    def get_communication_routes(self) -> list[dict[str, Any]]:
        """Return active external communication routes exposed by this broker."""
        routes: list[dict[str, Any]] = []
        for channel in self._channels.channels:
            route_getter = getattr(channel, "communication_route", None)
            if not callable(route_getter):
                continue
            route = route_getter()
            if isinstance(route, dict):
                routes.append(route)
        return routes

    def _build_auth_headers(self) -> dict[str, str]:
        """Build authentication headers for Volundr API calls.

        Priority:
        1. VOLUNDR_API_TOKEN (long-lived PAT injected by Infisical) — preferred
           because user JWTs expire after minutes while PATs last for months.
        2. User JWT from WebSocket connection (fallback for dev/local)
        3. Empty (dev mode — no auth, Volundr backend must accept)
        """
        service_token = os.environ.get("VOLUNDR_API_TOKEN", "")
        if service_token:
            return {"Authorization": f"Bearer {service_token}"}

        if self._user_jwt:
            return {"Authorization": f"Bearer {self._user_jwt}"}

        logger.debug("No auth token available — requests will be unauthenticated")
        return {}

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Lazy-init HTTP client for Volundr API calls.

        Recreates the client when the JWT changes so the Authorization
        header stays current.
        """
        if self._http_client is not None and self._http_client_jwt != self._user_jwt:
            await self._http_client.aclose()
            self._http_client = None

        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self.volundr_api_url,
                timeout=10.0,
                headers=self._build_auth_headers(),
            )
            self._http_client_jwt = self._user_jwt
        return self._http_client

    def _next_sequence(self) -> int:
        """Return a monotonically increasing sequence number."""
        seq = self._event_sequence
        self._event_sequence += 1
        return seq

    @staticmethod
    def _trace_label(name: str, *, limit: int = 120) -> str:
        """Trim trace labels so the API payloads stay readable."""
        value = " ".join(str(name or "").split())
        if len(value) <= limit:
            return value
        return value[: limit - 3].rstrip() + "..."

    async def _start_trace_span(
        self,
        *,
        kind: str,
        name: str,
        source_service: str = "skuld",
        parent_span_id: uuid.UUID | None = None,
        actor_type: str | None = None,
        actor_id: str | None = None,
        actor_label: str | None = None,
        attributes: dict[str, Any] | None = None,
        started_at: datetime | None = None,
    ) -> uuid.UUID | None:
        """Open a trace span in Volundr for this session."""
        if not self.volundr_api_url:
            return None
        client = await self._get_http_client()
        span_id = uuid.uuid4()
        payload = {
            "id": str(span_id),
            "session_id": str(self.session_id),
            "trace_id": str(self._trace_id),
            "kind": kind,
            "name": self._trace_label(name),
            "source_service": source_service,
            "started_at": (started_at or datetime.now(UTC)).isoformat(),
            "attributes": attributes or {},
        }
        if parent_span_id is not None:
            payload["parent_span_id"] = str(parent_span_id)
        if actor_type is not None:
            payload["actor_type"] = actor_type
        if actor_id is not None:
            payload["actor_id"] = actor_id
        if actor_label is not None:
            payload["actor_label"] = actor_label
        try:
            response = await client.post(FORGE_TRACE_SPANS_START_PATH, json=payload)
            if response.status_code < 300:
                return span_id
            logger.debug(
                "Trace span start failed (%d): %s",
                response.status_code,
                response.text[:200],
            )
        except Exception:
            logger.debug("Failed to start trace span", exc_info=True)
        return None

    async def _finish_trace_span(
        self,
        span_id: uuid.UUID | None,
        *,
        status: str = "completed",
        attributes: dict[str, Any] | None = None,
        ended_at: datetime | None = None,
    ) -> None:
        """Finish an already-open trace span."""
        if span_id is None or not self.volundr_api_url:
            return
        client = await self._get_http_client()
        payload = {
            "session_id": str(self.session_id),
            "ended_at": (ended_at or datetime.now(UTC)).isoformat(),
            "status": status,
            "attributes": attributes or {},
        }
        try:
            response = await client.post(
                f"/api/v1/forge/spans/{span_id}/finish",
                json=payload,
            )
            if response.status_code < 300:
                return
            logger.debug(
                "Trace span finish failed (%d): %s",
                response.status_code,
                response.text[:200],
            )
        except Exception:
            logger.debug("Failed to finish trace span id=%s", span_id, exc_info=True)

    async def _complete_trace_span(
        self,
        *,
        kind: str,
        name: str,
        source_service: str = "skuld",
        parent_span_id: uuid.UUID | None = None,
        actor_type: str | None = None,
        actor_id: str | None = None,
        actor_label: str | None = None,
        attributes: dict[str, Any] | None = None,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        duration_ms: int | None = None,
        status: str = "completed",
    ) -> uuid.UUID | None:
        """Record a completed span in a single request."""
        if not self.volundr_api_url:
            return None
        client = await self._get_http_client()
        span_id = uuid.uuid4()
        actual_started_at = started_at or datetime.now(UTC)
        payload = {
            "id": str(span_id),
            "session_id": str(self.session_id),
            "trace_id": str(self._trace_id),
            "kind": kind,
            "name": self._trace_label(name),
            "source_service": source_service,
            "started_at": actual_started_at.isoformat(),
            "status": status,
            "attributes": attributes or {},
        }
        if ended_at is not None:
            payload["ended_at"] = ended_at.isoformat()
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if parent_span_id is not None:
            payload["parent_span_id"] = str(parent_span_id)
        if actor_type is not None:
            payload["actor_type"] = actor_type
        if actor_id is not None:
            payload["actor_id"] = actor_id
        if actor_label is not None:
            payload["actor_label"] = actor_label
        try:
            response = await client.post(FORGE_TRACE_SPANS_COMPLETE_PATH, json=payload)
            if response.status_code < 300:
                return span_id
            logger.debug(
                "Trace span complete failed (%d): %s",
                response.status_code,
                response.text[:200],
            )
        except Exception:
            logger.debug(
                "Failed to complete trace span kind=%s name=%s",
                _sanitize_log(kind),
                _sanitize_log(name),
                exc_info=True,
            )
        return None

    async def _ensure_session_trace_started(self) -> None:
        """Ensure the root session lifecycle span is open."""
        if self._trace_session_span_id is not None:
            return
        self._trace_session_span_id = await self._start_trace_span(
            kind="session.lifecycle",
            name=self._settings.session.name or "session",
            source_service="skuld",
            actor_type="system",
            actor_id=self.session_id,
            actor_label=self._settings.session.name or "session",
            attributes={
                "model": self.model,
                "workspace_path": self.workspace_dir,
                "workflow_enabled": bool(self._has_workflow_trigger()),
            },
        )

    # -- Durable event log (full-fidelity transcript capture) -----------------

    FORGE_LOG_PATH_TEMPLATE = "/api/v1/forge/sessions/{sid}/log"

    @staticmethod
    def _extract_request_id(data: dict) -> str | None:
        """Best-effort turn correlation id from a raw CLI frame."""
        rid = data.get("request_id")
        if isinstance(rid, str) and rid:
            return rid
        msg = data.get("message")
        if isinstance(msg, dict):
            inner = msg.get("request_id")
            if isinstance(inner, str) and inner:
                return inner
        return None

    def _enqueue_event_log(self, data: dict) -> None:
        """Buffer a raw CLI frame for durable persistence. Never raises.

        Runs for every frame regardless of attached channels — this is what
        guarantees no agent output is dropped when no client is connected.
        """
        if not self._settings.event_log_enabled or not self.volundr_api_url:
            return
        self._event_log_seq += 1
        entry = {
            "seq": self._event_log_seq,
            "kind": str(data.get("type", "unknown"))[:64],
            "payload": data,
            "request_id": self._extract_request_id(data),
            # Emission time, captured HERE — without it the ingest stamps
            # arrival time, which skews replayed timelines whenever the POST
            # batch lags (rate-limit stalls, backend hiccups). Clients replay
            # these ts so an old session shows when things actually happened.
            "ts": datetime.now(UTC).isoformat(),
        }
        role = data.get("role")
        if isinstance(role, str):
            entry["role"] = role[:32]
        self._event_log_buffer.append(entry)
        # Safety valve: cap memory if the backend is unreachable for a long time.
        # Dropping the oldest is the least-bad option vs OOM-killing the broker,
        # and is logged loudly so the loss is visible.
        overflow = len(self._event_log_buffer) - self._settings.event_log_max_buffer
        if overflow > 0:
            del self._event_log_buffer[:overflow]
            logger.warning(
                "event log buffer overflow — dropped %d oldest frames (backend unreachable?)",
                overflow,
            )

    def _enqueue_human_turn_event(self, content: str, turn_id: str) -> None:
        """Persist a HUMAN message to the durable event log as a user frame.

        The CLI never echoes the operator's own prompt as a text frame — only
        tool_results arrive with role=user — so a transcript replayed purely
        from the durable log (web Code tab, iOS) omitted every human turn ("the
        transcript doesn't show my message"). Synthesize a string-content user
        frame, which the replay reducers render directly as a user turn (and
        string content distinguishes it from the CLI's block-list tool_result
        user frames).
        """
        if not content:
            return
        self._enqueue_event_log(
            {
                "type": "user",
                "role": "user",
                "uuid": turn_id,
                "message": {"role": "user", "content": content},
            }
        )

    async def _surface_remote_control_url(self, url: str) -> None:
        """Surface a remote-control pairing URL as an assistant turn everywhere.

        Appends it to conversation history (the conversation endpoint), enqueues a
        renderable assistant frame to the durable log (log-only replay), and
        broadcasts to any live channels — so whichever surface a client uses, the
        hand-off link to the native app is visible.
        """
        notice = (
            "🔗 **Remote control ready.** Drive this session from the Claude app "
            f"or claude.ai/code:\n\n{url}\n\n"
            "Open the link (or scan the QR in the host terminal) to attach the "
            "native app. This session is controlled remotely — messages typed "
            "here are not sent to the agent."
        )
        turn_id = str(uuid.uuid4())
        self._append_turn(ConversationTurn(id=turn_id, role="assistant", content=notice))
        frame = {
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": notice}]},
        }
        self._enqueue_event_log(frame)
        try:
            await self._channels.broadcast(frame)
        except Exception:
            logger.debug("remote-control URL broadcast failed", exc_info=True)
        safe_url_for_log = url.replace("\r", "").replace("\n", "")
        logger.info(
            "Remote control pairing URL surfaced for session %s: %s",
            self.session_id,
            safe_url_for_log,
        )

    async def _event_log_flush_loop(self) -> None:
        """Background worker: drain the event-log buffer to Volundr with retry."""
        interval = self._settings.event_log_flush_interval_ms / 1000.0
        while not self._event_log_stopping:
            await asyncio.sleep(interval)
            try:
                await self._flush_event_log()
            except Exception:
                logger.debug("event log flush iteration failed", exc_info=True)

    async def _flush_event_log(self) -> None:
        """Send one batch from the front of the buffer. Removes only on success."""
        if not self.volundr_api_url:
            return
        async with self._event_log_lock:
            batch = self._event_log_buffer[: self._settings.event_log_batch_size]
        if not batch:
            return

        client = await self._get_http_client()
        path = self.FORGE_LOG_PATH_TEMPLATE.format(sid=self.session_id)
        try:
            response = await client.post(path, json={"entries": batch})
        except Exception:
            logger.debug("event log POST failed — will retry", exc_info=True)
            return
        if response.status_code >= 300:
            logger.debug(
                "event log POST rejected (%d): %s — will retry",
                response.status_code,
                response.text[:200],
            )
            return
        # Idempotent on (session_id, seq), so removing exactly the sent count is
        # safe even if newer frames were appended during the POST.
        async with self._event_log_lock:
            del self._event_log_buffer[: len(batch)]

    async def _init_event_log(self) -> None:
        """Resume the seq counter from the backend so restarts don't collide.

        The PK is (session_id, seq); if a restarted broker reset seq to 0 its
        appends would hit ON CONFLICT DO NOTHING and silently vanish. Seeding
        from the stored head keeps the sequence monotonic across restarts.
        """
        if not self._settings.event_log_enabled or not self.volundr_api_url:
            return
        client = await self._get_http_client()
        path = self.FORGE_LOG_PATH_TEMPLATE.format(sid=self.session_id) + "/head"
        try:
            response = await client.get(path)
            if response.status_code < 300:
                self._event_log_seq = int(response.json().get("latest_seq", 0))
        except Exception:
            logger.debug("event log head fetch failed — starting seq at 0", exc_info=True)
        self._event_log_task = asyncio.create_task(self._event_log_flush_loop())
        logger.info("Durable event log started (resume seq=%d)", self._event_log_seq)

    async def _stop_event_log(self) -> None:
        """Drain remaining frames and stop the worker on shutdown."""
        self._event_log_stopping = True
        if self._event_log_task is not None:
            self._event_log_task.cancel()
            await asyncio.gather(self._event_log_task, return_exceptions=True)
            self._event_log_task = None
        # Final best-effort drain so the last turn isn't lost on shutdown.
        for _ in range(self._settings.event_log_max_buffer):
            async with self._event_log_lock:
                remaining = len(self._event_log_buffer)
            if remaining == 0:
                return
            before = remaining
            await self._flush_event_log()
            async with self._event_log_lock:
                if len(self._event_log_buffer) >= before:
                    return  # made no progress (backend down) — give up

    async def _emit_pipeline_event(
        self,
        event_type: str,
        data: dict,
        *,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost: float | None = None,
        duration_ms: int | None = None,
        model: str | None = None,
    ) -> None:
        """Emit a raw event to the Volundr event pipeline.

        Fires as a background task — must not raise or block the WebSocket.
        """
        if not self.volundr_api_url:
            return

        client = await self._get_http_client()
        from datetime import datetime

        payload = {
            "session_id": self.session_id,
            "event_type": event_type,
            "timestamp": datetime.now(UTC).isoformat(),
            "data": data,
            "sequence": self._next_sequence(),
        }

        if tokens_in is not None:
            payload["tokens_in"] = tokens_in
        if tokens_out is not None:
            payload["tokens_out"] = tokens_out
        if cost is not None:
            payload["cost"] = cost
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        if model is not None:
            payload["model"] = model

        try:
            response = await client.post(FORGE_EVENTS_PATH, json=payload)
            if response.status_code < 300:
                logger.debug("Pipeline event emitted: %s", event_type)
            else:
                logger.debug(
                    "Pipeline event failed (%d): %s",
                    response.status_code,
                    response.text[:200],
                )
        except Exception:
            logger.debug("Failed to emit pipeline event: %s", event_type, exc_info=True)

    async def _report_usage(self, result_data: dict) -> None:
        """Report token usage from a CLI result event to the Volundr API.

        Fires as a background task — must not raise or block the WebSocket.
        """
        if not self.volundr_api_url:
            return

        model_usage = result_data.get("modelUsage", {})
        if not model_usage:
            logger.debug("No modelUsage in result event, skipping usage report")
            return

        client = await self._get_http_client()
        url = f"{FORGE_SESSIONS_PATH}/{self.session_id}/usage"

        for model_id, usage in model_usage.items():
            tokens = (
                usage.get("inputTokens", 0)
                + usage.get("outputTokens", 0)
                + usage.get("cacheReadInputTokens", 0)
                + usage.get("cacheCreationInputTokens", 0)
            )
            if tokens <= 0:
                continue

            cost = usage.get("costUSD")
            payload = {
                "tokens": tokens,
                "provider": "cloud",
                "model": model_id,
                "message_count": 1,
            }
            if cost is not None:
                payload["cost"] = cost

            try:
                response = await client.post(url, json=payload)
                if response.status_code < 300:
                    logger.info("Reported usage")
                else:
                    logger.warning(
                        "Usage report failed (%d): %s",
                        response.status_code,
                        response.text[:200],
                    )
            except Exception:
                logger.warning("Failed to report usage", exc_info=True)

    async def _report_timeline_event(self, event: dict) -> None:
        """Report a single timeline event to the Volundr API.

        Fires as a background task — must not raise or block the WebSocket.
        The event dict must contain at minimum: t, type, label.
        """
        if not self.volundr_api_url:
            return

        client = await self._get_http_client()
        url = f"{FORGE_CHRONICLES_PATH}/{self.session_id}/timeline"

        try:
            response = await client.post(url, json=event)
            if response.status_code < 300:
                logger.debug("Timeline event reported")
            else:
                logger.debug(
                    "Timeline event report failed (%d): %s",
                    response.status_code,
                    response.text[:200],
                )
        except Exception:
            logger.debug("Failed to report timeline event", exc_info=True)

    @staticmethod
    def _classify_pipeline_event(tool_ev: dict) -> str:
        """Map a timeline tool event dict to a SessionEventType value."""
        ev_type = tool_ev.get("type", "")
        action = tool_ev.get("action", "")
        if ev_type == "file":
            if action == "created":
                return "file_created"
            if action == "deleted":
                return "file_deleted"
            return "file_modified"
        if ev_type == "git":
            return "git_commit"
        if ev_type == "git_push":
            return "git_push"
        if ev_type == "terminal":
            return "terminal_command"
        return "tool_use"

    @staticmethod
    def _event_content_blocks(data: dict[str, Any]) -> list[dict[str, Any]]:
        """Return normalized content blocks from either top-level or message payload."""
        content = data.get("content", [])
        if isinstance(content, list) and content:
            return [block for block in content if isinstance(block, dict)]
        message = data.get("message")
        if isinstance(message, dict):
            nested = message.get("content", [])
            if isinstance(nested, list):
                return [block for block in nested if isinstance(block, dict)]
        return []

    @classmethod
    def _is_tool_result_only_user_event(cls, data: dict[str, Any]) -> bool:
        """Return True when a user event only carries internal tool_result blocks."""
        blocks = cls._event_content_blocks(data)
        return bool(blocks) and all(block.get("type") == "tool_result" for block in blocks)

    @staticmethod
    def _extract_tool_result_preview(block: dict[str, Any]) -> str:
        """Return a compact text preview for a tool_result payload."""
        content = block.get("content", "")
        if isinstance(content, str):
            return content[:200]
        if isinstance(content, list):
            text_parts = [
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("text")
            ]
            return " ".join(text_parts)[:200]
        return ""

    async def _start_assistant_tool_trace_spans(self, data: dict[str, Any]) -> None:
        """Open child tool spans for assistant tool_use blocks."""
        if self._trace_assistant_span_id is None:
            return

        assistant_label = (
            self._settings.mesh.persona
            if getattr(self._settings, "mesh", None) is not None
            else None
        ) or self._settings.session.name

        for block in self._event_content_blocks(data):
            if block.get("type") != "tool_use":
                continue

            tool_name = str(block.get("name") or "tool").strip() or "tool"
            tool_input = block.get("input") if isinstance(block.get("input"), dict) else {}
            tool_key = str(block.get("id") or uuid.uuid4())
            if tool_key in self._trace_assistant_tool_spans:
                continue

            span_id = await self._start_trace_span(
                kind="tool.call",
                name=tool_name,
                parent_span_id=self._trace_assistant_span_id,
                actor_type="assistant",
                actor_id=self._observer_peer_id() or self.session_id,
                actor_label=assistant_label,
                attributes={
                    "tool_name": tool_name,
                    "tool_use_id": str(block.get("id") or ""),
                    "tool_input": tool_input,
                },
            )
            if span_id is None:
                continue

            self._trace_assistant_tool_spans[tool_key] = span_id
            self._trace_assistant_tool_order.append(tool_key)

            command = str(tool_input.get("command") or "").strip()
            if command:
                self._assistant_pending_commands[tool_key] = command

    def _pop_assistant_tool_trace_span(
        self,
        tool_use_id: str,
    ) -> tuple[uuid.UUID | None, str]:
        """Pop a pending assistant tool span by id, falling back to FIFO order."""
        tool_key = (
            tool_use_id if tool_use_id and tool_use_id in self._trace_assistant_tool_spans else ""
        )
        if not tool_key and self._trace_assistant_tool_order:
            tool_key = self._trace_assistant_tool_order[0]
        if not tool_key:
            return None, ""

        span_id = self._trace_assistant_tool_spans.pop(tool_key, None)
        if tool_key in self._trace_assistant_tool_order:
            self._trace_assistant_tool_order.remove(tool_key)
        command = self._assistant_pending_commands.pop(tool_key, "")
        return span_id, command

    async def _finish_assistant_tool_trace_spans_from_user_event(
        self, data: dict[str, Any]
    ) -> None:
        """Close assistant child tool spans from tool_result-only user events."""
        for block in self._event_content_blocks(data):
            if block.get("type") != "tool_result":
                continue
            tool_use_id = str(block.get("tool_use_id") or "")
            span_id, command = self._pop_assistant_tool_trace_span(tool_use_id)
            await self._finish_trace_span(
                span_id,
                status="failed" if bool(block.get("is_error")) else "completed",
                attributes={
                    "tool_use_id": tool_use_id,
                    "command": command,
                    "exit_code": SessionArtifacts._extract_exit_code(block),
                    "is_error": bool(block.get("is_error")),
                    "result_preview": self._extract_tool_result_preview(block),
                },
            )

    async def _finish_pending_assistant_tool_trace_spans(
        self,
        *,
        status: str,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """Close any assistant tool spans still open on turn/session termination."""
        while self._trace_assistant_tool_order:
            tool_key = self._trace_assistant_tool_order.pop(0)
            span_id = self._trace_assistant_tool_spans.pop(tool_key, None)
            command = self._assistant_pending_commands.pop(tool_key, "")
            extra_attributes = dict(attributes or {})
            if command:
                extra_attributes.setdefault("command", command)
            extra_attributes.setdefault("tool_use_id", tool_key)
            await self._finish_trace_span(
                span_id,
                status=status,
                attributes=extra_attributes,
            )
        self._trace_assistant_tool_spans.clear()
        self._assistant_pending_commands.clear()

    async def _report_session_start(self) -> None:
        """Report the session start timeline event (once)."""
        if self._session_start_reported:
            return
        self._session_start_reported = True
        await self._report_timeline_event(
            {
                "t": 0,
                "type": "session",
                "label": "Session started",
            }
        )
        # Emit session_start to event pipeline
        await self._emit_pipeline_event(
            "session_start",
            {
                "model": self.model,
                "session_name": self._settings.session.name,
            },
            model=self.model,
        )

    async def _report_activity_state(
        self, state: str, *, extra_metadata: dict[str, Any] | None = None
    ) -> None:
        """Report activity state change to Volundr.

        States: active, idle, tool_executing.
        Debounces rapid transitions — only reports when state actually changes.
        """
        if state == self._activity_state and not extra_metadata:
            return

        self._activity_state = state
        now = time.monotonic()
        self._last_activity_report = now

        if not self.volundr_api_url:
            return

        metadata = {
            "turn_count": self._artifacts.turn_count,
            "duration_seconds": self._artifacts.duration_seconds,
        }
        # Ride the CLI/agent conversation id upward so Volundr can persist it
        # and resume the conversation when the session is restarted. Works for
        # both Claude (session UUID) and Codex (thread id) — every
        # resume-capable transport implements .session_id.
        cli_session_id = self._transport.session_id if self._transport else None
        if cli_session_id:
            metadata["cli_session_id"] = cli_session_id
        if extra_metadata:
            metadata.update(extra_metadata)

        try:
            client = await self._get_http_client()
            resp = await client.post(
                f"{FORGE_SESSIONS_PATH}/{self.session_id}/activity",
                json={"state": state, "metadata": metadata},
            )
            logger.info(
                "Activity report: state=%s status=%d url=%s",
                state,
                resp.status_code,
                resp.url,
            )
        except Exception:
            logger.warning(
                "Failed to report activity state %s",
                state,
                exc_info=True,
            )

    async def _generate_summary(self) -> dict:
        """Ask the CLI to generate a session summary.

        Returns a dict with ``summary``, ``key_changes``, and
        ``unfinished_work`` keys.  Falls back to artifacts data
        when the CLI is unavailable or times out.
        """
        if not self._transport or not self._transport.is_alive:
            logger.info("CLI not alive, skipping AI summary generation")
            return {
                "summary": None,
                "key_changes": self._artifacts.files_changed,
                "unfinished_work": None,
            }

        try:
            await self._transport.send_message(CHRONICLE_SUMMARY_PROMPT)

            # Wait for the result event (set by _handle_cli_message)
            deadline = time.monotonic() + SUMMARY_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                last = self._transport.last_result
                if last is not None:
                    break
                await asyncio.sleep(0.25)

            last = self._transport.last_result
            if last is None:
                logger.warning("Summary generation timed out after %ds", SUMMARY_TIMEOUT_SECONDS)
                return {
                    "summary": None,
                    "key_changes": self._artifacts.files_changed,
                    "unfinished_work": None,
                }

            # Extract text from result
            result_text = last.get("result", "")
            if not result_text:
                # Try to extract from content blocks
                for block in last.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        result_text = block.get("text", "")
                        break

            # Strip markdown fencing if present
            result_text = result_text.strip()
            if result_text.startswith("```"):
                lines = result_text.split("\n")
                lines = lines[1:]  # drop opening fence
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]  # drop closing fence
                result_text = "\n".join(lines).strip()

            parsed = json.loads(result_text)
            logger.info("AI summary generated successfully")
            return {
                "summary": parsed.get("summary"),
                "key_changes": parsed.get("key_changes", self._artifacts.files_changed),
                "unfinished_work": parsed.get("unfinished_work"),
            }
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to parse summary response: %s", e)
        except Exception:
            logger.warning("Summary generation failed", exc_info=True)

        return {
            "summary": None,
            "key_changes": self._artifacts.files_changed,
            "unfinished_work": None,
        }

    def _fallback_chronicle_summary(self) -> dict[str, Any]:
        """Build a best-effort chronicle summary from already-captured artifacts."""
        summary: str | None = None
        unfinished_work: str | None = None

        if isinstance(self._artifacts.structured_outcome, dict):
            raw_summary = self._artifacts.structured_outcome.get("summary")
            if isinstance(raw_summary, str) and raw_summary.strip():
                summary = raw_summary.strip()

            raw_unfinished = self._artifacts.structured_outcome.get("unfinished_work")
            if isinstance(raw_unfinished, str) and raw_unfinished.strip():
                unfinished_work = raw_unfinished.strip()

        return {
            "summary": summary,
            "key_changes": list(self._artifacts.files_changed),
            "unfinished_work": unfinished_work,
        }

    def _build_transcript(self) -> str:
        """Concatenate all assistant turns to form the session transcript."""
        return "\n\n".join(
            turn.content
            for turn in self._conversation_turns
            if turn.role == "assistant" and turn.content
        )

    def _extract_and_store_outcome(self) -> None:
        """Extract outcome block from the session transcript and store in artifacts.

        No-ops silently when no outcome block is present or parsing fails.
        """
        transcript = self._build_transcript()
        if not transcript:
            return
        try:
            outcome = parse_outcome_block(transcript)
            if outcome is None:
                return
            self._artifacts.structured_outcome = outcome.fields
            self._artifacts.outcome_valid = outcome.valid
        except Exception:
            logger.warning("Failed to extract outcome block from transcript", exc_info=True)

    async def _emit_session_ended_event(self) -> None:
        """Emit ravn.session.ended via Sleipnir with structured outcome and saga/run context.

        Always emits the event — even when outcome extraction failed — so that
        downstream Ting pipeline executors receive session completion signals.
        """
        outcome_str = "SUCCESS" if self._artifacts.outcome_valid else "PARTIAL"
        source = f"ravn:{self.session_id}"
        persona = self._settings.session.name

        try:
            event = ravn_session_ended(
                session_id=self.session_id,
                persona=persona,
                outcome=outcome_str,
                token_count=self._artifacts.total_tokens,
                duration_s=self._artifacts.duration_seconds,
                source=source,
                correlation_id=self.session_id,
            )
            if self._artifacts.structured_outcome is not None:
                event.payload["structured_outcome"] = self._artifacts.structured_outcome
                event.payload["outcome_valid"] = self._artifacts.outcome_valid
                for key in (
                    "verdict",
                    "tests_passing",
                    "scope_adherence",
                    "pr_url",
                    "summary",
                    "files_changed",
                ):
                    if key in self._artifacts.structured_outcome:
                        event.payload[key] = self._artifacts.structured_outcome[key]
            if self._artifacts.files_changed:
                event.payload["files_changed"] = list(self._artifacts.files_changed)
            if self._artifacts.run_id:
                event.payload["run_id"] = self._artifacts.run_id
            if self._artifacts.saga_id:
                event.payload["saga_id"] = self._artifacts.saga_id
            await self._sleipnir_publisher.publish(event)
            logger.info(
                "Session ended event emitted: session=%s outcome=%s saga=%s run=%s",
                self.session_id,
                outcome_str,
                self._artifacts.saga_id,
                self._artifacts.run_id,
            )
        except Exception:
            logger.warning("Failed to emit session ended event", exc_info=True)

    async def _on_result_publish_mesh(self) -> None:
        """Called when a CLI result event arrives (turn finished).

        Extracts outcome from transcript and publishes ``code.changed``
        on the mesh so flock peers (reviewer) can react immediately.
        """
        self._extract_and_store_outcome()
        await self._publish_mesh_outcome()

    async def _git_diff_summary(self) -> str:
        """Return a truncated git diff from the workspace.

        Best-effort: returns an empty string on any failure so mesh
        publishing is never blocked by git issues.
        """
        max_bytes = self._settings.mesh.diff_max_bytes
        timeout = self._settings.mesh.diff_timeout_s

        # Try committed changes first (HEAD~1..HEAD) since the coder
        # session typically commits before finishing.  Fall back to
        # uncommitted working-tree changes (diff HEAD).
        for diff_args in (["git", "diff", "HEAD~1..HEAD"], ["git", "diff", "HEAD"]):
            try:
                proc = await asyncio.create_subprocess_exec(
                    *diff_args,
                    cwd=self.workspace_dir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except Exception:
                return ""
            raw = stdout.decode(errors="replace")
            if raw.strip():
                if len(raw) > max_bytes:
                    return raw[:max_bytes] + "\n... (truncated)"
                return raw

        return ""

    async def _publish_mesh_outcome(self) -> None:
        """Publish a ``code.changed`` event on the mesh so flock peers react.

        Called after the session completes.  The reviewer ravn subscribes
        to ``code.changed`` and will trigger a review when it receives this.
        """
        if self._mesh_adapter is None:
            return

        from ravn.domain.events import RavnEvent, RavnEventType

        diff_summary = await self._git_diff_summary()

        outcome_payload: dict = {
            "event_type": "code.changed",
            "session_id": self.session_id,
            "persona": self._settings.mesh.persona,
            "summary": (
                f"Session completed"
                f" ({self._artifacts.turn_count} turns,"
                f" {len(self._artifacts.files_changed)} files)"
            ),
            "workspace_path": self.workspace_dir,
        }

        initial_prompt = self._settings.session.initial_prompt
        if initial_prompt:
            outcome_payload["task_description"] = initial_prompt

        if diff_summary:
            outcome_payload["diff_summary"] = diff_summary

        if self._artifacts.structured_outcome is not None:
            outcome_payload["outcome"] = self._artifacts.structured_outcome
        if self._artifacts.files_changed:
            outcome_payload["files_changed"] = list(self._artifacts.files_changed)

        event = RavnEvent(
            type=RavnEventType.OUTCOME,
            source=f"skuld:{self._mesh_adapter.peer_id}",
            payload=outcome_payload,
            timestamp=datetime.now(UTC),
            urgency=0.8,
            correlation_id=self.session_id,
            session_id=self.session_id,
        )

        try:
            await self._mesh_adapter._mesh.publish(event, "code.changed")
            logger.info(
                "Mesh: published code.changed (peer=%s, files=%d)",
                self._mesh_adapter.peer_id,
                len(self._artifacts.files_changed),
            )
            # RoomMeshBridge subscribes to the same NNG bus and will
            # pick this up via loopback (subscriber dials own pub address
            # from cluster.yaml).  No separate broadcast needed.

        except Exception:
            logger.warning("Mesh: failed to publish code.changed", exc_info=True)

    async def _report_chronicle(self) -> None:
        """Report chronicle summary data to the Volundr API on shutdown.

        Mirrors ``_report_usage`` — fires once during shutdown, best-effort,
        never raises.

        Also extracts the outcome block from the session transcript and emits
        the ``ravn.session.ended`` Sleipnir event so Ting can track run completion.
        """
        self._extract_and_store_outcome()
        await self._emit_session_ended_event()
        await self._publish_mesh_outcome()

        # The chronicle SUMMARY (an LLM pass on stop) is opt-in — OFF by default
        # in our pipeline. The session-ended signals above (Ting run tracking,
        # mesh outcome) still fire; only the extra summarization is gated.
        if not self._settings.chronicle_on_stop_enabled:
            return

        if not self.volundr_api_url:
            return

        has_reportable_artifacts = (
            self._artifacts.turn_count > 0
            or bool(self._artifacts.files_changed)
            or self._artifacts.structured_outcome is not None
        )
        if not has_reportable_artifacts:
            logger.info("No chronicle artifacts recorded, skipping chronicle report")
            return

        logger.info(
            "Generating chronicle report (turns=%d, files=%d, duration=%ds)",
            self._artifacts.turn_count,
            len(self._artifacts.files_changed),
            self._artifacts.duration_seconds,
        )

        try:
            summary_data = (
                await self._generate_summary()
                if self._artifacts.turn_count > 0
                else self._fallback_chronicle_summary()
            )

            client = await self._get_http_client()
            url = f"{FORGE_SESSIONS_PATH}/{self.session_id}/chronicle"

            payload: dict = {
                "duration_seconds": self._artifacts.duration_seconds,
            }
            if summary_data.get("summary"):
                payload["summary"] = summary_data["summary"]
            if summary_data.get("key_changes"):
                payload["key_changes"] = summary_data["key_changes"]
            if summary_data.get("unfinished_work"):
                payload["unfinished_work"] = summary_data["unfinished_work"]

            response = await client.post(url, json=payload)
            if response.status_code < 300:
                logger.info("Chronicle report submitted successfully")
            else:
                logger.warning(
                    "Chronicle report failed (%d): %s",
                    response.status_code,
                    response.text[:200],
                )

            # Emit session_stop to event pipeline
            await self._emit_pipeline_event(
                "session_stop",
                {
                    "reason": "shutdown",
                    "total_tokens": 0,
                    "duration_seconds": self._artifacts.duration_seconds,
                    "turn_count": self._artifacts.turn_count,
                    "files_changed": len(self._artifacts.files_changed),
                },
            )
        except Exception:
            logger.warning("Failed to report chronicle", exc_info=True)

    async def _write_workspace_archive(self) -> None:
        """Write a workspace-backed archive snapshot for stopped-session reads."""
        try:
            transcript_payload = {
                "turns": [asdict(turn) for turn in self._conversation_turns],
                "is_active": False,
                "last_activity": "",
            }
            aggregated_logs = aggregate_workspace_logs(
                self.workspace_dir,
                lines=5000,
                level="DEBUG",
            )
            workspace_slug = self.workspace_dir.replace("/", "-")
            event_source_dir = Path.home() / ".claude" / "projects" / workspace_slug
            self._archive_store.write_archive(
                session_id=self.session_id,
                workspace_dir=self.workspace_dir,
                transcript_payload=transcript_payload,
                aggregated_logs=aggregated_logs,
                event_source_dir=event_source_dir,
            )
            logger.info("Workspace archive written for session %s", self.session_id)
        except Exception:
            logger.warning("Failed to write workspace archive", exc_info=True)

    async def _init_telegram_channel(self) -> None:
        """Initialize and register a Telegram channel if configured."""
        tg_config = self._settings.telegram
        if not tg_config.enabled:
            return

        if not tg_config.bot_token or not tg_config.chat_id:
            logger.warning("Telegram enabled but bot_token or chat_id missing, skipping")
            return

        try:
            channel = TelegramChannel(
                bot_token=tg_config.bot_token,
                chat_id=tg_config.chat_id,
                notify_only=tg_config.notify_only,
                topic_mode=tg_config.topic_mode,
                message_thread_id=tg_config.message_thread_id,
                topic_name=self._build_telegram_topic_name(),
                on_message=self._dispatch_browser_message,
            )
            await channel.start()
            self._channels.add(channel)
            logger.info("Telegram channel initialized for chat %s", tg_config.chat_id)
        except RuntimeError:
            logger.warning("python-telegram-bot not installed, Telegram channel disabled")
        except Exception:
            logger.warning("Failed to initialize Telegram channel", exc_info=True)

    def _build_telegram_topic_name(self) -> str:
        """Build a readable Telegram topic name for the active session."""
        session_name = (self._settings.session.name or "").strip()
        session_id = (self._settings.session.id or "").strip()

        if not session_name or session_name == "unknown":
            session_name = "Volundr session"

        pieces = [session_name]
        if session_id and session_id not in session_name:
            pieces.append(session_id[:8])

        topic_name = " · ".join(piece for piece in pieces if piece).strip()
        topic_name = " ".join(topic_name.split())
        return topic_name[:128] or "Volundr session"

    def _update_jwt_from_websocket(self, websocket: WebSocket) -> None:
        """Extract and store JWT from an incoming WebSocket connection.

        Prefers the Authorization header (set by Envoy or reverse proxy),
        then falls back to the access_token query parameter (browser).
        Updates the stored JWT on each connection so token refreshes
        propagate automatically.
        """
        try:
            token = _extract_token_from_websocket(websocket)
        except Exception:
            logger.debug("Failed to extract JWT from WebSocket", exc_info=True)
            return
        if not token:
            if self._user_jwt is None:
                logger.warning("No JWT found on WebSocket connection")
            return

        self._user_jwt = token
        self._user_claims = _decode_jwt_claims(token)

        user_id = self._user_claims.get("sub", "unknown")
        logger.info("JWT updated from WebSocket connection (sub=%s)", _sanitize_log(user_id))

        # Propagate new auth headers to the chronicle watcher
        if self._chronicle_watcher is not None:
            self._chronicle_watcher.update_headers(self._build_auth_headers())

    async def _safe_browser_send_json(self, websocket: WebSocket, payload: dict[str, Any]) -> bool:
        """Send a browser frame unless the client has already disconnected."""
        try:
            await websocket.send_json(payload)
            return True
        except Exception as exc:
            if _is_expected_ws_disconnect(exc):
                logger.info("WebSocket disconnected")
                return False
            raise

    async def handle_websocket(self, websocket: WebSocket) -> None:
        """Handle a browser WebSocket connection at /session."""
        # Extract JWT before accepting — headers are available pre-accept
        self._update_jwt_from_websocket(websocket)

        await websocket.accept()
        channel = WebSocketChannel(websocket)
        self._channels.add(channel)
        conn_count = self._channels.count
        logger.info("WebSocket connected, total channels: %d", conn_count)

        try:
            if not self._transport:
                logger.error("handle_websocket: transport not initialized")
                await self._safe_browser_send_json(
                    websocket,
                    {"type": "error", "content": "Transport not initialized"},
                )
                return

            # Lazy-start transport on first browser connection
            if not self._transport.is_alive:
                if self._is_room_only_workflow_session():
                    logger.info(
                        "handle_websocket: workflow room session detected; "
                        "skipping transport lazy-start"
                    )
                else:
                    logger.info("handle_websocket: transport not alive, starting...")
                    try:
                        await self._transport.start()
                        logger.info("handle_websocket: transport started successfully")
                    except Exception as e:
                        logger.error(
                            "handle_websocket: transport.start() failed: %r",
                            e,
                            exc_info=True,
                        )
                        await self._safe_browser_send_json(
                            websocket,
                            {
                                "type": "error",
                                "content": f"Transport start failed: {e}",
                            },
                        )
                        return
            else:
                logger.debug("handle_websocket: transport already alive")

            # Report session start to timeline (once, on first connection)
            asyncio.create_task(self._report_session_start())

            # Send welcome message
            if not await self._safe_browser_send_json(
                websocket,
                {"type": "system", "content": f"Connected to session {self.session_id}"},
            ):
                return
            logger.debug("handle_websocket: welcome message sent")

            # Send transport capabilities so the frontend knows which
            # controls to render.
            if self._transport:
                caps = {"type": "capabilities", **asdict(self._transport.capabilities)}
                if not await self._safe_browser_send_json(websocket, caps):
                    return
                logger.debug("handle_websocket: capabilities sent")

            # Replay conversation history so late-joining browsers see
            # earlier messages (including the initial prompt)
            if self._conversation_turns:
                logger.info(
                    "Replaying %d conversation turns to new browser",
                    len(self._conversation_turns),
                )
                if not await self._safe_browser_send_json(
                    websocket,
                    {
                        "type": "conversation_history",
                        "turns": [asdict(t) for t in self._conversation_turns],
                    },
                ):
                    return

            # Send current room state to late-joining browsers when room mode active
            if self._room_bridge is not None:
                if not await self._safe_browser_send_json(
                    websocket,
                    self._room_bridge.get_room_state_event(),
                ):
                    return

            # Permission requests are transport RPCs, not conversation turns.
            # Replay outstanding approvals so a browser that reconnects after
            # the event was emitted still sees the allow/deny callout.
            if self._pending_permission_requests:
                logger.info(
                    "Replaying %d pending permission request(s) to new browser",
                    len(self._pending_permission_requests),
                )
                for permission_request in list(self._pending_permission_requests.values()):
                    if not await self._safe_browser_send_json(websocket, permission_request):
                        return

            # Handle messages from browser
            while True:
                data = await websocket.receive_json()
                logger.debug(
                    "handle_websocket: browser msg: %s",
                    _sanitize_log(json.dumps(data)[:500]),
                )
                try:
                    await self._dispatch_browser_message(data, sender_ws=websocket)
                except Exception as e:
                    logger.exception("Error processing browser message: %s", _sanitize_log(data))
                    await websocket.send_json({"type": "error", "content": str(e)})

        except WebSocketDisconnect:
            logger.info("WebSocket disconnected")
        except Exception as e:
            if _is_expected_ws_disconnect(e):
                logger.info("WebSocket disconnected")
                return
            logger.exception("WebSocket error")
            try:
                await websocket.send_json({"type": "error", "content": str(e)})
            except Exception:
                logger.debug("Failed to send error response to WebSocket", exc_info=True)
        finally:
            self._channels.remove(channel)
            remaining = self._channels.count
            logger.info("Connection closed, remaining channels: %d", remaining)

    async def handle_cli_websocket(self, websocket: WebSocket, session_id: str) -> None:
        """Handle the CLI WebSocket connection at /ws/cli/{session_id}.

        Only used by the SdkWebSocketTransport. The CLI process connects
        back to this endpoint after being spawned with --sdk-url.
        """
        logger.info(
            "handle_cli_websocket: incoming CLI connection for session=%s (transport=%s)",
            _sanitize_log(session_id),
            type(self._transport).__name__ if self._transport else None,
        )

        if not self._transport or not self._transport.capabilities.cli_websocket:
            logger.warning(
                "CLI WebSocket received but transport %s does not support SDK WebSocket protocol",
                type(self._transport).__name__ if self._transport else "None",
            )
            await websocket.close(code=1008, reason="SDK transport not active")
            return

        if session_id != self.session_id:
            logger.warning(
                "CLI WebSocket session mismatch: expected %s, got %s",
                _sanitize_log(self.session_id),
                _sanitize_log(session_id),
            )
            await websocket.close(code=1008, reason="Session ID mismatch")
            return

        logger.info("handle_cli_websocket: attaching CLI websocket to transport")
        await self._transport.attach_cli_websocket(websocket)

        # Block until the receive loop finishes (CLI disconnects)
        logger.info("handle_cli_websocket: waiting for CLI disconnect")
        await self._transport.wait_for_cli_disconnect()
        logger.info("handle_cli_websocket: CLI disconnected, handler returning")

    async def handle_ravn_websocket(self, websocket: WebSocket, peer_id: str) -> None:
        """Handle a Ravn WebSocket connection at /ws/ravn/{peer_id}.

        Accepts NDJSON frames from Ravn daemons and forwards them to the
        RoomBridge for translation and broadcast. Only active when room mode
        is enabled.
        """
        if self._room_bridge is None:
            logger.warning(
                "handle_ravn_websocket: room mode disabled, rejecting peer_id=%s",
                _sanitize_log(peer_id),
            )
            await websocket.close(code=1008, reason="Room mode is not enabled")
            return

        await websocket.accept()
        logger.info("handle_ravn_websocket: Ravn connected peer_id=%s", _sanitize_log(peer_id))

        # Register with peer_id as initial persona; enriched on first frame
        await self._room_bridge.register(
            peer_id=peer_id,
            persona=peer_id,
            websocket=websocket,
        )
        _registered_with_metadata = False

        try:
            while True:
                raw = await websocket.receive_text()
                for line in raw.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        frame = json.loads(line)
                    except json.JSONDecodeError:
                        logger.warning(
                            "handle_ravn_websocket: invalid JSON from peer_id=%s",
                            _sanitize_log(peer_id),
                        )
                        continue

                    # Enrich participant on first frame with persona metadata
                    if not _registered_with_metadata and (
                        frame.get("persona") or frame.get("subscribes_to")
                    ):
                        _registered_with_metadata = True
                        await self._room_bridge.register(
                            peer_id=peer_id,
                            persona=frame.get("persona", peer_id),
                            websocket=websocket,
                            display_name=frame.get("display_name", ""),
                            subscribes_to=frame.get("subscribes_to"),
                            emits=frame.get("emits"),
                            tools=frame.get("tools"),
                        )

                    await self._room_bridge.handle_ravn_frame(peer_id, frame)

        except WebSocketDisconnect:
            logger.info(
                "handle_ravn_websocket: Ravn disconnected peer_id=%s",
                _sanitize_log(peer_id),
            )
        except Exception:
            logger.exception(
                "handle_ravn_websocket: error from peer_id=%s",
                _sanitize_log(peer_id),
            )
        finally:
            await self._room_bridge.unregister(peer_id)


# Global broker instance
broker = Broker()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Attach JWT redaction filter after uvicorn has configured its loggers
    _redact_filter = _TokenRedactFilter()
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).addFilter(_redact_filter)

    await broker.startup()
    yield
    await broker.shutdown()


app = FastAPI(
    title="Skuld Broker",
    description="WebSocket broker for Claude Code CLI",
    version="0.3.0",
    lifespan=lifespan,
)

# Add CORS middleware — browser UI (hlidskjalf) is served from a different
# origin than the per-session Skuld pod, so cross-origin requests to /api/*
# need explicit CORS headers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    """Health check endpoint."""
    return {"status": "healthy", "session_id": broker.session_id}


@app.get("/ready")
async def ready() -> dict:
    """Readiness check endpoint."""
    is_ready = broker._transport is not None
    return {"ready": is_ready, "session_id": broker.session_id}


@app.websocket("/session")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for browser chat."""
    await broker.handle_websocket(websocket)


@app.websocket("/ws/cli/{session_id}")
async def cli_websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    """WebSocket endpoint for Claude Code CLI (via --sdk-url)."""
    await broker.handle_cli_websocket(websocket, session_id)


@app.websocket("/ws/ravn/{peer_id}")
async def ravn_websocket_endpoint(websocket: WebSocket, peer_id: str) -> None:
    """WebSocket endpoint for Ravn daemon connections (room mode)."""
    await broker.handle_ravn_websocket(websocket, peer_id)


# --- Broker Log API ---


@app.get("/api/logs")
async def get_broker_logs(
    lines: int = Query(default=100, ge=1, le=2000),
    level: str = Query(default="DEBUG"),
) -> dict:
    """Return recent broker log entries from the in-memory ring buffer."""
    min_level = getattr(logging, level.upper(), logging.DEBUG)
    filtered = [entry for entry in _log_buffer if logging.getLevelName(entry["level"]) >= min_level]
    tail = list(filtered)[-lines:]
    return {
        "session_id": broker.session_id,
        "total": len(_log_buffer),
        "returned": len(tail),
        "lines": tail,
    }


@app.get("/api/logs/aggregate")
async def get_aggregate_logs(
    lines: int = Query(default=100, ge=1, le=2000),
    level: str = Query(default="DEBUG"),
    participants: str | None = Query(default=None),
    query: str = Query(default=""),
) -> dict:
    """Return interleaved broker, flock, and service logs from the session workspace."""
    requested_participants = (
        {item.strip() for item in participants.split(",") if item.strip()} if participants else None
    )
    payload = aggregate_workspace_logs(
        broker.workspace_dir,
        lines=lines,
        level=level,
        participants=requested_participants,
        query=query,
    )
    if not any(item.get("id") == "skuld" for item in payload.get("available_participants", [])):
        min_level = getattr(logging, level.upper(), logging.DEBUG)
        query_lower = query.casefold()
        broker_lines = []
        for index, entry in enumerate(_log_buffer):
            entry_level = str(entry.get("level") or "INFO").upper()
            entry_level_no = getattr(logging, entry_level, logging.INFO)
            message = str(entry.get("message") or "")
            source = str(entry.get("logger") or "skuld")
            if entry_level_no < min_level:
                continue
            if requested_participants and "skuld" not in requested_participants:
                continue
            if query_lower and not (
                query_lower in "skuld"
                or query_lower in source.casefold()
                or query_lower in message.casefold()
            ):
                continue
            created = entry.get("timestamp")
            timestamp = (
                datetime.fromtimestamp(float(created), tz=UTC)
                if isinstance(created, (int, float))
                else datetime.now(UTC)
            )
            broker_lines.append(
                {
                    "id": f"skuld-buffer-{index}",
                    "timestamp": timestamp.isoformat(),
                    "level": entry_level,
                    "participant": "skuld",
                    "participant_label": "Skuld",
                    "participant_kind": "broker",
                    "source": source,
                    "message": message,
                    "sequence": index,
                    "stream": "memory",
                }
            )

        if _log_buffer:
            payload.setdefault("available_participants", []).insert(
                0,
                {"id": "skuld", "label": "Skuld", "kind": "broker"},
            )
        if broker_lines:
            all_lines = [*payload.get("lines", []), *broker_lines]
            all_lines.sort(key=lambda item: (str(item.get("timestamp") or ""), item.get("id", "")))
            payload["lines"] = all_lines[-lines:]
            payload["returned"] = len(payload["lines"])
            payload["filtered"] = int(payload.get("filtered") or 0) + len(broker_lines)
        payload["total"] = int(payload.get("total") or 0) + len(_log_buffer)
    payload["session_id"] = broker.session_id
    return payload


# --- Conversation History API ---


@app.get("/api/conversation/history")
async def get_conversation_history() -> dict:
    """Return the full conversation history with activity state."""
    is_active = (
        broker._transport is not None
        and broker._transport.is_alive
        and bool(broker._pending_assistant_content or broker._pending_reasoning_text)
    )
    # Build a short activity description for the UI
    last_activity = ""
    if is_active:
        if broker._pending_assistant_content:
            # Last ~100 chars of what the assistant is writing
            last_activity = broker._pending_assistant_content[-100:]
        elif broker._pending_reasoning_text:
            last_activity = "Thinking..."

    def _serialize_turn(turn: ConversationTurn) -> dict:
        d = asdict(turn)
        # Omit optional participant fields when absent to keep JSON backward-compatible
        if d["participant_id"] is None:
            del d["participant_id"]
        if d["participant_meta"] is None:
            del d["participant_meta"]
        if d["thread_id"] is None:
            del d["thread_id"]
        # Always include visibility so clients can rely on it being present
        return d

    return {
        "turns": [_serialize_turn(t) for t in broker._conversation_turns],
        "is_active": is_active,
        "last_activity": last_activity,
    }


# --- Capabilities API ---


class _SlashCommandRequest(BaseModel):
    """Request body for sending a slash command to the active transport."""

    command: str
    arguments: str = ""
    pane_id: str = ""


@app.get("/api/capabilities")
async def get_capabilities() -> dict:
    """Return transport capabilities so the frontend knows which controls to render."""
    if not broker._transport:
        raise HTTPException(status_code=503, detail="Transport not initialized")
    return asdict(broker._transport.capabilities)


@app.get("/api/slash-commands")
async def get_slash_commands(refresh: bool = Query(True)) -> dict[str, Any]:
    """Return slash commands available in the active CLI session."""
    if not broker._transport:
        raise HTTPException(status_code=503, detail="Transport not initialized")
    if not broker._transport.capabilities.slash_commands:
        raise HTTPException(status_code=501, detail="Slash commands not supported")
    commands = await broker.discover_slash_commands(refresh=refresh)
    return {"commands": commands, "count": len(commands)}


@app.post("/api/slash-commands/send")
async def send_slash_command(body: _SlashCommandRequest) -> dict[str, str]:
    """Send a slash command to the active CLI session as terminal input."""
    if not broker._transport:
        raise HTTPException(status_code=503, detail="Transport not initialized")
    if not broker._transport.capabilities.slash_commands:
        raise HTTPException(status_code=501, detail="Slash commands not supported")
    command = body.command.strip()
    if not command:
        raise HTTPException(status_code=400, detail="Command is required")
    await broker._transport.send_control(
        "slash_command",
        command=command,
        arguments=body.arguments,
        pane_id=body.pane_id,
    )
    name = command.split(maxsplit=1)[0]
    if not name.startswith("/"):
        name = f"/{name}"
    return {"status": "sent", "command": name}


@app.post("/api/claude/hooks")
async def receive_claude_hook(payload: dict[str, Any]) -> dict[str, bool]:
    """Receive Claude Code HTTP hook callbacks for interactive tmux sessions."""
    await broker.handle_claude_hook(payload)
    return {"ok": True}


# --- Service Management API ---


@app.post("/api/services", response_model=ServiceStatus)
async def create_service(request: ServiceCreateRequest) -> ServiceStatus:
    """Start a new local service."""
    if not broker.service_manager:
        raise HTTPException(status_code=503, detail="Service manager not initialized")
    return await broker.service_manager.add_service(request)


@app.get("/api/services", response_model=list[ServiceStatus])
async def list_services() -> list[ServiceStatus]:
    """List all local services."""
    if not broker.service_manager:
        raise HTTPException(status_code=503, detail="Service manager not initialized")
    return await broker.service_manager.list_services()


@app.get("/api/services/{name}", response_model=ServiceStatus)
async def get_service(name: str) -> ServiceStatus:
    """Get status of a specific service."""
    if not broker.service_manager:
        raise HTTPException(status_code=503, detail="Service manager not initialized")

    result = await broker.service_manager.get_service(name)
    if not result:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    return result


@app.delete("/api/services/{name}")
async def delete_service(name: str) -> dict:
    """Stop and remove a local service."""
    if not broker.service_manager:
        raise HTTPException(status_code=503, detail="Service manager not initialized")

    removed = await broker.service_manager.remove_service(name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    return {"status": "removed", "name": name}


@app.get("/api/services/{name}/logs")
async def get_service_logs(name: str, lines: int = 100) -> dict:
    """Get logs for a service."""
    if not broker.service_manager:
        raise HTTPException(status_code=503, detail="Service manager not initialized")

    logs = await broker.service_manager.get_logs(name, lines=lines)
    if logs is None:
        raise HTTPException(status_code=404, detail=f"No logs found for service '{name}'")
    return {"name": name, "lines": lines, "logs": logs}


@app.post("/api/services/{name}/restart", response_model=ServiceStatus)
async def restart_service(name: str) -> ServiceStatus:
    """Restart a local service."""
    if not broker.service_manager:
        raise HTTPException(status_code=503, detail="Service manager not initialized")

    result = await broker.service_manager.restart_service(name)
    if not result:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    return result


# --- Workspace File Listing API ---

# Directories that add noise and should be hidden from the file browser
_SKIP_NAMES = frozenset(
    {
        "node_modules",
        "__pycache__",
        ".git",
        "venv",
        ".venv",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
    }
)

# Dotfiles/dotdirs that *should* be shown despite the general hidden-file rule
_SHOW_HIDDEN = frozenset({".github", ".claude", ".vscode"})


def _resolve_root(root: str) -> Path:
    """Return the resolved base directory for the given root name."""
    if root == "home":
        return Path(broker._settings.home_path).resolve()
    return Path(broker.workspace_dir).resolve()


def _sanitize_relative(relative_path: str) -> str:
    """Sanitize user-supplied path: reject NUL, normalize, reject traversal/absolute."""
    if "\0" in relative_path:
        raise HTTPException(400, "Invalid path")
    normalised = os.path.normpath(relative_path)
    # Disallow absolute paths
    if os.path.isabs(normalised):
        raise HTTPException(400, "Path traversal not allowed")
    # Disallow parent-directory references that could escape the base directory
    # (for example "foo/../../etc/passwd").
    parts = [p for p in normalised.split(os.sep) if p not in (".", "")]
    if any(part == os.pardir for part in parts):
        raise HTTPException(400, "Path traversal not allowed")
    return normalised


def _check_within_base(base: str | Path, target: str | Path) -> None:
    """Raise if *target* is not under *base*.

    Uses ``os.path.realpath`` + ``str.startswith`` so that CodeQL
    recognises the guard as a path-injection sanitiser.
    """
    base_real = os.path.realpath(str(base))
    target_real = os.path.realpath(str(target))
    if target_real != base_real and not target_real.startswith(base_real + os.sep):
        raise HTTPException(400, "Path traversal not allowed")


def _validate_root(root: str) -> None:
    """Validate root parameter is exactly 'workspace' or 'home'."""
    if root not in ("workspace", "home"):
        raise HTTPException(400, "root must be 'workspace' or 'home'")


@app.get("/api/files")
async def list_files(path: str = "", root: str = "workspace") -> dict:
    """List files and directories in a session root (workspace or home)."""
    _validate_root(root)
    base = _resolve_root(root)
    sanitized = _sanitize_relative(path)
    base_real = os.path.realpath(str(base))
    target_real = os.path.realpath(os.path.join(base_real, sanitized))
    _check_within_base(base_real, target_real)
    target = Path(target_real)

    if not target.is_dir():
        raise HTTPException(404, "Directory not found")

    try:
        items = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except PermissionError:
        raise HTTPException(403, "Permission denied")

    entries: list[dict] = []
    for item in items:
        if item.name.startswith(".") and item.name not in _SHOW_HIDDEN:
            continue
        if item.name in _SKIP_NAMES:
            continue
        stat = item.stat(follow_symlinks=False)
        entries.append(
            {
                "name": item.name,
                "path": str(item.relative_to(base)),
                "type": "directory" if item.is_dir() else "file",
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
            }
        )

    return {"entries": entries}


@app.get("/api/files/download")
async def download_file(path: str, root: str = "workspace") -> FileResponse:
    """Download a single file from the session."""
    _validate_root(root)
    base = _resolve_root(root)
    sanitized = _sanitize_relative(path)
    base_real = os.path.realpath(str(base))
    target_real = os.path.realpath(os.path.join(base_real, sanitized))
    _check_within_base(base_real, target_real)

    if not os.path.isfile(target_real):
        raise HTTPException(404, "File not found")

    return FileResponse(
        path=target_real,
        filename=os.path.basename(target_real),
        media_type="application/octet-stream",
    )


@app.post("/api/files/upload")
async def upload_files(
    files: list[UploadFile],
    path: str = "",
    root: str = "workspace",
) -> dict:
    """Upload files to a target directory in the session."""
    _validate_root(root)
    base = _resolve_root(root)
    sanitized = _sanitize_relative(path)
    base_real = os.path.realpath(str(base))
    dir_real = os.path.realpath(os.path.join(base_real, sanitized))
    _check_within_base(base_real, dir_real)

    if not os.path.isdir(dir_real):
        raise HTTPException(404, "Target directory not found")

    max_size = broker._settings.max_upload_size_bytes
    uploaded: list[dict] = []
    for upload in files:
        if upload.filename is None:
            continue
        # Prevent path traversal in filenames
        safe_name = os.path.basename(upload.filename)
        dest_real = os.path.realpath(os.path.join(dir_real, safe_name))
        _check_within_base(base_real, dest_real)

        content = await upload.read()
        if len(content) > max_size:
            raise HTTPException(
                413,
                f"File {safe_name} exceeds maximum upload size ({max_size} bytes)",
            )
        with open(dest_real, "wb") as f:
            f.write(content)
        stat = os.stat(dest_real)
        uploaded.append(
            {
                "name": safe_name,
                "path": os.path.relpath(dest_real, base_real),
                "type": "file",
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
            }
        )

    return {"entries": uploaded}


@app.put("/api/files/upload")
async def upload_file_raw(
    request: Request,
    path: str,
    root: str = "workspace",
) -> dict:
    """Write raw request-body bytes to a single workspace-relative file.

    Raw-body mirror of ``download_file``: ``path`` is the destination *file*
    (not a directory), confined identically (reject absolute/.. + realpath guard).
    """
    _validate_root(root)
    base = _resolve_root(root)
    sanitized = _sanitize_relative(path)
    base_real = os.path.realpath(str(base))
    # Containment checks are inlined (not _check_within_base) so the
    # realpath + startswith barrier is visible to CodeQL's path-injection
    # query in the same dataflow scope as the filesystem sinks below. The
    # bare startswith(base_real) is the shape the query recognises; the
    # separator-anchored check after it rules out /base vs /base-evil
    # prefix collisions.
    target_real = os.path.realpath(os.path.join(base_real, sanitized))
    if not target_real.startswith(base_real):
        raise HTTPException(400, "Path traversal not allowed")
    if target_real != base_real and not target_real.startswith(base_real + os.sep):
        raise HTTPException(400, "Path traversal not allowed")
    if sanitized in ("", ".") or os.path.isdir(target_real):
        raise HTTPException(400, "path must name a file, not a directory")

    body = await request.body()
    max_size = broker._settings.max_upload_size_bytes
    if len(body) > max_size:
        raise HTTPException(
            413,
            f"File exceeds maximum upload size ({max_size} bytes)",
        )

    parent_real = os.path.realpath(os.path.dirname(target_real))
    if not parent_real.startswith(base_real):
        raise HTTPException(400, "Path traversal not allowed")
    if parent_real != base_real and not parent_real.startswith(base_real + os.sep):
        raise HTTPException(400, "Path traversal not allowed")
    os.makedirs(parent_real, exist_ok=True)

    with open(target_real, "wb") as f:
        f.write(body)

    stat = os.stat(target_real)
    return {
        "name": os.path.basename(target_real),
        "path": os.path.relpath(target_real, base_real),
        "type": "file",
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
    }


class MkdirRequest(BaseModel):
    path: str
    root: str = "workspace"


@app.post("/api/files/mkdir")
async def mkdir(body: MkdirRequest) -> dict:
    """Create a directory."""
    _validate_root(body.root)
    base = _resolve_root(body.root)
    sanitized = _sanitize_relative(body.path)
    base_real = os.path.realpath(str(base))
    target_real = os.path.realpath(os.path.join(base_real, sanitized))
    if not target_real.startswith(base_real):
        raise HTTPException(400, "Path traversal not allowed")
    if target_real != base_real and not target_real.startswith(base_real + os.sep):
        raise HTTPException(400, "Path traversal not allowed")

    if os.path.exists(target_real):
        raise HTTPException(409, "Path already exists")

    try:
        os.makedirs(target_real, exist_ok=False)
    except PermissionError:
        raise HTTPException(403, "Permission denied")

    stat = os.stat(target_real)
    return {
        "name": os.path.basename(target_real),
        "path": os.path.relpath(target_real, base_real),
        "type": "directory",
        "size": stat.st_size,
        "modified": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
    }


@app.delete("/api/files")
async def delete_file(path: str, root: str = "workspace") -> dict:
    """Delete a file or directory."""
    _validate_root(root)
    if not path:
        raise HTTPException(400, "Cannot delete root directory")
    base = _resolve_root(root)
    sanitized = _sanitize_relative(path)
    base_real = os.path.realpath(str(base))
    target_real = os.path.realpath(os.path.join(base_real, sanitized))
    _check_within_base(base_real, target_real)

    if not os.path.exists(target_real):
        raise HTTPException(404, "Path not found")

    try:
        if os.path.isdir(target_real):
            shutil.rmtree(target_real)
            return {"deleted": os.path.relpath(target_real, base_real)}
        os.unlink(target_real)
    except PermissionError:
        raise HTTPException(403, "Permission denied")

    return {"deleted": os.path.relpath(target_real, base_real)}


def _parse_diff_output(raw: str, file_path: str) -> dict:
    """Parse unified diff output into structured hunks."""
    hunks: list[dict] = []
    current_hunk: dict | None = None

    for line in raw.splitlines():
        # Hunk header: @@ -oldStart,oldCount +newStart,newCount @@
        if line.startswith("@@"):
            parts = line.split("@@")
            if len(parts) < 2:
                continue
            header = parts[1].strip()
            tokens = header.split()
            old_start, old_count = 0, 0
            new_start, new_count = 0, 0
            for token in tokens:
                if token.startswith("-"):
                    nums = token[1:].split(",")
                    old_start = int(nums[0])
                    old_count = int(nums[1]) if len(nums) > 1 else 1
                elif token.startswith("+"):
                    nums = token[1:].split(",")
                    new_start = int(nums[0])
                    new_count = int(nums[1]) if len(nums) > 1 else 1
            current_hunk = {
                "oldStart": old_start,
                "oldCount": old_count,
                "newStart": new_start,
                "newCount": new_count,
                "lines": [],
            }
            hunks.append(current_hunk)
            continue

        if current_hunk is None:
            continue

        if line.startswith("+"):
            current_hunk["lines"].append(
                {
                    "type": "add",
                    "content": line[1:],
                    "newLine": new_start,
                }
            )
            new_start += 1
        elif line.startswith("-"):
            current_hunk["lines"].append(
                {
                    "type": "remove",
                    "content": line[1:],
                    "oldLine": old_start,
                }
            )
            old_start += 1
        elif line.startswith(" "):
            current_hunk["lines"].append(
                {
                    "type": "context",
                    "content": line[1:],
                    "oldLine": old_start,
                    "newLine": new_start,
                }
            )
            old_start += 1
            new_start += 1

    return {"filePath": file_path, "hunks": hunks}


@app.get("/api/diff")
async def get_diff(
    file: str = Query(..., description="File path relative to workspace"),
    base: str = Query(
        default="last-commit",
        description="Diff base: last-commit or default-branch",
    ),
) -> dict:
    """Return parsed git diff for a single file."""
    workspace = _resolve_git_workspace_root(broker.workspace_dir)
    target = (workspace / file).resolve()

    if not str(target).startswith(str(workspace)):
        raise HTTPException(400, "Path traversal not allowed")

    if base == "last-commit":
        cmd = ["git", "diff", "HEAD", "--", file]
    elif base == "default-branch":
        cmd = ["git", "diff", "main...HEAD", "--", file]
    else:
        raise HTTPException(400, f"Invalid base: {base}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except TimeoutError:
        raise HTTPException(504, "git diff timed out")

    if proc.returncode not in (0, 1):
        detail = stderr.decode(errors="replace").strip()
        logger.warning("git diff failed for %s: %s", _sanitize_log(file), _sanitize_log(detail))
        raise HTTPException(502, f"git diff failed: {detail}")

    raw = stdout.decode(errors="replace")
    return _parse_diff_output(raw, file)


@app.get("/api/diff/files")
async def get_diff_files(
    base: str = Query(
        default="last-commit",
        description="Diff base: last-commit or default-branch",
    ),
) -> dict:
    """Return list of changed files with insertion/deletion counts."""
    workspace = _resolve_git_workspace_root(broker.workspace_dir)

    if base == "last-commit":
        cmd = ["git", "diff", "HEAD", "--numstat"]
    elif base == "default-branch":
        cmd = ["git", "diff", "main...HEAD", "--numstat"]
    else:
        raise HTTPException(400, f"Invalid base: {base}")

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(workspace),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except TimeoutError:
        raise HTTPException(504, "git diff timed out")

    if proc.returncode not in (0, 1):
        detail = stderr.decode(errors="replace").strip()
        logger.warning("git diff --numstat failed: %s", detail)
        raise HTTPException(502, f"git diff failed: {detail}")

    files = []
    for line in stdout.decode(errors="replace").strip().splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        ins_str, del_str, path = parts
        ins = int(ins_str) if ins_str != "-" else 0
        del_ = int(del_str) if del_str != "-" else 0
        files.append({"path": path, "status": "mod", "ins": ins, "del": del_})

    return {"files": files}


class _SendMessageRequest(BaseModel):
    """Request body for session-to-session messaging."""

    session_id: str
    content: str


class _RoomMessageRequest(BaseModel):
    """Request body for a human message entering a room session."""

    content: str
    source: str = "external"
    participant_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class _DirectedRoomMessageRequest(BaseModel):
    """Request body for a directed human message entering a room session."""

    target_peer_id: str
    content: str
    source: str = "external"
    participant_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class _RoomJoinRequest(BaseModel):
    """Request body for joining a live Environment room."""

    participant_id: str
    display_name: str = ""
    environment_id: str
    role: str = "observer"
    room_id: str = ""
    capabilities: list[str] = Field(default_factory=list)
    surfaces: list[str] = Field(default_factory=lambda: ["skuld.room"])
    environment_action_authorities: list[str] = Field(default_factory=list)


class _RoomHeartbeatRequest(BaseModel):
    """Request body for human Environment heartbeat/state updates."""

    participant_id: str
    status: str | None = None
    wakefulness: str | None = None
    attention_state: str | None = None


class _RoomLeaveRequest(BaseModel):
    """Request body for leaving a live Environment room."""

    participant_id: str
    reason: str = "left"


class _RoomCloseRequest(BaseModel):
    """Request body for closing a live Environment huddle."""

    room_id: str
    reason: str = "closed"
    summary: str = ""


class _RoomCapabilityRequest(BaseModel):
    """Request body for verifying a participant control capability."""

    participant_id: str
    capability: str


class _WorkflowGateResolveRequest(BaseModel):
    """Request body for resolving a pending workflow gate."""

    decision: str
    notes: str = ""
    source: str = "human"


@app.post("/api/message")
async def send_message_to_session(body: _SendMessageRequest) -> dict:
    """Send a message to another session via Volundr's WS proxy.

    The broker injects its own auth token so the calling session
    never sees credentials.
    """
    if not broker.volundr_api_url:
        raise HTTPException(503, "Volundr API URL not configured")

    client = await broker._get_http_client()
    url = f"{FORGE_SESSIONS_PATH}/{body.session_id}/messages"
    try:
        resp = await client.post(url, json={"content": body.content})
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(e.response.status_code, f"Volundr error: {e.response.text[:500]}")
    except httpx.RequestError as e:
        raise HTTPException(502, f"Failed to reach Volundr: {e}")

    return {"status": "sent", "target_session_id": body.session_id}


@app.post("/api/room/message")
async def send_room_message(body: _RoomMessageRequest) -> dict:
    """Inject a human-originated message into the active room session."""
    try:
        message_id = await broker.handle_human_room_message(
            body.content,
            source=body.source,
            participant_id=body.participant_id,
            metadata=body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))

    return {"status": "sent", "message_id": message_id}


@app.post("/api/room/direct")
async def send_directed_room_message(body: _DirectedRoomMessageRequest) -> dict:
    """Inject a human-originated directed room message."""
    try:
        message_id = await broker.handle_directed_room_message(
            body.target_peer_id,
            body.content,
            source=body.source,
            metadata={**body.metadata, "participant_id": body.participant_id}
            if body.participant_id
            else body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))

    return {"status": "sent", "message_id": message_id}


@app.post("/api/room/join")
async def join_room(body: _RoomJoinRequest) -> dict:
    """Join a human participant to a live Valkyrie Environment."""
    try:
        participant = await broker.join_human_environment(
            participant_id=body.participant_id,
            display_name=body.display_name,
            environment_id=body.environment_id,
            role=body.role,
            room_id=body.room_id,
            capabilities=body.capabilities or None,
            surfaces=body.surfaces or None,
            environment_action_authorities=body.environment_action_authorities,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))

    return {"status": "joined", "participant": participant}


@app.post("/api/room/heartbeat")
async def heartbeat_room(body: _RoomHeartbeatRequest) -> dict:
    """Update human participant presence in a live Valkyrie Environment."""
    try:
        participant = await broker.heartbeat_human_environment(
            participant_id=body.participant_id,
            status=body.status,
            wakefulness=body.wakefulness,
            attention_state=body.attention_state,
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))

    return {"status": "ok", "participant": participant}


@app.post("/api/room/leave")
async def leave_room(body: _RoomLeaveRequest) -> dict:
    """Leave a human participant from a live Valkyrie Environment."""
    try:
        await broker.leave_human_environment(
            participant_id=body.participant_id,
            reason=body.reason,
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))

    return {"status": "left", "participant_id": body.participant_id}


@app.post("/api/room/close")
async def close_room(body: _RoomCloseRequest) -> dict:
    """Close a live Environment huddle, publishing its transcript for archival."""
    try:
        closed = await broker.close_environment_huddle(
            room_id=body.room_id,
            reason=body.reason,
            summary=body.summary,
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    return {"status": "closed", **closed}


@app.post("/api/room/require-capability")
async def require_room_capability(body: _RoomCapabilityRequest) -> dict:
    """Verify a joined participant holds a control capability (403 otherwise)."""
    try:
        broker.require_room_capability(body.participant_id, body.capability)
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    return {"status": "ok", "participant_id": body.participant_id, "capability": body.capability}


@app.get("/api/room/participants")
async def get_room_participants(environment_id: str | None = Query(default=None)) -> dict:
    """Return the current participants in the active room session."""
    return {"participants": broker.get_room_participants(environment_id=environment_id)}


@app.get("/api/workflow/gates")
async def get_workflow_gates() -> dict:
    """Return native workflow gate states for the active session."""
    return {"gates": broker.list_workflow_gates()}


@app.post("/api/workflow/gates/{gate_id}/resolve")
async def resolve_workflow_gate(
    request: Request,
    gate_id: str,
    body: _WorkflowGateResolveRequest,
) -> dict:
    """Resolve a pending native workflow gate and publish its outcome."""
    if (
        request.headers.get(WORKFLOW_GATE_INTENT_HEADER, "").strip().lower()
        != WORKFLOW_GATE_INTENT_RESOLVE
    ):
        raise HTTPException(428, "Missing explicit workflow gate intent header")
    try:
        gate = await broker.resolve_workflow_gate(
            gate_id,
            body.decision,
            notes=body.notes,
            source=body.source,
        )
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except RuntimeError as exc:
        raise HTTPException(503, str(exc))

    return {"status": "resolved", "gate": gate}


@app.get("/api/communication/routes")
async def get_communication_routes() -> dict:
    """Return active external communication routes for the live session."""
    return {"routes": broker.get_communication_routes()}


class _TokenRedactFilter(logging.Filter):
    """Redact access_token values from log messages to prevent JWT leaks."""

    _pattern = re.compile(r"access_token=[^\s\"&]+")

    def _redact(self, value: object) -> object:
        if isinstance(value, str):
            return self._pattern.sub("access_token=[REDACTED]", value)
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        if hasattr(record, "msg"):
            record.msg = self._redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(self._redact(arg) for arg in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: self._redact(value) for key, value in record.args.items()}
        return True


def main() -> None:
    """Run the broker server."""
    import uvicorn

    settings = SkuldSettings()
    logger.info("Starting Skuld broker on %s:%d", settings.host, settings.port)
    uvicorn.run(app, host=settings.host, port=settings.port, access_log=False)


if __name__ == "__main__":
    main()
