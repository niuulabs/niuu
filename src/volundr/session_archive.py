"""Filesystem-backed session archive helpers shared by Volundr and Skuld."""

from __future__ import annotations

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

ARCHIVE_VERSION = 1
DEFAULT_WORKSPACE_ARCHIVE_DIR = Path(".volundr") / "archive"
DEFAULT_CONFIG_ARCHIVE_DIR = Path("archives")
TRANSCRIPT_DIR = Path(".skuld")


class ArchivePathError(ValueError):
    """Raised when an archive path fails traversal or containment validation."""


def resolve_contained_path(
    root: str | Path,
    candidate: str | Path,
    *,
    strict: bool = False,
) -> Path:
    """Resolve ``candidate`` and require it to remain below ``root``.

    Resolving both paths before comparing them rejects lexical traversal and
    symlink escapes. Callers must use the returned path for the filesystem
    operation so the validated path, rather than unchecked input, reaches the
    sink.
    """
    candidate_value = os.path.expanduser(os.fspath(candidate))
    candidate_parts = candidate_value.replace("\\", "/").split("/")
    if not os.path.isabs(candidate_value) and ".." in candidate_parts:
        raise ArchivePathError(f"Path contains traversal: {candidate}")
    root_path = os.path.realpath(os.path.expanduser(os.fspath(root)), strict=strict)
    joined_path = os.path.join(root_path, candidate_value)
    resolved_candidate = os.path.realpath(joined_path, strict=strict)
    root_prefix = root_path.rstrip(os.sep) + os.sep
    if resolved_candidate != root_path and not resolved_candidate.startswith(root_prefix):
        raise ArchivePathError(f"Path escapes configured root: {candidate}")
    return Path(resolved_candidate)


def resolve_archive_member_path(root: str | Path, member_name: str) -> Path:
    """Resolve an untrusted ZIP/TAR member name below an extraction root."""
    posix_member = PurePosixPath(member_name.replace("\\", "/"))
    windows_member = PureWindowsPath(member_name)
    if posix_member.is_absolute() or windows_member.is_absolute():
        raise ArchivePathError(f"Archive member path must be relative: {member_name}")
    if ".." in posix_member.parts or ".." in windows_member.parts:
        raise ArchivePathError(f"Archive member path contains traversal: {member_name}")
    return resolve_contained_path(root, Path(*posix_member.parts))


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
            raise ValueError("Workspace archive path must be relative to the workspace")
        return resolve_contained_path(workspace_dir, configured)

    if archive_location == "config":
        configured = Path(archive_path) if archive_path else DEFAULT_CONFIG_ARCHIVE_DIR
        if configured.is_absolute():
            base = configured.expanduser().resolve()
        else:
            base = resolve_contained_path(config_root_dir(), configured)
        if not session_id:
            raise ValueError("session_id is required for config-scoped archives")
        return resolve_contained_path(base, session_id)

    raise ValueError(f"Unsupported archive location: {archive_location}")


def transcript_source_path(workspace_dir: str | Path, session_id: str) -> Path:
    """Return the persisted transcript JSON path for a session."""
    return resolve_contained_path(
        workspace_dir,
        TRANSCRIPT_DIR / f"conversation_{session_id}.json",
    )


def archive_manifest_path(
    workspace_dir: str | Path | None,
    *,
    session_id: str | None = None,
    archive_location: str = "workspace",
    archive_path: str | Path | None = None,
) -> Path:
    """Return the archive manifest path."""
    root = archive_root(
        workspace_dir,
        session_id=session_id,
        archive_location=archive_location,
        archive_path=archive_path,
    )
    return resolve_contained_path(root, "manifest.json")


def archive_transcript_json_path(
    workspace_dir: str | Path | None,
    *,
    session_id: str | None = None,
    archive_location: str = "workspace",
    archive_path: str | Path | None = None,
) -> Path:
    """Return the archived transcript JSON path."""
    root = archive_root(
        workspace_dir,
        session_id=session_id,
        archive_location=archive_location,
        archive_path=archive_path,
    )
    return resolve_contained_path(root, "transcript.json")


def archive_transcript_markdown_path(
    workspace_dir: str | Path | None,
    *,
    session_id: str | None = None,
    archive_location: str = "workspace",
    archive_path: str | Path | None = None,
) -> Path:
    """Return the archived transcript Markdown path."""
    root = archive_root(
        workspace_dir,
        session_id=session_id,
        archive_location=archive_location,
        archive_path=archive_path,
    )
    return resolve_contained_path(root, "transcript.md")


def load_workspace_transcript(workspace_dir: str | Path, session_id: str) -> dict[str, Any]:
    """Load the persisted workspace transcript in API response shape."""
    workspace = Path(workspace_dir).expanduser().resolve()
    path = resolve_contained_path(workspace, transcript_source_path(workspace, session_id))
    if not path.exists():
        return {"turns": [], "is_active": False, "last_activity": ""}

    data = _read_json(workspace, path)
    return _normalise_transcript_payload(data)


def _archive_owned_by(
    workspace_dir: str | Path | None,
    *,
    session_id: str | None,
    archive_location: str = "workspace",
    archive_path: str | Path | None = None,
) -> bool:
    """True when the archive at this root belongs to ``session_id``.

    Workspace-scoped archives live at ONE shared path per workspace
    (``.volundr/archive``) — ``archive_root`` ignores the session id for that
    location. Without this guard a freshly created session in a workspace with
    prior history is served the PREVIOUS session's transcript while its broker
    is still starting (the "new session born full of old content" ghost-replay
    bug). The manifest records the owning session; an archive without a
    manifest (legacy) stays readable to avoid regressions.
    """
    if not session_id:
        return True
    manifest = load_archive_manifest(
        workspace_dir,
        session_id=session_id,
        archive_location=archive_location,
        archive_path=archive_path,
    )
    if not isinstance(manifest, dict):
        return True
    owner = manifest.get("session_id")
    return not owner or str(owner) == str(session_id)


def load_archive_transcript(
    workspace_dir: str | Path | None,
    *,
    session_id: str | None = None,
    archive_location: str = "workspace",
    archive_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Load the normalized archived transcript if present (and owned)."""
    try:
        path = archive_transcript_json_path(
            workspace_dir,
            session_id=session_id,
            archive_location=archive_location,
            archive_path=archive_path,
        )
    except ArchivePathError:
        raise
    except ValueError:
        return None
    if not path.exists():
        return None
    if not _archive_owned_by(
        workspace_dir,
        session_id=session_id,
        archive_location=archive_location,
        archive_path=archive_path,
    ):
        return None
    root = archive_root(
        workspace_dir,
        session_id=session_id,
        archive_location=archive_location,
        archive_path=archive_path,
    )
    data = _read_json(root, path)
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
    except ArchivePathError:
        raise
    except ValueError:
        return None
    if not path.exists():
        return None
    root = archive_root(
        workspace_dir,
        session_id=session_id,
        archive_location=archive_location,
        archive_path=archive_path,
    )
    return _read_json(root, path)


def archive_logs_aggregate_path(
    workspace_dir: str | Path | None,
    *,
    session_id: str | None = None,
    archive_location: str = "workspace",
    archive_path: str | Path | None = None,
) -> Path:
    """Return the archived aggregated logs path."""
    root = archive_root(
        workspace_dir,
        session_id=session_id,
        archive_location=archive_location,
        archive_path=archive_path,
    )
    return resolve_contained_path(root, Path("logs") / "aggregate.json")


def load_archive_logs(
    workspace_dir: str | Path | None,
    *,
    session_id: str | None = None,
    archive_location: str = "workspace",
    archive_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Load archived aggregate logs if present (and owned — see transcript)."""
    try:
        path = archive_logs_aggregate_path(
            workspace_dir,
            session_id=session_id,
            archive_location=archive_location,
            archive_path=archive_path,
        )
    except ArchivePathError:
        raise
    except ValueError:
        return None
    if not path.exists():
        return None
    if not _archive_owned_by(
        workspace_dir,
        session_id=session_id,
        archive_location=archive_location,
        archive_path=archive_path,
    ):
        return None
    root = archive_root(
        workspace_dir,
        session_id=session_id,
        archive_location=archive_location,
        archive_path=archive_path,
    )
    return _read_json(root, path)


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
    workspace = Path(workspace_dir).expanduser().resolve()
    root = archive_root(
        workspace,
        session_id=session_id,
        archive_location=archive_location,
        archive_path=archive_path,
    )
    if archive_location == "workspace":
        root = resolve_contained_path(workspace, root)
    root.mkdir(parents=True, exist_ok=True)

    _write_json(root, "transcript.json", transcript_payload)
    transcript_markdown = resolve_contained_path(root, "transcript.md")
    transcript_markdown.write_text(
        render_transcript_markdown(transcript_payload),
        encoding="utf-8",
    )
    _write_json(root, Path("logs") / "aggregate.json", aggregated_logs)

    if chronicle_payload is not None:
        _write_json(root, "chronicle.json", chronicle_payload)
    if timeline_payload is not None:
        _write_json(root, "timeline.json", timeline_payload)

    raw_logs_root = resolve_contained_path(root, Path("logs") / "raw")
    raw_events_root = resolve_contained_path(root, Path("events") / "claude-jsonl")
    raw_logs = _copy_workspace_logs(workspace, raw_logs_root)
    raw_events = _copy_event_streams(event_source_dir, raw_events_root)

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
    _write_json(root, "manifest.json", manifest)
    return manifest


def _copy_workspace_logs(workspace: Path, destination_root: Path) -> list[str]:
    destination_root = destination_root.resolve()
    copied: list[str] = []
    for source, relative in _workspace_log_sources(workspace):
        source = resolve_contained_path(workspace, source, strict=True)
        target = resolve_contained_path(destination_root, relative)
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
    source_dir = source_dir.resolve(strict=True)
    destination_root = destination_root.resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    for source in sorted(source_dir.glob("*.jsonl")):
        source = resolve_contained_path(source_dir, source, strict=True)
        target = resolve_contained_path(destination_root, source.name)
        shutil.copy2(source, target)
        copied.append(str((Path("events") / "claude-jsonl" / source.name).as_posix()))
    return copied


def _workspace_log_sources(workspace: Path) -> list[tuple[Path, Path]]:
    sources: list[tuple[Path, Path]] = []
    skuld_log = resolve_contained_path(workspace, ".skuld.log")
    if skuld_log.is_file():
        sources.append((skuld_log, Path("skuld.log")))

    flock_logs = resolve_contained_path(workspace, Path(".flock") / "logs")
    if flock_logs.is_dir():
        for path in sorted(flock_logs.glob("*.log")):
            sources.append((path, Path("flock") / path.name))

    service_logs = resolve_contained_path(workspace, Path(".services") / "logs")
    if service_logs.is_dir():
        for path in sorted(service_logs.glob("*.log")):
            sources.append((path, Path("services") / path.name))
    return sources


def _read_json(root: Path, path: Path) -> dict[str, Any]:
    safe_path = resolve_contained_path(root, path, strict=True)
    data = json.loads(safe_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _normalise_transcript_payload(data: dict[str, Any]) -> dict[str, Any]:
    turns = data.get("turns", [])
    if not isinstance(turns, list):
        turns = []
    return {"turns": turns, "is_active": False, "last_activity": ""}


def _write_json(root: Path, path: str | Path, payload: dict[str, Any]) -> None:
    safe_path = resolve_contained_path(root, path)
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    safe_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
