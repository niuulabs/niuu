"""Local observability helpers for persisted wardens."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from ravn.warden.models import WardenDreamSummary, WardenSpec

_LOG_HEADING_RE = re.compile(
    r"^## \[(?P<date>[^\]]+)\] (?P<prefix>[a-z0-9_-]+) \| (?P<subject>.+)$"
)
_DETAIL_KV_RE = re.compile(r"(?P<key>[a-z_]+)=(?P<value>[^\s]+)")


def mimir_root() -> Path:
    return Path.home() / ".ravn" / "mimir"


def resolve_mount_log_path(mount_name: str) -> Path:
    return mimir_root() / mount_name / "wiki" / "log.md"


def list_warden_dreams(spec: WardenSpec, *, limit: int | None = None) -> list[WardenDreamSummary]:
    """Return parsed dream-cycle summaries from the warden's attached Mimir mounts."""
    mount_names = list(
        dict.fromkeys(
            [
                *(spec.mimir.read_mount_names or spec.mimir.mount_names),
                *(
                    spec.mimir.write_mount_names
                    or ([] if not spec.mimir.write_mount else [spec.mimir.write_mount])
                ),
            ]
        )
    )
    dreams: list[WardenDreamSummary] = []
    for mount_name in mount_names:
        dreams.extend(_parse_mount_dreams(mount_name, warden_id=spec.id))
    dreams.sort(key=lambda dream: dream.timestamp or datetime.min.replace(tzinfo=UTC), reverse=True)
    if limit is not None:
        return dreams[:limit]
    return dreams


def latest_warden_dream(spec: WardenSpec) -> WardenDreamSummary | None:
    last_state_at = read_warden_last_dream_at(spec)
    if last_state_at is None:
        return None

    dreams = list_warden_dreams(spec)
    if not dreams:
        return None

    exact_match = next((dream for dream in dreams if dream.ravn == spec.id), None)
    if exact_match is not None:
        return exact_match

    eligible = [
        dream
        for dream in dreams
        if dream.timestamp is not None and dream.timestamp <= last_state_at
    ]
    if eligible:
        return eligible[0]
    return dreams[0]


def read_warden_last_dream_at(spec: WardenSpec) -> datetime | None:
    state_file = Path.home() / ".ravn" / "wardens" / spec.id / "state" / "dream_cycle_state.json"
    if not state_file.exists():
        return None
    try:
        raw = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = str(raw.get("last_dream_at", "")).strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def synthetic_warden_dream_logs(spec: WardenSpec, *, limit: int = 20) -> list[dict[str, object]]:
    """Return synthetic log entries derived from durable Mimir dream-cycle summaries."""
    entries: list[dict[str, object]] = []
    for index, dream in enumerate(reversed(list_warden_dreams(spec, limit=limit)), start=1):
        timestamp = dream.timestamp.isoformat() if dream.timestamp else None
        mount_label = ", ".join(dream.mounts) if dream.mounts else spec.mimir.write_mount or "mimir"
        message = (
            f"dream cycle completed for {mount_label} "
            f"(pages_updated={dream.pages_updated} entities_created={dream.entities_created} "
            f"lint_fixes={dream.lint_fixes})"
        )
        entries.append(
            {
                "id": f"dream:{dream.id}",
                "source": "dream",
                "line_number": index,
                "raw": message,
                "timestamp": timestamp,
                "level": "INFO",
                "logger": "mimir.dream",
                "message": message,
            }
        )
    return entries


def _parse_mount_dreams(mount_name: str, *, warden_id: str) -> list[WardenDreamSummary]:
    path = resolve_mount_log_path(mount_name)
    if not path.exists():
        return []

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

    entries: list[tuple[str, str, str, list[str]]] = []
    current: tuple[str, str, str, list[str]] | None = None
    for line in lines:
        heading = _LOG_HEADING_RE.match(line.strip())
        if heading:
            if current is not None:
                entries.append(current)
            current = (
                heading.group("date"),
                heading.group("prefix"),
                heading.group("subject"),
                [],
            )
            continue
        if current is not None and line.strip():
            current[3].append(line.strip())
    if current is not None:
        entries.append(current)

    dreams: list[WardenDreamSummary] = []
    for raw_date, prefix, subject, detail_lines in entries:
        if prefix != "dream":
            continue
        detail_text = " ".join(detail_lines)
        values = _extract_key_values(detail_lines)
        dreams.append(
            WardenDreamSummary(
                id=f"{mount_name}:{raw_date}:{subject}",
                timestamp=_parse_timestamp(subject, raw_date),
                ravn=values.get("ravn", warden_id or "mimir"),
                mounts=[mount_name],
                pages_updated=int(
                    values.get("pages_updated", _parse_count(detail_text, "pages_updated"))
                ),
                entities_created=int(
                    values.get("entities_created", _parse_count(detail_text, "entities_created"))
                ),
                lint_fixes=int(values.get("lint_fixes", _parse_count(detail_text, "lint_fixes"))),
                duration_ms=int(values.get("duration_ms", 0)),
            )
        )
    return dreams


def _extract_key_values(detail_lines: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in detail_lines:
        for match in _DETAIL_KV_RE.finditer(line):
            values[match.group("key")] = match.group("value")
    return values


def _parse_count(detail_text: str, key: str) -> int:
    match = re.search(rf"{re.escape(key)}=(\d+)", detail_text)
    if not match:
        return 0
    return int(match.group(1))


def _parse_timestamp(subject: str, raw_date: str) -> datetime | None:
    subject_text = str(subject or "").strip()
    if subject_text:
        try:
            parsed = datetime.fromisoformat(subject_text.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        except ValueError:
            pass
    date_text = str(raw_date or "").strip()
    if not date_text:
        return None
    try:
        parsed = datetime.fromisoformat(date_text)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    except ValueError:
        return None
