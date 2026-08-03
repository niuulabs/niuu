"""Construct, render, and parse resident inbox signals and triage records."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ravn.resident_text import compact_line as _compact_line
from ravn.resident_text import slug as _slug
from ravn.resident_text import timestamp_slug

from .classify import classify_text
from .models import (
    _INBOX_SIGNAL_JSON_END,
    _INBOX_SIGNAL_JSON_START,
    _INBOX_TRIAGE_JSON_END,
    _INBOX_TRIAGE_JSON_START,
    _OPERATOR_DIRECTED_MESSAGE_KIND,
    ResidentInboxClassification,
    ResidentInboxSignal,
    ResidentInboxStatus,
    ResidentInboxTriage,
)
from .shape import ShapeAggregate


def _signal_from_record(record: dict[str, Any], *, default_kind: str) -> ResidentInboxSignal:
    summary = str(record.get("summary") or "Resident inbox signal observed.")
    classification, confidence, reason = classify_text(summary, payload=record)
    return ResidentInboxSignal(
        id=str(record.get("id") or _slug(summary) or "signal"),
        source=str(record.get("source") or "environment"),
        kind=str(record.get("kind") or default_kind),
        summary=summary,
        payload=dict(record),
        trace_context={
            str(key): str(value) for key, value in dict(record.get("trace_context") or {}).items()
        },
        raw_ref=str(record.get("evidence_ref") or ""),
        classification=classification,
        confidence=confidence,
        reason=reason,
        observed_at=str(record.get("observed_at") or ""),
    )


def signal_from_event(event: Any) -> ResidentInboxSignal:
    return _signal_from_record(_signal_record_from_event(event), default_kind="signal")


def signal_from_directed_message(
    content: str,
    *,
    metadata: dict[str, Any] | None = None,
    source: str = "skuld:directed_message",
) -> ResidentInboxSignal:
    meta = dict(metadata or {})
    text = _compact_line(content, limit=240) or "Directed operator message"
    classification, confidence, reason = classify_text(text, payload=meta)
    message_id = (
        str(meta.get("telegram_message_id") or "").strip()
        or str(meta.get("message_id") or "").strip()
        or timestamp_slug(datetime.now(UTC))
    )
    return ResidentInboxSignal(
        id=f"operator-message-{_slug(message_id) or 'message'}",
        source=source,
        kind=_OPERATOR_DIRECTED_MESSAGE_KIND,
        summary=text,
        payload={"content": content, "metadata": meta},
        raw_ref=str(meta.get("raw_ref") or ""),
        classification=classification,
        confidence=confidence,
        reason=reason,
        observed_at=str(meta.get("telegram_date") or datetime.now(UTC).isoformat()),
    )


def _signal_record_from_event(event: Any) -> dict[str, Any]:
    # JSON-normalize so datetimes / non-serializable payload values become strings
    # before the record is persisted.
    record = _environment_signal_record(event)
    return json.loads(json.dumps(record, default=str))


def _environment_signal_record(event: Any) -> dict[str, Any]:
    """Extract the inbox-relevant fields from a Sleipnir-style event object."""
    payload = dict(getattr(event, "payload", {}) or {})
    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}
    event_type = str(getattr(event, "event_type", "") or payload.get("event_type") or "")
    severity = str(payload.get("severity") or data.get("severity") or "info")
    event_id = str(
        getattr(event, "event_id", "")
        or payload.get("event_id")
        or data.get("provider_event_id")
        or data.get("dedupe_key")
        or getattr(event, "correlation_id", "")
        or event_type
    )
    return {
        "id": event_id,
        "source": str(data.get("source_id") or payload.get("signal_source") or "environment"),
        "kind": event_type or str(payload.get("signal_kind") or "environment_signal"),
        "summary": _environment_signal_summary(event, payload, data, severity),
        "evidence_ref": str(data.get("raw_payload_ref") or ""),
        "severity": severity,
        "observed_at": _event_timestamp(event, data),
        "payload": payload,
        "trace_context": dict(getattr(event, "trace_context", {}) or {}),
    }


def _event_timestamp(event: Any, data: dict[str, Any]) -> str:
    raw = data.get("observed_at") or getattr(event, "timestamp", None)
    if hasattr(raw, "isoformat"):
        return raw.isoformat()
    text = str(raw or "").strip()
    if text:
        return text
    return datetime.now(UTC).isoformat()


def _environment_signal_summary(
    event: Any,
    payload: dict[str, Any],
    data: dict[str, Any],
    severity: str,
) -> str:
    message = str(
        data.get("message")
        or data.get("reason")
        or data.get("title")
        or payload.get("summary")
        or ""
    ).strip()
    explicit = str(getattr(event, "summary", "") or "").strip()
    if explicit:
        if message and message not in explicit:
            return _compact_line(f"{explicit}: {message}", limit=220)
        return explicit
    object_ref = data.get("object_ref")
    if not isinstance(object_ref, dict):
        object_ref = {}
    object_label = " ".join(
        str(object_ref.get(key) or "").strip()
        for key in ("kind", "namespace", "name")
        if str(object_ref.get(key) or "").strip()
    )
    signal_kind = str(payload.get("signal_kind") or payload.get("kind") or "").strip()
    parts = [part for part in (signal_kind, object_label, severity, message) if part]
    return _compact_line(" - ".join(parts) or "Environment signal observed.", limit=220)


def render_inbox_signal(signal: ResidentInboxSignal) -> str:
    payload = json.dumps(_signal_to_dict(signal), indent=2, sort_keys=True, default=str)
    return (
        f"# Resident Inbox Signal: {_compact_line(signal.summary, limit=120)}\n\n"
        f"- id: {signal.id}\n"
        f"- source: {signal.source}\n"
        f"- kind: {signal.kind}\n"
        f"- classification: {signal.classification}\n"
        f"- confidence: {signal.confidence:.2f}\n"
        f"- status: {signal.status}\n"
        f"- target_objective_id: {signal.target_objective_id}\n"
        f"- shape_key: {signal.shape_key}\n"
        f"- observation_count: {signal.observation_count}\n"
        f"- archive_range: {signal.first_archive_ref}..{signal.last_archive_ref}\n"
        f"- first_observed_at: {signal.first_observed_at}\n"
        f"- observed_at: {signal.observed_at}\n"
        f"- created_at: {signal.created_at.isoformat()}\n"
        f"- processed_at: {signal.processed_at.isoformat() if signal.processed_at else ''}\n\n"
        f"## Summary\n\n{signal.summary}\n\n"
        f"## Reason\n\n{signal.reason}\n\n"
        f"{_INBOX_SIGNAL_JSON_START}\n"
        f"{payload}\n"
        f"{_INBOX_SIGNAL_JSON_END}\n"
    )


def parse_inbox_signal(content: str) -> ResidentInboxSignal | None:
    start = content.find(_INBOX_SIGNAL_JSON_START)
    end = content.find(_INBOX_SIGNAL_JSON_END)
    if start < 0 or end <= start:
        return None
    raw = content[start + len(_INBOX_SIGNAL_JSON_START) : end].strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    return _signal_from_dict(parsed)


def render_inbox_triage(triage: ResidentInboxTriage) -> str:
    payload = json.dumps(_triage_to_dict(triage), indent=2, sort_keys=True, default=str)
    return (
        f"# Resident Inbox Triage: {triage.signal_id}\n\n"
        f"- signal_id: {triage.signal_id}\n"
        f"- classification: {triage.classification}\n"
        f"- decision: {triage.decision}\n"
        f"- target_objective_id: {triage.target_objective_id}\n"
        f"- created_at: {triage.created_at.isoformat()}\n\n"
        f"## Reason\n\n{triage.reason}\n\n"
        f"{_INBOX_TRIAGE_JSON_START}\n"
        f"{payload}\n"
        f"{_INBOX_TRIAGE_JSON_END}\n"
    )


def _signal_to_dict(signal: ResidentInboxSignal) -> dict[str, Any]:
    return {
        "id": signal.id,
        "source": signal.source,
        "kind": signal.kind,
        "summary": signal.summary,
        "payload": signal.payload,
        "trace_context": signal.trace_context,
        "raw_ref": signal.raw_ref,
        "classification": signal.classification,
        "confidence": signal.confidence,
        "status": signal.status,
        "evidence_refs": list(signal.evidence_refs),
        "target_objective_id": signal.target_objective_id,
        "reason": signal.reason,
        "observed_at": signal.observed_at,
        "created_at": signal.created_at.isoformat(),
        "processed_at": signal.processed_at.isoformat() if signal.processed_at else "",
        "shape_key": signal.shape_key,
        "observation_count": signal.observation_count,
        "first_archive_ref": signal.first_archive_ref,
        "last_archive_ref": signal.last_archive_ref,
        "first_observed_at": signal.first_observed_at,
        "aggregate": signal.aggregate.to_dict(),
        "attempts": signal.attempts,
    }


def _signal_from_dict(data: dict[str, Any]) -> ResidentInboxSignal:
    return ResidentInboxSignal(
        id=str(data.get("id") or "signal"),
        source=str(data.get("source") or "resident"),
        kind=str(data.get("kind") or "signal"),
        summary=str(data.get("summary") or "Resident inbox signal"),
        payload=dict(data.get("payload") or {}),
        trace_context={
            str(key): str(value) for key, value in dict(data.get("trace_context") or {}).items()
        },
        raw_ref=str(data.get("raw_ref") or ""),
        classification=str(data.get("classification") or ResidentInboxClassification.UNKNOWN.value),
        confidence=float(data.get("confidence") or 0.5),
        status=str(data.get("status") or ResidentInboxStatus.NEW.value),
        evidence_refs=tuple(str(item) for item in data.get("evidence_refs") or ()),
        target_objective_id=str(data.get("target_objective_id") or ""),
        reason=str(data.get("reason") or ""),
        observed_at=str(data.get("observed_at") or ""),
        created_at=_parse_datetime(data.get("created_at")),
        processed_at=_parse_optional_datetime(data.get("processed_at")),
        shape_key=str(data.get("shape_key") or ""),
        # Records written before coalescing existed cover exactly one observation.
        observation_count=max(1, int(data.get("observation_count") or 1)),
        first_archive_ref=str(data.get("first_archive_ref") or ""),
        last_archive_ref=str(data.get("last_archive_ref") or ""),
        first_observed_at=str(data.get("first_observed_at") or data.get("observed_at") or ""),
        aggregate=ShapeAggregate.from_dict(data.get("aggregate")),
        attempts=max(0, int(data.get("attempts") or 0)),
    )


def _triage_to_dict(triage: ResidentInboxTriage) -> dict[str, Any]:
    return {
        "signal_id": triage.signal_id,
        "classification": triage.classification,
        "decision": triage.decision,
        "reason": triage.reason,
        "signal_ref": triage.signal_ref,
        "objective_ref": triage.objective_ref,
        "memory_ref": triage.memory_ref,
        "target_objective_id": triage.target_objective_id,
        "created_at": triage.created_at.isoformat(),
    }


def _parse_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.now(UTC)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        # A malformed/legacy date on one persisted page must not crash the whole
        # wake pass (list_signals/collect iterates every page).
        return datetime.now(UTC)


def _parse_optional_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    return _parse_datetime(text)


def _signal_filename(signal: ResidentInboxSignal) -> str:
    observed = signal.observed_at or signal.created_at.isoformat()
    stamp = _slug(observed.replace("+00:00", "Z")) or signal.created_at.strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{_slug(signal.id) or 'signal'}.md"
