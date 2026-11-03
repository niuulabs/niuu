"""``present_file``: hand the session's people a document as a file.

The Ravn counterpart of the ``present-file`` command that Claude Code and Codex
sessions run. In a Forge flock session the members share the session's workspace
volume with its broker (Skuld) but not the broker's loopback secret, so the tool
asks the broker over the flock mesh it already speaks. The broker stages the file,
makes it durable in Forge and records it in the session log, where Forge reads it
as an artifact publication.

The agent decides what is worth presenting. A document it will revise (a plan, a
spec, a report) gets an ``artifact_key``: every file presented under one key is the
next version of one artifact, which is what a reviewer approves or sends back.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from niuu.domain.presented_files import (
    PRESENT_FILE_RPC_TYPE,
    PRESENTED_ARTIFACT_KEY_FIELD,
    PRESENTED_ARTIFACT_KEY_PATTERN,
)
from ravn.cap_telemetry import record_cap_hit
from ravn.domain.models import ToolResult
from ravn.ports.tool import ToolPort

if TYPE_CHECKING:
    from ravn.ports.mesh import MeshPort

PRESENT_FILE_TOOL_NAME = "present_file"
_PERMISSION = "artifact:present"
#: Where a document given as text is written before it is presented: Ravn's own state
#: under the shared workspace, never product source.
_WRITTEN_DIR = Path(".ravn") / "presented"
_ARTIFACT_KEY = re.compile(PRESENTED_ARTIFACT_KEY_PATTERN)
_FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")

_DESCRIPTION = (
    "Present a finished document (a plan, a spec, a research report, a design) to the "
    "people working with this session. It appears as a file they can open and review, "
    "and Forge keeps it after the session ends.\n\n"
    "Give either `path` (a file in the workspace) or `content` with `name` (the document "
    "text; it is saved for you, so no write access is needed). Give a document you will "
    "revise an `artifact_key` such as `plan` or `spec`: presenting again under the same "
    "key is its next version (v1, v2, ...), so reviewers see one document's history.\n\n"
    "Present only what a person should read. Routine tool output, logs and intermediate "
    "notes stay in your outcome or your workspace."
)

_INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "path": {
            "type": "string",
            "description": "A file in the session workspace (relative, or absolute under it).",
        },
        "content": {
            "type": "string",
            "description": "The document text, when it is not already a workspace file.",
        },
        "name": {
            "type": "string",
            "description": "File name for `content`, e.g. plan.md (letters, digits, . _ -).",
        },
        "artifact_key": {
            "type": "string",
            "description": "Stable name of the document across versions, e.g. plan.",
        },
        "title": {"type": "string", "description": "Display name; defaults to the file name."},
        "caption": {"type": "string", "description": "One line saying what this version is."},
    },
}


class PresentFileError(ValueError):
    """The request names no presentable document; the message says what to fix."""


class PresentFileTool(ToolPort):
    """Ask the session broker to present a workspace file (see the module docstring)."""

    def __init__(
        self,
        mesh: MeshPort,
        *,
        broker_peer_id: str,
        own_peer_id: str,
        workspace_root: str | Path,
        timeout_s: float,
    ) -> None:
        self._mesh = mesh
        self._broker_peer_id = broker_peer_id
        self._own_peer_id = own_peer_id
        self._workspace = Path(workspace_root).resolve()
        self._timeout_s = timeout_s

    @property
    def name(self) -> str:
        return PRESENT_FILE_TOOL_NAME

    @property
    def description(self) -> str:
        return _DESCRIPTION

    @property
    def input_schema(self) -> dict:
        return _INPUT_SCHEMA

    @property
    def required_permission(self) -> str:
        return _PERMISSION

    @property
    def parallelisable(self) -> bool:
        return False

    async def execute(self, input: dict) -> ToolResult:
        fields = dict(input or {})
        try:
            relative = self._document(fields)
            key = _artifact_key(fields.get(PRESENTED_ARTIFACT_KEY_FIELD))
        except PresentFileError as exc:
            return _error(str(exc))
        message: dict = {
            "type": PRESENT_FILE_RPC_TYPE,
            "path": relative.as_posix(),
            "peer_id": self._own_peer_id,
        }
        for field in ("title", "caption"):
            value = str(fields.get(field) or "").strip()
            if value:
                message[field] = value
        if key:
            message[PRESENTED_ARTIFACT_KEY_FIELD] = key
        try:
            reply = await self._mesh.send(self._broker_peer_id, message, timeout_s=self._timeout_s)
        except TimeoutError:
            problem = (
                f"The session broker did not answer within present_file.timeout_s="
                f"{self._timeout_s:g}s; the file may still appear. Raise "
                "present_file.timeout_s if presenting regularly takes longer."
            )
            record_cap_hit("present_file.timeout_s", problem)
            return _error(problem)
        if reply.get("status") != "complete":
            return _error(f"The file was not presented: {reply.get('error') or reply}")
        reply.pop("status", None)
        return ToolResult(tool_call_id="", content=json.dumps(reply))

    def _document(self, fields: dict) -> Path:
        """The workspace-relative path of the file to present, writing ``content`` first."""
        path = str(fields.get("path") or "").strip()
        content = fields.get("content")
        if path and content is not None:
            raise PresentFileError("Give either path or content, not both")
        if path:
            return self._inside_workspace(path)
        if not isinstance(content, str) or not content.strip():
            raise PresentFileError("Give path (a workspace file) or content with name")
        name = str(fields.get("name") or "").strip()
        if _FILE_NAME.fullmatch(name) is None:
            raise PresentFileError(
                "name is the file name for content, e.g. plan.md (letters, digits, "
                "spaces, '.', '_' or '-')"
            )
        relative = _WRITTEN_DIR / uuid.uuid4().hex / name
        target = self._workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return relative

    def _inside_workspace(self, path: str) -> Path:
        candidate = Path(path)
        resolved = (candidate if candidate.is_absolute() else self._workspace / candidate).resolve()
        if not resolved.is_relative_to(self._workspace):
            raise PresentFileError(
                f"path must be in the session workspace ({self._workspace}); the broker "
                "can only read files there"
            )
        if not resolved.is_file():
            raise PresentFileError(f"No such file in the workspace: {path}")
        return resolved.relative_to(self._workspace)


def _artifact_key(raw: object) -> str | None:
    key = str(raw or "").strip()
    if not key:
        return None
    if _ARTIFACT_KEY.fullmatch(key) is None:
        raise PresentFileError(
            "artifact_key is letters, digits, '.', '_' or '-', starting with a letter or "
            "digit (e.g. plan, spec, research-report)"
        )
    return key


def _error(content: str) -> ToolResult:
    return ToolResult(tool_call_id="", content=content, is_error=True)
