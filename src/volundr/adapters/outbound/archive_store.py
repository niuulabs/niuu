"""Filesystem-backed session archive store adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from volundr.domain.ports import ArchiveStorePort
from volundr.session_archive import (
    archive_manifest_path,
    archive_root,
    archive_transcript_json_path,
    archive_transcript_markdown_path,
    load_archive_logs,
    load_archive_manifest,
    load_archive_transcript,
    write_session_archive,
)


class FileSystemArchiveStore(ArchiveStorePort):
    """Store session archives on a local/shared filesystem."""

    def __init__(
        self,
        *,
        location: str = "workspace",
        path: str = "",
        **_extra: object,
    ) -> None:
        self._location = location
        self._path = path

    def load_manifest(
        self,
        *,
        session_id: str,
        workspace_dir: str | Path | None = None,
    ) -> dict[str, Any] | None:
        return load_archive_manifest(
            workspace_dir,
            session_id=session_id,
            archive_location=self._location,
            archive_path=self._path,
        )

    def load_transcript(
        self,
        *,
        session_id: str,
        workspace_dir: str | Path | None = None,
    ) -> dict[str, Any] | None:
        return load_archive_transcript(
            workspace_dir,
            session_id=session_id,
            archive_location=self._location,
            archive_path=self._path,
        )

    def load_aggregated_logs(
        self,
        *,
        session_id: str,
        workspace_dir: str | Path | None = None,
    ) -> dict[str, Any] | None:
        return load_archive_logs(
            workspace_dir,
            session_id=session_id,
            archive_location=self._location,
            archive_path=self._path,
        )

    def write_archive(
        self,
        *,
        session_id: str,
        workspace_dir: str | Path,
        transcript_payload: dict[str, Any],
        aggregated_logs: dict[str, Any],
        chronicle_payload: dict[str, Any] | None = None,
        timeline_payload: dict[str, Any] | None = None,
        event_source_dir: str | Path | None = None,
    ) -> dict[str, Any]:
        return write_session_archive(
            session_id=session_id,
            workspace_dir=workspace_dir,
            transcript_payload=transcript_payload,
            aggregated_logs=aggregated_logs,
            archive_location=self._location,
            archive_path=self._path,
            chronicle_payload=chronicle_payload,
            timeline_payload=timeline_payload,
            event_source_dir=event_source_dir,
        )

    def transcript_json_path(
        self,
        *,
        session_id: str,
        workspace_dir: str | Path | None = None,
    ) -> Path:
        return archive_transcript_json_path(
            workspace_dir,
            session_id=session_id,
            archive_location=self._location,
            archive_path=self._path,
        )

    def transcript_markdown_path(
        self,
        *,
        session_id: str,
        workspace_dir: str | Path | None = None,
    ) -> Path:
        return archive_transcript_markdown_path(
            workspace_dir,
            session_id=session_id,
            archive_location=self._location,
            archive_path=self._path,
        )

    def archive_root(
        self,
        *,
        session_id: str,
        workspace_dir: str | Path | None = None,
    ) -> Path:
        return archive_root(
            workspace_dir,
            session_id=session_id,
            archive_location=self._location,
            archive_path=self._path,
        )

    def manifest_path(
        self,
        *,
        session_id: str,
        workspace_dir: str | Path | None = None,
    ) -> Path:
        """Return the manifest path for callers that need direct access."""
        return archive_manifest_path(
            workspace_dir,
            session_id=session_id,
            archive_location=self._location,
            archive_path=self._path,
        )
