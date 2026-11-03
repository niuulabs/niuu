"""Shared contract for ``report_artifact``: a session reports an artifact explicitly.

The Forge MCP tool ``report_artifact`` covers what Forge cannot observe on its own (an
artifact published outside the claude.ai Artifact tool, or by an engine without it). The
broker appends a durable ``conversation.turn`` built by :func:`build_report_turn`; Forge's
artifact projector reads it back with :func:`report_from_turn`. Both sides live here so the
turn shape cannot drift between producer and projector (design §5.2).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

REPORT_ARTIFACT_TOOL_NAME = "report_artifact"
REPORT_ARTIFACT_TURN_KIND = "artifact_report"

MAX_REPORT_TITLE_CHARS = 200
MAX_REPORT_URL_CHARS = 2048
MAX_VERSION_CHARS = 100

_CLAUDE_ARTIFACT_URL = re.compile(
    r"^https://claude\.ai/(?:code/)?artifact/[A-Za-z0-9]+(?:[/?#].*)?$"
)

ReportedType = Literal["deck", "design", "doc", "design_system", "page"]
ARTIFACT_REPORT_TYPES: tuple[str, ...] = ("deck", "design", "doc", "design_system", "page")


class ArtifactReport(BaseModel):
    """What a session reports: the artifact's durable URL, and what it knows about it."""

    url: str = Field(min_length=1, max_length=MAX_REPORT_URL_CHARS)
    title: str = Field(min_length=1, max_length=MAX_REPORT_TITLE_CHARS)
    provider_version: str | None = Field(default=None, max_length=MAX_VERSION_CHARS)
    version_label: str | None = Field(default=None, max_length=MAX_VERSION_CHARS)
    type: ReportedType | None = None

    @field_validator("url")
    @classmethod
    def _claude_artifact(cls, value: str) -> str:
        stripped = value.strip()
        if not _CLAUDE_ARTIFACT_URL.match(stripped):
            raise ValueError(
                "url must be a claude.ai artifact URL (https://claude.ai/artifact/<id>)"
            )
        return stripped

    @field_validator("title")
    @classmethod
    def _one_line(cls, value: str) -> str:
        stripped = " ".join(value.split())
        if not stripped:
            raise ValueError("title must not be blank")
        return stripped


def build_report_turn(report: ArtifactReport, *, turn_id: str, created_at: str) -> dict:
    """The ConversationTurn-shaped dict a broker appends for a report (no final output)."""
    payload = report.model_dump(mode="json", exclude_none=True)
    return {
        "id": turn_id,
        "role": "assistant",
        "content": f"Reported artifact: {report.title}",
        "parts": [
            {"type": "tool_use", "id": turn_id, "name": REPORT_ARTIFACT_TOOL_NAME, "input": payload}
        ],
        "created_at": created_at,
        "metadata": {"kind": REPORT_ARTIFACT_TURN_KIND, "artifact_report": payload},
        "participant_id": None,
        "participant_meta": None,
        "thread_id": None,
        "visibility": "public",
    }


def report_from_turn(turn: dict) -> ArtifactReport | None:
    """The report a stored turn carries, else ``None``. Malformed reports are ignored."""
    if not isinstance(turn, dict):
        return None
    metadata = turn.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("kind") != REPORT_ARTIFACT_TURN_KIND:
        return None
    raw = metadata.get("artifact_report")
    if not isinstance(raw, dict):
        return None
    try:
        return ArtifactReport.model_validate(raw)
    except ValueError:
        return None
