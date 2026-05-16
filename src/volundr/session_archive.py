"""Filesystem-backed session archive helpers shared by Volundr and Skuld."""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ARCHIVE_VERSION = 1
DEFAULT_WORKSPACE_ARCHIVE_DIR = Path(".volundr") / "archive"
DEFAULT_CONFIG_ARCHIVE_DIR = Path("archives")
TRANSCRIPT_DIR = Path(".skuld")


def config_root_dir() -> Path:
    """Return the runtime config root used for config-scoped archive paths."""
    raw = os.environ.get("NIUU_HOME", "~/.niuu")
    return Path(raw).expanduser()


def archive_root(
    workspace_dir: str | Path | None,
    *,
    session_id: str | None = None,
    archive_location: str = "workspace",
    archive_path: str | Path | None = None,
) -> Path:
    """Return the archive root for a session."""
    if archive_location == "workspace":
        if workspace_dir is None:
            raise ValueError("workspace_dir is required for workspace-scoped archives")
        configured = Path(archive_path) if archive_path else DEFAULT_WORKSPACE_ARCHIVE_DIR
        if configured.is_absolute():
            return configured.expanduser()
        return Path(workspace_dir) / configured

    if archive_location == "config":
        configured = Path(archive_path) if archive_path else DEFAULT_CONFIG_ARCHIVE_DIR
        if configured.is_absolute():
            base = configured.expanduser()
        else:
            base = config_root_dir() / configured
        if not session_id:
            raise ValueError("session_id is required for config-scoped archives")
        return base / session_id

    raise ValueError(f"Unsupported archive location: {archive_location}")


def transcript_source_path(workspace_dir: str | Path, session_id: str) -> Path:
    """Return the persisted transcript JSON path for a session."""
    return Path(workspace_dir) / TRANSCRIPT_DIR / f"conversation_{session_id}.json"


def archive_manifest_path(
    workspace_dir: str | Path | None,
    *,
    session_id: str | None = None,
    archive_location: str = "workspace",
    archive_path: str | Path | None = None,
) -> Path:
    """Return the archive manifest path."""
    return (
        archive_root(
            workspace_dir,
            session_id=session_id,
            archive_location=archive_location,
            archive_path=archive_path,
        )
        / "manifest.json"
    )


def archive_transcript_json_path(
    workspace_dir: str | Path | None,
    *,
    session_id: str | None = None,
    archive_location: str = "workspace",
    archive_path: str | Path | None = None,
) -> Path:
    """Return the archived transcript JSON path."""
    return (
        archive_root(
            workspace_dir,
            session_id=session_id,
            archive_location=archive_location,
            archive_path=archive_path,
        )
        / "transcript.json"
    )


def archive_transcript_markdown_path(
    workspace_dir: str | Path | None,
    *,
    session_id: str | None = None,
    archive_location: str = "workspace",
    archive_path: str | Path | None = None,
) -> Path:
    """Return the archived transcript Markdown path."""
    return (
        archive_root(
            workspace_dir,
            session_id=session_id,
            archive_location=archive_location,
            archive_path=archive_path,
        )
        / "transcript.md"
    )


def load_workspace_transcript(workspace_dir: str | Path, session_id: str) -> dict[str, Any]:
    """Load the persisted workspace transcript in API response shape."""
    path = transcript_source_path(workspace_dir, session_id)
    if not path.exists():
        return {"turns": [], "is_active": False, "last_activity": ""}

    data = _read_json(path)
    return _normalise_transcript_payload(data)


def load_archive_transcript(
    workspace_dir: str | Path | None,
    *,
    session_id: str | None = None,
    archive_location: str = "workspace",
    archive_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Load the normalized archived transcript if present."""
    try:
        path = archive_transcript_json_path(
            workspace_dir,
            session_id=session_id,
            archive_location=archive_location,
            archive_path=archive_path,
        )
    except ValueError:
        return None
    if not path.exists():
        return None
    data = _read_json(path)
    return _normalise_transcript_payload(data)


def load_archive_manifest(
    workspace_dir: str | Path | None,
    *,
    session_id: str | None = None,
    archive_location: str = "workspace",
    archive_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Load an archive manifest if present."""
    try:
        path = archive_manifest_path(
            workspace_dir,
            session_id=session_id,
            archive_location=archive_location,
            archive_path=archive_path,
        )
    except ValueError:
        return None
    if not path.exists():
        return None
    return _read_json(path)


def archive_logs_aggregate_path(
    workspace_dir: str | Path | None,
    *,
    session_id: str | None = None,
    archive_location: str = "workspace",
    archive_path: str | Path | None = None,
) -> Path:
    """Return the archived aggregated logs path."""
    return (
        archive_root(
            workspace_dir,
            session_id=session_id,
            archive_location=archive_location,
            archive_path=archive_path,
        )
        / "logs"
        / "aggregate.json"
    )


def load_archive_logs(
    workspace_dir: str | Path | None,
    *,
    session_id: str | None = None,
    archive_location: str = "workspace",
    archive_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Load archived aggregate logs if present."""
    try:
        path = archive_logs_aggregate_path(
            workspace_dir,
            session_id=session_id,
            archive_location=archive_location,
            archive_path=archive_path,
        )
    except ValueError:
        return None
    if not path.exists():
        return None
    return _read_json(path)


def render_transcript_markdown(payload: dict[str, Any]) -> str:
    """Render transcript turns to a simple Markdown transcript."""
    turns = payload.get("turns", [])
    lines = ["# Session Transcript", ""]
    if not turns:
        lines.append("_No conversation turns recorded._")
        lines.append("")
        return "\n".join(lines)

    for turn in turns:
        role = str(turn.get("role", "unknown")).strip() or "unknown"
        created_at = str(turn.get("created_at", "")).strip()
        header = f"## {role.title()}"
        if created_at:
            header = f"{header} ({created_at})"
        lines.append(header)
        lines.append("")
        content = str(turn.get("content", ""))
        lines.append(content if content else "_(empty)_")
        lines.append("")
    return "\n".join(lines)


def write_session_archive(
    *,
    session_id: str,
    workspace_dir: str | Path,
    transcript_payload: dict[str, Any],
    aggregated_logs: dict[str, Any],
    archive_location: str = "workspace",
    archive_path: str | Path | None = None,
    chronicle_payload: dict[str, Any] | None = None,
    timeline_payload: dict[str, Any] | None = None,
    event_source_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Write a normalized archive snapshot into the workspace."""
    workspace = Path(workspace_dir)
    root = archive_root(
        workspace,
        session_id=session_id,
        archive_location=archive_location,
        archive_path=archive_path,
    )
    root.mkdir(parents=True, exist_ok=True)

    _write_json(root / "transcript.json", transcript_payload)
    (root / "transcript.md").write_text(
        render_transcript_markdown(transcript_payload),
        encoding="utf-8",
    )
    _write_json(root / "logs" / "aggregate.json", aggregated_logs)

    if chronicle_payload is not None:
        _write_json(root / "chronicle.json", chronicle_payload)
    if timeline_payload is not None:
        _write_json(root / "timeline.json", timeline_payload)

    raw_logs = _copy_workspace_logs(workspace, root / "logs" / "raw")
    raw_events = _copy_event_streams(event_source_dir, root / "events" / "claude-jsonl")

    manifest = {
        "version": ARCHIVE_VERSION,
        "session_id": session_id,
        "created_at": datetime.now(UTC).isoformat(),
        "location": archive_location,
        "archive_root": str(root),
        "artifacts": {
            "transcript_json": "transcript.json",
            "transcript_md": "transcript.md",
            "logs_aggregate": "logs/aggregate.json",
            "chronicle": "chronicle.json" if chronicle_payload is not None else None,
            "timeline": "timeline.json" if timeline_payload is not None else None,
        },
        "counts": {
            "turns": len(transcript_payload.get("turns", [])),
            "log_lines": len(aggregated_logs.get("lines", [])),
            "raw_logs": len(raw_logs),
            "raw_event_streams": len(raw_events),
        },
        "sources": {
            "workspace_transcript": (
                str(transcript_source_path(workspace, session_id).relative_to(workspace))
                if transcript_source_path(workspace, session_id).exists()
                else None
            ),
            "workspace_logs": raw_logs,
            "event_streams": raw_events,
        },
    }
    _write_json(root / "manifest.json", manifest)
    return manifest


def _copy_workspace_logs(workspace: Path, destination_root: Path) -> list[str]:
    copied: list[str] = []
    for source, relative in _workspace_log_sources(workspace):
        target = destination_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.append(str((Path("logs") / "raw" / relative).as_posix()))
    return copied


def _copy_event_streams(
    event_source_dir: str | Path | None,
    destination_root: Path,
) -> list[str]:
    if event_source_dir is None:
        return []
    source_dir = Path(event_source_dir)
    if not source_dir.is_dir():
        return []

    copied: list[str] = []
    destination_root.mkdir(parents=True, exist_ok=True)
    for source in sorted(source_dir.glob("*.jsonl")):
        target = destination_root / source.name
        shutil.copy2(source, target)
        copied.append(str((Path("events") / "claude-jsonl" / source.name).as_posix()))
    return copied


def _workspace_log_sources(workspace: Path) -> list[tuple[Path, Path]]:
    sources: list[tuple[Path, Path]] = []
    skuld_log = workspace / ".skuld.log"
    if skuld_log.is_file():
        sources.append((skuld_log, Path("skuld.log")))

    flock_logs = workspace / ".flock" / "logs"
    if flock_logs.is_dir():
        for path in sorted(flock_logs.glob("*.log")):
            sources.append((path, Path("flock") / path.name))

    service_logs = workspace / ".services" / "logs"
    if service_logs.is_dir():
        for path in sorted(service_logs.glob("*.log")):
            sources.append((path, Path("services") / path.name))
    return sources


def _read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _normalise_transcript_payload(data: dict[str, Any]) -> dict[str, Any]:
    turns = data.get("turns", [])
    if not isinstance(turns, list):
        turns = []
    return {"turns": turns, "is_active": False, "last_activity": ""}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
