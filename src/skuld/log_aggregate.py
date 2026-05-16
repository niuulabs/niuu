"""Helpers for aggregating broker, flock, and service logs from a workspace."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

_LOCAL_TZ = datetime.now().astimezone().tzinfo or UTC

_STANDARD_LOG_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:,\d{3,6})?)"
    r"(?:\s+-\s+(?P<logger_dash>.+?)\s+-\s+(?P<level_dash>[A-Z]+)\s+-\s+(?P<message_dash>.*)"
    r"|\s+(?P<logger_ws>\S+)\s+(?P<level_ws>[A-Z]+)\s+(?P<message_ws>.*))$"
)
_UVICORN_LOG_RE = re.compile(r"^(?P<level>INFO|ERROR|WARNING|DEBUG|CRITICAL):\s+(?P<message>.*)$")

ParticipantKind = Literal["broker", "ravn", "service"]

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "WARN": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


@dataclass(frozen=True)
class AggregateParticipant:
    """Metadata for one participant contributing logs to a session stream."""

    id: str
    label: str
    kind: ParticipantKind


@dataclass(frozen=True)
class AggregateLogEntry:
    """A single normalized log line from any workspace-backed session source."""

    id: str
    timestamp: datetime
    level: str
    participant: str
    participant_label: str
    participant_kind: ParticipantKind
    source: str
    message: str
    sequence: int
    stream: str

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "level": self.level,
            "participant": self.participant,
            "participant_label": self.participant_label,
            "participant_kind": self.participant_kind,
            "source": self.source,
            "message": self.message,
            "sequence": self.sequence,
            "stream": self.stream,
        }


def aggregate_workspace_logs(
    workspace_dir: str | Path,
    *,
    lines: int = 100,
    level: str = "DEBUG",
    participants: set[str] | None = None,
    query: str = "",
) -> dict[str, object]:
    """Return a merged, filtered view of broker + flock + service logs for a workspace."""
    workspace = Path(workspace_dir).expanduser()
    all_entries: list[AggregateLogEntry] = []
    available: dict[str, AggregateParticipant] = {}

    for file_path, participant_id, participant_kind in _iter_workspace_log_files(workspace):
        participant = AggregateParticipant(
            id=participant_id,
            label=_participant_label(participant_id),
            kind=participant_kind,
        )
        available[participant.id] = participant
        all_entries.extend(_parse_log_file(file_path, participant))

    min_level = _coerce_level(level)
    requested_participants = {item.strip() for item in participants or set() if item.strip()}
    query_text = query.strip().lower()

    filtered = [
        entry
        for entry in all_entries
        if _coerce_level(entry.level) >= min_level
        and (not requested_participants or entry.participant in requested_participants)
        and (
            not query_text
            or query_text in entry.message.lower()
            or query_text in entry.source.lower()
            or query_text in entry.participant.lower()
        )
    ]
    filtered.sort(key=lambda entry: (entry.timestamp, entry.participant, entry.sequence))
    tail = filtered[-lines:]

    return {
        "total": len(all_entries),
        "filtered": len(filtered),
        "returned": len(tail),
        "available_participants": [
            {
                "id": participant.id,
                "label": participant.label,
                "kind": participant.kind,
            }
            for participant in sorted(available.values(), key=lambda item: (item.kind, item.label))
        ],
        "lines": [entry.to_dict() for entry in tail],
    }


def _iter_workspace_log_files(
    workspace: Path,
) -> list[tuple[Path, str, ParticipantKind]]:
    result: list[tuple[Path, str, ParticipantKind]] = []

    skuld_log = workspace / ".skuld.log"
    if skuld_log.is_file():
        result.append((skuld_log, "skuld", "broker"))

    flock_logs_dir = workspace / ".flock" / "logs"
    if flock_logs_dir.is_dir():
        for path in sorted(flock_logs_dir.glob("*.log")):
            result.append((path, path.stem, "ravn"))

    service_logs_dir = workspace / ".services" / "logs"
    if service_logs_dir.is_dir():
        for path in sorted(service_logs_dir.glob("*.log")):
            result.append((path, f"service:{path.stem}", "service"))

    return result


def _parse_log_file(path: Path, participant: AggregateParticipant) -> list[AggregateLogEntry]:
    fallback_timestamp = datetime.fromtimestamp(path.stat().st_mtime, tz=_LOCAL_TZ)
    last_timestamp = fallback_timestamp
    last_level = "INFO"
    last_source = participant.id
    entries: list[AggregateLogEntry] = []

    for index, raw_line in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines()
    ):
        line = raw_line.rstrip("\n")
        if not line:
            continue

        parsed = _parse_structured_line(line)
        if parsed is None:
            timestamp = last_timestamp
            level = last_level
            source = last_source
            message = line
        else:
            timestamp, level, source, message = parsed
            if timestamp is None:
                timestamp = last_timestamp
            last_timestamp = timestamp
            last_level = level
            last_source = source

        entry_id = (
            f"{participant.id}:{index}:{int(timestamp.timestamp() * 1000)}:"
            f"{level}:{source}:{message}"
        )
        entries.append(
            AggregateLogEntry(
                id=entry_id,
                timestamp=timestamp,
                level=level,
                participant=participant.id,
                participant_label=participant.label,
                participant_kind=participant.kind,
                source=source,
                message=message,
                sequence=index,
                stream=str(
                    path.relative_to(
                        path.parent.parent if path.parent.name == "logs" else path.parent
                    )
                ),
            )
        )

    return entries


def _parse_structured_line(line: str) -> tuple[datetime | None, str, str, str] | None:
    match = _STANDARD_LOG_RE.match(line)
    if match:
        timestamp = _parse_timestamp(match.group("timestamp"))
        logger_name = match.group("logger_dash") or match.group("logger_ws") or "session"
        level = match.group("level_dash") or match.group("level_ws") or "INFO"
        message = match.group("message_dash") or match.group("message_ws") or ""
        return timestamp, _normalize_level(level), logger_name.strip(), message

    uvicorn = _UVICORN_LOG_RE.match(line)
    if uvicorn:
        return (
            None,
            _normalize_level(uvicorn.group("level")),
            "uvicorn",
            uvicorn.group("message"),
        )

    return None


def _parse_timestamp(value: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S,%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=_LOCAL_TZ)
        except ValueError:
            continue
    return datetime.now(tz=_LOCAL_TZ)


def _normalize_level(value: str) -> str:
    level = value.upper()
    if level == "WARN":
        return "WARNING"
    return level if level in _LEVELS else "INFO"


def _coerce_level(value: str) -> int:
    return _LEVELS.get(value.upper(), logging.INFO)


def _participant_label(value: str) -> str:
    if value.startswith("service:"):
        return f"Service {value.split(':', 1)[1]}"
    return value.replace("-", " ").replace("_", " ").title()
