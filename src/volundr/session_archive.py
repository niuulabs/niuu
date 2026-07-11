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
    if resolved_candidate == root_path:
        resolved_candidate = root_path
    elif not resolved_candidate.startswith(root_prefix):
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
    workspace_path = os.path.realpath(os.path.abspath(os.path.expanduser(os.fspath(workspace_dir))))
    candidate = transcript_source_path(Path(workspace_path), session_id)
    path = os.path.realpath(os.path.abspath(os.fspath(candidate)))
    workspace_prefix = workspace_path.rstrip(os.sep) + os.sep
    if path == workspace_path:
        path = workspace_path
    elif not path.startswith(workspace_prefix):
        raise ArchivePathError(f"Transcript path escapes workspace: {candidate}")
    if not os.path.exists(path):
        return {"turns": [], "is_active": False, "last_activity": ""}

    data = _read_json(Path(workspace_path), Path(path))
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
    root = archive_root(
        workspace_dir,
        session_id=session_id,
        archive_location=archive_location,
        archive_path=archive_path,
    )
    root_path = os.path.realpath(os.path.abspath(os.fspath(root)))
    candidate_path = os.path.realpath(os.path.abspath(os.fspath(path)))
    root_prefix = root_path.rstrip(os.sep) + os.sep
    if candidate_path == root_path:
        candidate_path = root_path
    elif not candidate_path.startswith(root_prefix):
        raise ArchivePathError(f"Archive artifact escapes archive root: {path}")
    path = Path(candidate_path)
    if not os.path.exists(candidate_path):
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
    root = archive_root(
        workspace_dir,
        session_id=session_id,
        archive_location=archive_location,
        archive_path=archive_path,
    )
    root_path = os.path.realpath(os.path.abspath(os.fspath(root)))
    candidate_path = os.path.realpath(os.path.abspath(os.fspath(path)))
    root_prefix = root_path.rstrip(os.sep) + os.sep
    if candidate_path == root_path:
        candidate_path = root_path
    elif not candidate_path.startswith(root_prefix):
        raise ArchivePathError(f"Archive manifest escapes archive root: {path}")
    if not os.path.exists(candidate_path):
        return None
    return _read_json(Path(root_path), Path(candidate_path))


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
    root = archive_root(
        workspace_dir,
        session_id=session_id,
        archive_location=archive_location,
        archive_path=archive_path,
    )
    root_path = os.path.realpath(os.path.abspath(os.fspath(root)))
    candidate_path = os.path.realpath(os.path.abspath(os.fspath(path)))
    root_prefix = root_path.rstrip(os.sep) + os.sep
    if candidate_path == root_path:
        candidate_path = root_path
    elif not candidate_path.startswith(root_prefix):
        raise ArchivePathError(f"Archive artifact escapes archive root: {path}")
    path = Path(candidate_path)
    if not os.path.exists(candidate_path):
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
    workspace_path = os.path.realpath(os.path.abspath(os.path.expanduser(os.fspath(workspace_dir))))
    workspace = Path(workspace_path)
    root_candidate = archive_root(
        workspace,
        session_id=session_id,
        archive_location=archive_location,
        archive_path=archive_path,
    )
    root_path = os.path.realpath(os.path.abspath(os.fspath(root_candidate)))
    if archive_location == "workspace":
        allowed_root = workspace_path
    else:
        configured = Path(archive_path) if archive_path else DEFAULT_CONFIG_ARCHIVE_DIR
        if configured.is_absolute():
            allowed_root = os.path.realpath(
                os.path.abspath(os.path.expanduser(os.fspath(configured)))
            )
        else:
            config_base = os.path.realpath(
                os.path.abspath(os.path.expanduser(os.fspath(config_root_dir())))
            )
            allowed_root = os.path.realpath(os.path.join(config_base, os.fspath(configured)))
    allowed_prefix = allowed_root.rstrip(os.sep) + os.sep
    if root_path == allowed_root:
        root_path = allowed_root
    elif not root_path.startswith(allowed_prefix):
        raise ArchivePathError(f"Archive root escapes configured root: {root_candidate}")
    os.makedirs(root_path, exist_ok=True)
    root = Path(root_path)

    _write_json(root, "transcript.json", transcript_payload)
    transcript_candidate = os.path.join(root_path, "transcript.md")
    transcript_path = os.path.realpath(os.path.abspath(transcript_candidate))
    root_prefix = root_path.rstrip(os.sep) + os.sep
    if transcript_path == root_path:
        transcript_path = root_path
    elif not transcript_path.startswith(root_prefix):
        raise ArchivePathError(f"Transcript path escapes archive root: {transcript_candidate}")
    Path(transcript_path).write_text(
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

    source_candidate = transcript_source_path(workspace, session_id)
    source_path = os.path.realpath(os.path.abspath(os.fspath(source_candidate)))
    workspace_prefix = workspace_path.rstrip(os.sep) + os.sep
    if source_path == workspace_path:
        source_path = workspace_path
    elif not source_path.startswith(workspace_prefix):
        raise ArchivePathError(f"Transcript source escapes workspace: {source_candidate}")

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
                str(Path(source_path).relative_to(workspace))
                if os.path.exists(source_path)
                else None
            ),
            "workspace_logs": raw_logs,
            "event_streams": raw_events,
        },
    }
    _write_json(root, "manifest.json", manifest)
    return manifest


def _copy_workspace_logs(workspace: Path, destination_root: Path) -> list[str]:
    workspace_path = os.path.realpath(os.path.abspath(os.fspath(workspace)))
    destination_path = os.path.realpath(os.path.abspath(os.fspath(destination_root)))
    copied: list[str] = []
    for source, relative in _workspace_log_sources(Path(workspace_path)):
        source_path = os.path.realpath(os.path.abspath(os.fspath(source)), strict=True)
        workspace_prefix = workspace_path.rstrip(os.sep) + os.sep
        if source_path == workspace_path:
            source_path = workspace_path
        elif not source_path.startswith(workspace_prefix):
            raise ArchivePathError(f"Log source escapes workspace: {source}")
        target_candidate = os.path.join(destination_path, os.fspath(relative))
        target_path = os.path.realpath(os.path.abspath(target_candidate))
        destination_prefix = destination_path.rstrip(os.sep) + os.sep
        if target_path == destination_path:
            target_path = destination_path
        elif not target_path.startswith(destination_prefix):
            raise ArchivePathError(f"Log destination escapes archive root: {relative}")
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        shutil.copy2(source_path, target_path)
        copied.append(str((Path("logs") / "raw" / relative).as_posix()))
    return copied


def _copy_event_streams(
    event_source_dir: str | Path | None,
    destination_root: Path,
) -> list[str]:
    if event_source_dir is None:
        return []
    source_path = os.path.realpath(
        os.path.abspath(os.path.expanduser(os.fspath(event_source_dir)))
    )
    source_parent = os.path.realpath(os.path.abspath(os.path.dirname(source_path)))
    source_parent_prefix = source_parent.rstrip(os.sep) + os.sep
    if source_path == source_parent:
        source_path = source_parent
    elif not source_path.startswith(source_parent_prefix):
        raise ArchivePathError(f"Event source escapes its parent: {event_source_dir}")
    if not os.path.isdir(source_path):
        return []

    copied: list[str] = []
    destination_path = os.path.realpath(os.path.abspath(os.fspath(destination_root)))
    destination_parent = os.path.realpath(os.path.abspath(os.path.dirname(destination_path)))
    destination_parent_prefix = destination_parent.rstrip(os.sep) + os.sep
    if destination_path == destination_parent:
        destination_path = destination_parent
    elif not destination_path.startswith(destination_parent_prefix):
        raise ArchivePathError(f"Event destination escapes its parent: {destination_root}")
    os.makedirs(destination_path, exist_ok=True)
    for source in sorted(Path(source_path).glob("*.jsonl")):
        file_path = os.path.realpath(os.path.abspath(os.fspath(source)), strict=True)
        source_prefix = source_path.rstrip(os.sep) + os.sep
        if file_path == source_path:
            file_path = source_path
        elif not file_path.startswith(source_prefix):
            raise ArchivePathError(f"Event source escapes source directory: {source}")
        target_candidate = os.path.join(destination_path, source.name)
        target_path = os.path.realpath(os.path.abspath(target_candidate))
        destination_prefix = destination_path.rstrip(os.sep) + os.sep
        if target_path == destination_path:
            target_path = destination_path
        elif not target_path.startswith(destination_prefix):
            raise ArchivePathError(f"Event destination escapes archive root: {source.name}")
        shutil.copy2(file_path, target_path)
        copied.append(str((Path("events") / "claude-jsonl" / source.name).as_posix()))
    return copied


def _workspace_log_sources(workspace: Path) -> list[tuple[Path, Path]]:
    workspace_path = os.path.realpath(os.path.abspath(os.fspath(workspace)))
    workspace_prefix = workspace_path.rstrip(os.sep) + os.sep
    sources: list[tuple[Path, Path]] = []

    skuld_path = os.path.realpath(os.path.join(workspace_path, ".skuld.log"))
    if skuld_path == workspace_path:
        skuld_path = workspace_path
    elif not skuld_path.startswith(workspace_prefix):
        raise ArchivePathError("Skuld log path escapes workspace")
    if os.path.isfile(skuld_path):
        sources.append((Path(skuld_path), Path("skuld.log")))

    flock_path = os.path.realpath(os.path.join(workspace_path, ".flock", "logs"))
    if flock_path == workspace_path:
        flock_path = workspace_path
    elif not flock_path.startswith(workspace_prefix):
        raise ArchivePathError("Flock log path escapes workspace")
    if os.path.isdir(flock_path):
        for candidate in sorted(Path(flock_path).glob("*.log")):
            path = os.path.realpath(os.path.abspath(os.fspath(candidate)), strict=True)
            flock_prefix = flock_path.rstrip(os.sep) + os.sep
            if path == flock_path:
                path = flock_path
            elif not path.startswith(flock_prefix):
                raise ArchivePathError(f"Flock log escapes log directory: {candidate}")
            sources.append((Path(path), Path("flock") / candidate.name))

    service_path = os.path.realpath(os.path.join(workspace_path, ".services", "logs"))
    if service_path == workspace_path:
        service_path = workspace_path
    elif not service_path.startswith(workspace_prefix):
        raise ArchivePathError("Service log path escapes workspace")
    if os.path.isdir(service_path):
        for candidate in sorted(Path(service_path).glob("*.log")):
            path = os.path.realpath(os.path.abspath(os.fspath(candidate)), strict=True)
            service_prefix = service_path.rstrip(os.sep) + os.sep
            if path == service_path:
                path = service_path
            elif not path.startswith(service_prefix):
                raise ArchivePathError(f"Service log escapes log directory: {candidate}")
            sources.append((Path(path), Path("services") / candidate.name))
    return sources


def _read_json(root: Path, path: Path) -> dict[str, Any]:
    root_path = os.path.realpath(os.path.abspath(os.fspath(root)))
    safe_path = os.path.realpath(os.path.abspath(os.fspath(path)), strict=True)
    root_prefix = root_path.rstrip(os.sep) + os.sep
    if safe_path == root_path:
        safe_path = root_path
    elif not safe_path.startswith(root_prefix):
        raise ArchivePathError(f"JSON path escapes archive root: {path}")
    with open(safe_path, encoding="utf-8") as archive_file:
        data = json.load(archive_file)
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data


def _normalise_transcript_payload(data: dict[str, Any]) -> dict[str, Any]:
    turns = data.get("turns", [])
    if not isinstance(turns, list):
        turns = []
    return {"turns": turns, "is_active": False, "last_activity": ""}


def _write_json(root: Path, path: str | Path, payload: dict[str, Any]) -> None:
    root_path = os.path.realpath(os.path.abspath(os.fspath(root)))
    candidate = os.path.join(root_path, os.fspath(path))
    safe_path = os.path.realpath(os.path.abspath(candidate))
    root_prefix = root_path.rstrip(os.sep) + os.sep
    if safe_path == root_path:
        safe_path = root_path
    elif not safe_path.startswith(root_prefix):
        raise ArchivePathError(f"JSON path escapes archive root: {path}")
    os.makedirs(os.path.dirname(safe_path), exist_ok=True)
    with open(safe_path, "w", encoding="utf-8") as archive_file:
        json.dump(payload, archive_file, indent=2)
