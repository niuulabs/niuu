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

    for path, participant, stream in _discover_log_files(workspace):
        available.setdefault(participant.id, participant)
        all_entries.extend(_load_log_entries(path, participant, stream))

    all_entries.sort(key=lambda item: (item.timestamp, item.sequence, item.id))
    total = len(all_entries)

    min_level = _LEVELS.get(_normalize_level(level), logging.DEBUG)
    participant_filter = participants or set()
    query_lower = query.casefold()

    filtered_entries = [
        entry
        for entry in all_entries
        if _LEVELS.get(entry.level, logging.DEBUG) >= min_level
        and (not participant_filter or entry.participant in participant_filter)
        and (
            not query_lower
            or query_lower in entry.participant.casefold()
            or query_lower in entry.source.casefold()
            or query_lower in entry.message.casefold()
        )
    ]

    return {
        "total": total,
        "filtered": len(filtered_entries),
        "returned": min(len(filtered_entries), lines),
        "available_participants": [
            {
                "id": participant.id,
                "label": participant.label,
                "kind": participant.kind,
            }
            for participant in available.values()
        ],
        "lines": [entry.to_dict() for entry in filtered_entries[-lines:]],
    }


def _discover_log_files(
    workspace: Path,
) -> list[tuple[Path, AggregateParticipant, str]]:
    files: list[tuple[Path, AggregateParticipant, str]] = []

    broker_path = workspace / ".skuld.log"
    if broker_path.is_file():
        files.append(
            (
                broker_path,
                AggregateParticipant(id="skuld", label="Skuld", kind="broker"),
                "skuld.log",
            )
        )

    flock_dir = workspace / ".flock" / "logs"
    if flock_dir.is_dir():
        for path in sorted(flock_dir.glob("*.log")):
            participant_id = path.stem
            files.append(
                (
                    path,
                    AggregateParticipant(
                        id=participant_id,
                        label=participant_id.replace("-", " ").title(),
                        kind="ravn",
                    ),
                    f".flock/logs/{path.name}",
                )
            )

    services_dir = workspace / ".services" / "logs"
    if services_dir.is_dir():
        for path in sorted(services_dir.glob("*.log")):
            participant_id = f"service:{path.stem}"
            files.append(
                (
                    path,
                    AggregateParticipant(
                        id=participant_id,
                        label=path.stem.replace("-", " ").title(),
                        kind="service",
                    ),
                    f".services/logs/{path.name}",
                )
            )

    return files


def _load_log_entries(
    path: Path,
    participant: AggregateParticipant,
    stream: str,
) -> list[AggregateLogEntry]:
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
                stream=stream,
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
    return datetime.now(_LOCAL_TZ)


def _normalize_level(value: str) -> str:
    normalized = value.upper().strip()
    return normalized if normalized in _LEVELS else "INFO"
