"""Bounded native transcript input and replay provenance shared by local providers."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from volundr.domain.models import SessionLogEntry

DEFAULT_MAX_TRANSCRIPT_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class NativeTranscript:
    records: list[tuple[int, dict]]
    sha256: str
    truncated_final_line: bool


def read_native_jsonl(path: Path, root: Path, max_bytes: int) -> NativeTranscript:
    """Read a bounded snapshot; only an unfinished final JSONL record is recoverable."""
    if max_bytes <= 0:
        raise ValueError("max_transcript_bytes must be positive")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Native transcript resolves outside its configured session directory")
    with path.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("Native transcript exceeds max_transcript_bytes")
    records = []
    lines = data.splitlines(keepends=True)
    truncated = False
    for index, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            record = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if index == len(lines) and not raw.endswith((b"\n", b"\r")):
                truncated = True
                break
            raise ValueError(f"Invalid native transcript JSON at line {index}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"Native transcript record at line {index} is not an object")
        records.append((index, record))
    return NativeTranscript(records, hashlib.sha256(data).hexdigest(), truncated)


def is_injected_text(text: str) -> bool:
    """Exclude native context wrappers which are stored with a user role."""
    return text.lstrip().startswith(
        (
            "<environment_context>",
            "<environment_details>",
            "<permissions instructions>",
            "<skills_instructions>",
            "<system-reminder>",
            "<system_reminder>",
            "<developer_instructions>",
            "<user_instructions>",
            "<plugin_instructions>",
            "<plugins>",
            "<recommended_plugins>",
            "<installed_plugins>",
            "<skills>",
            "<available_skills>",
            "<collaboration_mode>",
            "<task-notification>",
            "<teammate-message>",
            "<available-deferred-tools>",
            "<command-name>",
            "<command-message>",
            "<INSTRUCTIONS>",
            "# AGENTS.md instructions for ",
            "<local-command-caveat>",
            "<local-command-stdout>",
        )
    )


class NativeTranscriptBuilder:
    def __init__(
        self, provider: str, external_id: str, session_id: UUID, source: NativeTranscript
    ) -> None:
        self.provider = provider
        self.external_id = external_id
        self.session_id = session_id
        self.source = source
        self.entries: list[SessionLogEntry] = []

    def emit(
        self,
        payload: dict,
        timestamp: object,
        *,
        line: int,
        source_type: str,
        native_id: str | None = None,
    ) -> None:
        if not isinstance(timestamp, str):
            raise ValueError(f"Missing native transcript timestamp at line {line}")
        try:
            ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Invalid native transcript timestamp at line {line}") from exc
        if ts.tzinfo is None:
            raise ValueError(f"Native transcript timestamp lacks timezone at line {line}")
        frame = dict(payload)
        provenance = {
            "provider": self.provider,
            "external_id": self.external_id,
            "source_line": line,
            "source_type": source_type,
            "native_id": native_id,
            "source_sha256": self.source.sha256,
            "partial": self.source.truncated_final_line,
        }
        if self.source.truncated_final_line:
            provenance["diagnostics"] = ["ignored_truncated_final_line"]
        frame["metadata"] = {**frame.get("metadata", {}), "native_import": provenance}
        kind = frame["type"]
        role = "assistant" if kind in {"assistant", "result"} else frame.get("role")
        if kind == "user":
            role = "user"
            if isinstance(frame.get("message", {}).get("content"), str):
                identity = str(native_id or f"line:{line}")
                try:
                    turn_id = UUID(identity)
                except ValueError:
                    turn_id = uuid5(NAMESPACE_URL, f"{self.provider}:{self.external_id}:{identity}")
                frame.setdefault("uuid", str(turn_id))
        self.entries.append(
            SessionLogEntry(
                session_id=self.session_id,
                seq=len(self.entries) + 1,
                kind=kind,
                payload=frame,
                ts=ts.astimezone(UTC),
                role=role,
                request_id=frame.get("request_id"),
            )
        )

    def finish(self) -> list[SessionLogEntry]:
        return self.entries
