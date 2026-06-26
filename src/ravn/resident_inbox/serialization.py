"""Construct, render, and parse resident inbox signals and triage records."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ravn.resident_continuation import _compact_line, _slug
from ravn.resident_opportunity import (
    _environment_signal_record,
    parse_environment_signal_record,
)

from .classify import classify_text
from .models import (
    _INBOX_SIGNAL_JSON_END,
    _INBOX_SIGNAL_JSON_START,
    _INBOX_TRIAGE_JSON_END,
    _INBOX_TRIAGE_JSON_START,
    ResidentInboxClassification,
    ResidentInboxSignal,
    ResidentInboxStatus,
    ResidentInboxTriage,
)


def _signal_from_record(record: dict[str, Any], *, default_kind: str) -> ResidentInboxSignal:
    summary = str(record.get("summary") or "Resident inbox signal observed.")
    classification, confidence, reason = classify_text(summary, payload=record)
    return ResidentInboxSignal(
        id=str(record.get("id") or _slug(summary) or "signal"),
        source=str(record.get("source") or "environment"),
        kind=str(record.get("kind") or default_kind),
        summary=summary,
        payload=dict(record),
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
        or datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    )
    return ResidentInboxSignal(
        id=f"operator-message-{_slug(message_id) or 'message'}",
        source=source,
        kind="operator.directed_message",
        summary=text,
        payload={"content": content, "metadata": meta},
        raw_ref=str(meta.get("raw_ref") or ""),
        classification=classification,
        confidence=confidence,
        reason=reason,
        observed_at=str(meta.get("telegram_date") or datetime.now(UTC).isoformat()),
    )


def signal_from_event_like_record(record: dict[str, Any]) -> ResidentInboxSignal:
    return _signal_from_record(record, default_kind="environment_signal")


def _signal_record_from_event(event: Any) -> dict[str, Any]:
    # JSON-normalize so datetimes / non-serializable payload values become strings
    # before the record is persisted; the canonical extractor lives in resident_opportunity.
    record = _environment_signal_record(event)
    return json.loads(json.dumps(record, default=str))


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
        legacy = parse_environment_signal_record(content)
        if legacy is not None:
            return signal_from_event_like_record(legacy)
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
    }


def _signal_from_dict(data: dict[str, Any]) -> ResidentInboxSignal:
    return ResidentInboxSignal(
        id=str(data.get("id") or "signal"),
        source=str(data.get("source") or "resident"),
        kind=str(data.get("kind") or "signal"),
        summary=str(data.get("summary") or "Resident inbox signal"),
        payload=dict(data.get("payload") or {}),
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
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _parse_optional_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    return _parse_datetime(text)


def _signal_filename(signal: ResidentInboxSignal) -> str:
    observed = signal.observed_at or signal.created_at.isoformat()
    stamp = _slug(observed.replace("+00:00", "Z")) or signal.created_at.strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{_slug(signal.id) or 'signal'}.md"
