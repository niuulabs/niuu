"""Resident inbox intake, triage, and portfolio conversion."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from ravn.domain.resident_continuation import ResidentPolicyObservation
from ravn.domain.resident_expert import ResidentDomainModel
from ravn.domain.resident_opportunity import ResidentOpportunitySignal
from ravn.domain.resident_portfolio import (
    ResidentObjective,
    ResidentObjectiveKind,
    ResidentObjectiveStatus,
    ResidentPortfolio,
    ResidentWorkItemBackend,
)
from ravn.ports.mimir import MimirPort
from ravn.resident_continuation import _compact_line, _slug
from ravn.resident_opportunity import (
    parse_environment_signal_record,
    render_environment_signal_record,
)
from ravn.resident_portfolio import _merge_text, merge_objectives

_INBOX_SIGNAL_PREFIX = "resident/inbox/signals"
_INBOX_TRIAGE_PREFIX = "resident/inbox/triage"
_INBOX_DECISION_PREFIX = "resident/inbox/decisions"
_INBOX_SIGNAL_JSON_START = "<resident-inbox-signal-json>"
_INBOX_SIGNAL_JSON_END = "</resident-inbox-signal-json>"
_INBOX_TRIAGE_JSON_START = "<resident-inbox-triage-json>"
_INBOX_TRIAGE_JSON_END = "</resident-inbox-triage-json>"


class ResidentInboxClassification(StrEnum):
    FACT = "fact"
    PREFERENCE = "preference"
    POLICY = "policy"
    APPROVAL = "approval"
    DENIAL = "denial"
    TASK_REQUEST = "task_request"
    IDEA = "idea"
    SOURCE_EVIDENCE = "source_evidence"
    CORRECTION = "correction"
    RISK = "risk"
    STATUS_UPDATE = "status_update"
    FILE_REFERENCE = "file_reference"
    URL_REFERENCE = "url_reference"
    PHYSICAL_OBSERVATION = "physical_observation"
    UNKNOWN = "unknown"


class ResidentInboxStatus(StrEnum):
    NEW = "new"
    TRIAGED = "triaged"
    ATTACHED = "attached"
    CONVERTED = "converted"
    REMEMBERED = "remembered"
    IGNORED = "ignored"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class ResidentInboxSignal:
    id: str
    source: str
    kind: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    raw_ref: str = ""
    classification: str = ResidentInboxClassification.UNKNOWN.value
    confidence: float = 0.5
    status: str = ResidentInboxStatus.NEW.value
    evidence_refs: tuple[str, ...] = ()
    target_objective_id: str = ""
    reason: str = ""
    observed_at: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    processed_at: datetime | None = None

    def with_updates(self, **updates: object) -> ResidentInboxSignal:
        values = {
            "id": self.id,
            "source": self.source,
            "kind": self.kind,
            "summary": self.summary,
            "payload": self.payload,
            "raw_ref": self.raw_ref,
            "classification": self.classification,
            "confidence": self.confidence,
            "status": self.status,
            "evidence_refs": self.evidence_refs,
            "target_objective_id": self.target_objective_id,
            "reason": self.reason,
            "observed_at": self.observed_at,
            "created_at": self.created_at,
            "processed_at": self.processed_at,
        }
        values.update(updates)
        return ResidentInboxSignal(**values)  # type: ignore[arg-type]


@dataclass(frozen=True)
class ResidentInboxTriage:
    signal_id: str
    classification: str
    decision: str
    reason: str
    signal_ref: str
    objective_ref: str = ""
    memory_ref: str = ""
    target_objective_id: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class ResidentInboxRun:
    mandate: str
    processed: tuple[ResidentInboxTriage, ...]
    persisted_refs: tuple[str, ...]
    final_suggested_next_action: str


class ResidentInboxBackend(Protocol):
    async def write_signal(self, signal: ResidentInboxSignal) -> str: ...

    async def list_signals(
        self,
        *,
        status: str = ResidentInboxStatus.NEW.value,
        limit: int = 10,
    ) -> list[tuple[str, ResidentInboxSignal]]: ...

    async def write_triage(self, triage: ResidentInboxTriage) -> str: ...

    async def append_decision(self, entry: str) -> str: ...


@dataclass(frozen=True)
class ResidentInboxConfig:
    max_signals_per_wake: int = 5
    create_objectives: bool = True
    attach_to_existing_objectives: bool = True
    min_attach_score: int = 2


class MimirResidentInbox(ResidentInboxBackend):
    """Mimir-backed resident inbox under ``resident/inbox/...``."""

    def __init__(
        self,
        mimir: MimirPort,
        *,
        signal_prefix: str = _INBOX_SIGNAL_PREFIX,
        triage_prefix: str = _INBOX_TRIAGE_PREFIX,
        decision_prefix: str = _INBOX_DECISION_PREFIX,
    ) -> None:
        self._mimir = mimir
        self._signal_prefix = signal_prefix.strip("/").strip() or _INBOX_SIGNAL_PREFIX
        self._triage_prefix = triage_prefix.strip("/").strip() or _INBOX_TRIAGE_PREFIX
        self._decision_prefix = decision_prefix.strip("/").strip() or _INBOX_DECISION_PREFIX

    async def write_event(self, event: Any) -> str:
        signal = signal_from_event(event)
        return await self.write_signal(signal)

    async def write_directed_message(
        self,
        *,
        content: str,
        metadata: dict[str, Any] | None = None,
        source: str = "skuld:directed_message",
    ) -> str:
        signal = signal_from_directed_message(content, metadata=metadata, source=source)
        return await self.write_signal(signal)

    async def write_signal(self, signal: ResidentInboxSignal) -> str:
        path = f"{self._signal_prefix}/{_signal_filename(signal)}"
        try:
            existing = parse_inbox_signal(await self._mimir.read_page(path))
        except FileNotFoundError:
            existing = None
        if (
            existing is not None
            and existing.status != ResidentInboxStatus.NEW.value
            and signal.status == ResidentInboxStatus.NEW.value
        ):
            return path
        await self._mimir.upsert_page(path, render_inbox_signal(signal))
        return path

    async def list_signals(
        self,
        *,
        status: str = ResidentInboxStatus.NEW.value,
        limit: int = 10,
    ) -> list[tuple[str, ResidentInboxSignal]]:
        metas = await self._mimir.list_pages(prefix=self._signal_prefix)
        items: list[tuple[str, ResidentInboxSignal]] = []
        for meta in sorted(metas, key=lambda page: getattr(page, "path", ""), reverse=True):
            path = str(getattr(meta, "path", "") or "")
            if not path:
                continue
            try:
                signal = parse_inbox_signal(await self._mimir.read_page(path))
            except FileNotFoundError:
                continue
            if signal is None:
                continue
            if status and signal.status != status:
                continue
            items.append((path, signal))
            if len(items) >= limit:
                break
        return items

    async def collect(
        self,
        *,
        mandate: str,
        domain_model: ResidentDomainModel | None,
        objectives: tuple[ResidentObjective, ...],
        limit: int,
    ) -> tuple[ResidentOpportunitySignal, ...]:
        del mandate, domain_model
        if limit <= 0:
            return ()
        used_refs = {
            source_evidence
            for objective in objectives
            for source_evidence in objective.source_evidence
        }
        rows = await self.list_signals(status="", limit=max(limit * 4, limit))
        signals: list[ResidentOpportunitySignal] = []
        for path, signal in rows:
            if any(path in source_evidence for source_evidence in used_refs):
                continue
            if signal.classification not in {
                ResidentInboxClassification.IDEA.value,
                ResidentInboxClassification.SOURCE_EVIDENCE.value,
                ResidentInboxClassification.TASK_REQUEST.value,
                ResidentInboxClassification.URL_REFERENCE.value,
                ResidentInboxClassification.PHYSICAL_OBSERVATION.value,
            }:
                continue
            signals.append(
                ResidentOpportunitySignal(
                    id=signal.id,
                    source=signal.source,
                    kind=signal.kind,
                    summary=signal.summary,
                    evidence_ref=path,
                    themes=tuple(_keywords(signal.summary)[:4]),
                    outcomes=_inbox_outcomes(signal),
                    confidence=signal.confidence,
                )
            )
            if len(signals) >= limit:
                break
        return tuple(signals)

    async def write_triage(self, triage: ResidentInboxTriage) -> str:
        stamp = triage.created_at.strftime("%Y%m%dT%H%M%S%fZ")
        slug = _slug(triage.signal_id) or "signal"
        path = f"{self._triage_prefix}/{stamp}-{slug}.md"
        await self._mimir.upsert_page(path, render_inbox_triage(triage))
        return path

    async def append_decision(self, entry: str) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        path = f"{self._decision_prefix}/{stamp}.md"
        await self._mimir.upsert_page(path, f"# Resident Inbox Decision\n\n{entry}\n")
        return path


class ResidentInboxRuntime:
    """Classify resident inbox signals and connect them to memory/work."""

    def __init__(
        self,
        *,
        inbox: ResidentInboxBackend,
        work: ResidentWorkItemBackend,
        memory: Any | None = None,
        config: ResidentInboxConfig | None = None,
    ) -> None:
        self._inbox = inbox
        self._work = work
        self._memory = memory
        self._config = config or ResidentInboxConfig()

    async def run(self, mandate: str) -> ResidentInboxRun:
        rows = await self._inbox.list_signals(
            status=ResidentInboxStatus.NEW.value,
            limit=max(1, self._config.max_signals_per_wake),
        )
        objectives = tuple(await self._work.list_objectives(mandate))
        triages: list[ResidentInboxTriage] = []
        refs: list[str] = []
        current_objectives = objectives
        for signal_ref, signal in rows:
            triage, updated_objectives, persisted = await self._process_signal(
                mandate,
                signal_ref,
                signal,
                current_objectives,
            )
            current_objectives = updated_objectives
            triages.append(triage)
            refs.extend(persisted)
        if triages:
            refs.append(
                await self._inbox.append_decision(
                    (
                        f"{datetime.now(UTC).isoformat()} [resident_inbox] "
                        f"processed={len(triages)} "
                        f"decisions={','.join(item.decision for item in triages)}"
                    )
                )
            )
        return ResidentInboxRun(
            mandate=mandate,
            processed=tuple(triages),
            persisted_refs=tuple(refs),
            final_suggested_next_action=_inbox_next_action(triages),
        )

    async def _process_signal(
        self,
        mandate: str,
        signal_ref: str,
        signal: ResidentInboxSignal,
        objectives: tuple[ResidentObjective, ...],
    ) -> tuple[ResidentInboxTriage, tuple[ResidentObjective, ...], tuple[str, ...]]:
        classification, confidence, reason = classify_inbox_signal(signal)
        classified = signal.with_updates(
            classification=classification,
            confidence=confidence,
            reason=reason,
            processed_at=datetime.now(UTC),
        )
        refs: list[str] = []
        updated_objectives = objectives
        memory_ref = ""
        objective_ref = ""
        target_id = ""
        decision = ResidentInboxStatus.IGNORED.value
        operator_resolution = _operator_resolution_for_signal(classified, objectives)
        if operator_resolution is not None:
            objective = operator_resolution
            objective_ref = await self._work.write_objective(objective)
            refs.append(objective_ref)
            target_id = objective.id
            updated_objectives = tuple(
                item if item.id != objective.id else objective for item in objectives
            )
            await self._persist_portfolio(mandate, updated_objectives)
            decision = ResidentInboxStatus.ATTACHED.value
            classified = classified.with_updates(
                status=ResidentInboxStatus.ATTACHED.value,
                target_objective_id=objective.id,
                evidence_refs=(signal_ref,),
            )
        elif classification in _MEMORY_CLASSIFICATIONS:
            memory_ref = await self._write_memory_observation(classified)
            if memory_ref:
                refs.append(memory_ref)
            decision = ResidentInboxStatus.REMEMBERED.value
            classified = classified.with_updates(status=ResidentInboxStatus.REMEMBERED.value)
        elif classification in _WORK_CLASSIFICATIONS:
            objective, action = _objective_for_signal(
                mandate,
                signal_ref,
                classified,
                objectives,
                config=self._config,
            )
            if objective is not None:
                objective_ref = await self._work.write_objective(objective)
                refs.append(objective_ref)
                target_id = objective.id
                updated_objectives = tuple(
                    item if item.id != objective.id else objective
                    for item in objectives
                )
                if all(item.id != objective.id for item in objectives):
                    updated_objectives = (*objectives, objective)
                await self._persist_portfolio(mandate, updated_objectives)
                decision = action
                classified = classified.with_updates(
                    status=action,
                    target_objective_id=objective.id,
                    evidence_refs=(signal_ref,),
                )
            else:
                decision = ResidentInboxStatus.IGNORED.value
                classified = classified.with_updates(status=ResidentInboxStatus.IGNORED.value)
        else:
            classified = classified.with_updates(status=ResidentInboxStatus.IGNORED.value)

        refs.append(await self._inbox.write_signal(classified))
        triage = ResidentInboxTriage(
            signal_id=classified.id,
            classification=classification,
            decision=decision,
            reason=reason,
            signal_ref=signal_ref,
            objective_ref=objective_ref,
            memory_ref=memory_ref,
            target_objective_id=target_id,
        )
        refs.append(await self._inbox.write_triage(triage))
        return triage, updated_objectives, tuple(refs)

    async def _write_memory_observation(self, signal: ResidentInboxSignal) -> str:
        if self._memory is None or not hasattr(self._memory, "write_policy_observation"):
            return ""
        observation = ResidentPolicyObservation(
            subject=f"resident-inbox:{signal.classification}",
            observation=signal.summary,
            source=signal.source,
            status="candidate",
        )
        return await self._memory.write_policy_observation(observation)

    async def _persist_portfolio(
        self,
        mandate: str,
        objectives: tuple[ResidentObjective, ...],
    ) -> str:
        portfolio = await self._work.read_portfolio(mandate)
        if portfolio is None:
            portfolio = ResidentPortfolio(mandate=mandate)
        return await self._work.write_portfolio(
            portfolio.with_objectives(
                merge_objectives(objectives),
                decision_history=_merge_text(
                    portfolio.decision_history,
                    (
                        f"{datetime.now(UTC).isoformat()} "
                        "[resident_inbox] converted inbox signal into resident work",
                    ),
                ),
            )
        )


_MEMORY_CLASSIFICATIONS = {
    ResidentInboxClassification.FACT.value,
    ResidentInboxClassification.PREFERENCE.value,
    ResidentInboxClassification.POLICY.value,
    ResidentInboxClassification.APPROVAL.value,
    ResidentInboxClassification.DENIAL.value,
    ResidentInboxClassification.CORRECTION.value,
    ResidentInboxClassification.RISK.value,
    ResidentInboxClassification.STATUS_UPDATE.value,
}

_WORK_CLASSIFICATIONS = {
    ResidentInboxClassification.TASK_REQUEST.value,
    ResidentInboxClassification.IDEA.value,
    ResidentInboxClassification.SOURCE_EVIDENCE.value,
    ResidentInboxClassification.URL_REFERENCE.value,
    ResidentInboxClassification.FILE_REFERENCE.value,
    ResidentInboxClassification.PHYSICAL_OBSERVATION.value,
}


def signal_from_event(event: Any) -> ResidentInboxSignal:
    record = _signal_record_from_event(event)
    summary = str(record.get("summary") or "Resident inbox signal observed.")
    classification, confidence, reason = classify_text(summary, payload=record)
    return ResidentInboxSignal(
        id=str(record.get("id") or _slug(summary) or "signal"),
        source=str(record.get("source") or "environment"),
        kind=str(record.get("kind") or "signal"),
        summary=summary,
        payload=record,
        raw_ref=str(record.get("evidence_ref") or ""),
        classification=classification,
        confidence=confidence,
        reason=reason,
        observed_at=str(record.get("observed_at") or ""),
    )


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


def classify_inbox_signal(signal: ResidentInboxSignal) -> tuple[str, float, str]:
    return classify_text(signal.summary, payload=signal.payload)


def classify_text(text: str, *, payload: dict[str, Any] | None = None) -> tuple[str, float, str]:
    payload = payload or {}
    haystack = " ".join(
        str(item or "")
        for item in (
            text,
            payload.get("kind"),
            payload.get("signal_kind"),
            payload.get("source"),
            payload.get("evidence_ref"),
            payload.get("raw_payload_ref"),
        )
    ).casefold()
    if _contains_url(haystack):
        return (
            ResidentInboxClassification.SOURCE_EVIDENCE.value,
            0.86,
            "message contains a URL or public source reference",
        )
    if str(payload.get("kind") or "").startswith("signal.") or str(
        payload.get("signal_kind") or ""
    ):
        return (
            ResidentInboxClassification.SOURCE_EVIDENCE.value,
            0.78,
            "environment signal is source evidence for resident attention",
        )
    if re.search(r"\b(not approved|do not approve|don't approve|denied|deny|no)\b", haystack):
        return ResidentInboxClassification.DENIAL.value, 0.9, "operator text denies approval"
    if re.search(r"\b(approved|yes|go ahead|proceed|allowed)\b", haystack):
        return ResidentInboxClassification.APPROVAL.value, 0.84, "operator text grants approval"
    if re.search(r"\b(policy|rule|always ask|never|must not|requires approval)\b", haystack):
        return ResidentInboxClassification.POLICY.value, 0.82, "message states a policy boundary"
    if re.search(r"\b(prefer|preference|i like|i hate|avoid)\b", haystack):
        return ResidentInboxClassification.PREFERENCE.value, 0.78, "message states a preference"
    if re.search(r"\b(correction|actually|instead|wrong|fix that)\b", haystack):
        return ResidentInboxClassification.CORRECTION.value, 0.72, "message corrects prior state"
    if re.search(
        r"\b(look into|please|can you|could you|todo|task|need you to|"
        r"do some research|research|investigate|find out|explore)\b",
        haystack,
    ):
        return ResidentInboxClassification.TASK_REQUEST.value, 0.75, "message asks for work"
    if re.search(r"\b(idea|maybe|what if|could add|opportunity)\b", haystack):
        return ResidentInboxClassification.IDEA.value, 0.68, "message suggests an idea"
    if re.search(r"\b(print failed|failed print|support|slicer|printer|filament|resin)\b", haystack):
        return (
            ResidentInboxClassification.PHYSICAL_OBSERVATION.value,
            0.8,
            "message describes physical-world or print state",
        )
    if re.search(r"\b(risk|unsafe|danger|customer data|private)\b", haystack):
        return ResidentInboxClassification.RISK.value, 0.76, "message identifies a risk"
    if re.search(r"\b(status|done|completed|blocked|waiting)\b", haystack):
        return ResidentInboxClassification.STATUS_UPDATE.value, 0.64, "message reports status"
    if re.search(r"(/[^\s]+|\b[a-z0-9_.-]+\.(md|json|yaml|yml|stl|3mf|py|ts|tsx)\b)", haystack):
        return ResidentInboxClassification.FILE_REFERENCE.value, 0.72, "message references a file"
    return ResidentInboxClassification.FACT.value, 0.55, "message is useful resident context"


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


def signal_from_event_like_record(record: dict[str, Any]) -> ResidentInboxSignal:
    summary = str(record.get("summary") or "Resident inbox signal observed.")
    classification, confidence, reason = classify_text(summary, payload=record)
    return ResidentInboxSignal(
        id=str(record.get("id") or _slug(summary) or "signal"),
        source=str(record.get("source") or "environment"),
        kind=str(record.get("kind") or "environment_signal"),
        summary=summary,
        payload=dict(record),
        raw_ref=str(record.get("evidence_ref") or ""),
        classification=classification,
        confidence=confidence,
        reason=reason,
        observed_at=str(record.get("observed_at") or ""),
    )


def _signal_record_from_event(event: Any) -> dict[str, Any]:
    rendered = render_environment_signal_record(_environment_signal_record(event))
    parsed = parse_environment_signal_record(rendered)
    return parsed or {}


def _environment_signal_record(event: Any) -> dict[str, Any]:
    payload = dict(getattr(event, "payload", {}) or {})
    data = payload.get("data")
    if not isinstance(data, dict):
        data = {}
    event_type = str(getattr(event, "event_type", "") or payload.get("event_type") or "")
    severity = str(payload.get("severity") or data.get("severity") or "info")
    event_id = str(
        data.get("provider_event_id")
        or data.get("dedupe_key")
        or payload.get("event_id")
        or getattr(event, "event_id", "")
        or getattr(event, "correlation_id", "")
        or event_type
    )
    observed_at = str(data.get("observed_at") or getattr(event, "timestamp", "") or "")
    explicit_summary = str(getattr(event, "summary", "") or "").strip()
    message = str(data.get("message") or data.get("reason") or data.get("subject") or "").strip()
    if explicit_summary and message and message not in explicit_summary:
        summary = f"{explicit_summary}: {message}"
    else:
        summary = explicit_summary or message or event_type
    return {
        "id": event_id,
        "source": str(data.get("source_id") or payload.get("signal_source") or "environment"),
        "kind": event_type or str(payload.get("signal_kind") or "environment_signal"),
        "summary": _compact_line(summary or "Environment signal observed.", limit=220),
        "evidence_ref": str(data.get("raw_payload_ref") or ""),
        "severity": severity,
        "observed_at": observed_at,
        "payload": payload,
    }


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


def _objective_for_signal(
    mandate: str,
    signal_ref: str,
    signal: ResidentInboxSignal,
    objectives: tuple[ResidentObjective, ...],
    *,
    config: ResidentInboxConfig,
) -> tuple[ResidentObjective | None, str]:
    if config.attach_to_existing_objectives:
        existing = _best_matching_objective(signal, objectives, config.min_attach_score)
        if existing is not None:
            return (
                existing.with_updates(
                    source_evidence=_append_unique(existing.source_evidence, signal_ref),
                    proof_progress=_append_unique(
                        existing.proof_progress,
                        f"inbox signal attached: {signal.id}",
                    ),
                    last_reviewed_at=datetime.now(UTC),
                ),
                ResidentInboxStatus.ATTACHED.value,
            )
    if not config.create_objectives:
        return None, ResidentInboxStatus.IGNORED.value
    objective = ResidentObjective(
        id=f"inbox-{_slug(signal.summary)[:80] or _slug(signal.id) or 'signal'}",
        title=f"Follow up inbox signal: {_compact_line(signal.summary, limit=80)}",
        purpose="Turn a resident inbox signal into useful, safe domain progress.",
        serves_mandate_because=(
            "The resident received new external or operator-provided context and should "
            "decide whether it changes the work portfolio."
        ),
        expected_outcome="A researched, attached, or resolved resident follow-up with evidence.",
        proof_criteria=(
            "The inbox signal is cited as source evidence.",
            "The resident records whether it acted, ignored, asked, or delegated.",
            "No spend, physical operation, external account change, or customer contact occurs without approval.",
        ),
        kind=_objective_kind_for_signal(signal),
        risk_boundaries=_risk_boundaries_for_signal(signal),
        priority_score=_priority_for_signal(signal),
        priority_band="inbox",
        priority_rationale=f"Inbox classification {signal.classification}: {signal.reason}",
        source_evidence=(signal_ref,),
        reasoning=f"Created from resident inbox signal {signal.id}. Mandate: {_compact_line(mandate, limit=160)}",
    )
    return objective, ResidentInboxStatus.CONVERTED.value


def _operator_resolution_for_signal(
    signal: ResidentInboxSignal,
    objectives: tuple[ResidentObjective, ...],
) -> ResidentObjective | None:
    approval = _approval_for_signal(signal)
    if approval is None:
        return None
    pending = tuple(
        objective
        for objective in objectives
        if objective.pending_question
        or objective.status == ResidentObjectiveStatus.NEEDS_OPERATOR.value
    )
    if len(pending) != 1:
        return None
    objective = pending[0]
    marker = (
        "operator inbox approval"
        if approval
        else "operator inbox denial"
    )
    return objective.with_updates(
        status=(
            ResidentObjectiveStatus.CANDIDATE.value
            if approval
            else ResidentObjectiveStatus.BLOCKED.value
        ),
        pending_question="",
        source_evidence=_append_unique(objective.source_evidence, signal.id),
        proof_progress=_append_unique(
            objective.proof_progress,
            f"{marker}: {signal.summary}",
        ),
        last_reviewed_at=datetime.now(UTC),
    )


def _approval_for_signal(signal: ResidentInboxSignal) -> bool | None:
    if signal.classification == ResidentInboxClassification.DENIAL.value:
        return False
    if signal.classification != ResidentInboxClassification.APPROVAL.value:
        return None
    text = f"{signal.summary} {json.dumps(signal.payload, default=str)}".casefold()
    if any(item in text for item in ("not approved", "do not", "don't", "deny", "denied")):
        return False
    if any(item in text for item in ("approved", "yes", "use ", "go ahead", "answered")):
        return True
    return None


def _best_matching_objective(
    signal: ResidentInboxSignal,
    objectives: tuple[ResidentObjective, ...],
    min_score: int,
) -> ResidentObjective | None:
    signal_terms = set(_keywords(signal.summary))
    best: tuple[int, ResidentObjective] | None = None
    for objective in objectives:
        objective_terms = set(
            _keywords(
                " ".join(
                    (
                        objective.id,
                        objective.title,
                        objective.purpose,
                        " ".join(objective.source_evidence),
                    )
                )
            )
        )
        score = len(signal_terms & objective_terms)
        if score >= min_score and (best is None or score > best[0]):
            best = (score, objective)
    return best[1] if best is not None else None


def _objective_kind_for_signal(signal: ResidentInboxSignal) -> str:
    if signal.classification == ResidentInboxClassification.PHYSICAL_OBSERVATION.value:
        return ResidentObjectiveKind.VERIFICATION.value
    if signal.classification == ResidentInboxClassification.IDEA.value:
        return ResidentObjectiveKind.CREATIVE_EXPLORATION.value
    return ResidentObjectiveKind.RESEARCH.value


def _risk_boundaries_for_signal(signal: ResidentInboxSignal) -> tuple[str, ...]:
    text = f"{signal.summary} {json.dumps(signal.payload, default=str)}".casefold()
    risks: list[str] = []
    if any(item in text for item in ("spend", "buy", "purchase", "ads", "subscription")):
        risks.append("spending")
    if any(item in text for item in ("printer", "print failed", "physical", "machine")):
        risks.append("physical_operation")
    if any(item in text for item in ("customer", "private", "email", "address")):
        risks.append("customer_or_private_data")
    return tuple(risks)


def _priority_for_signal(signal: ResidentInboxSignal) -> int:
    base = int(max(1.0, min(10.0, signal.confidence * 10)))
    if signal.classification in {
        ResidentInboxClassification.TASK_REQUEST.value,
        ResidentInboxClassification.SOURCE_EVIDENCE.value,
    }:
        base += 8
    if signal.classification == ResidentInboxClassification.PHYSICAL_OBSERVATION.value:
        base += 6
    return min(base, 25)


def _append_unique(items: tuple[str, ...], item: str) -> tuple[str, ...]:
    value = item.strip()
    if not value or value in items:
        return items
    return (*items, value)


def _keywords(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text.casefold())
    stop = {
        "and",
        "the",
        "for",
        "that",
        "this",
        "with",
        "into",
        "resident",
        "signal",
        "inbox",
        "from",
        "should",
        "could",
        "would",
    }
    return [word for word in words if word not in stop]


def _contains_url(text: str) -> bool:
    return bool(re.search(r"https?://|www\.|[a-z0-9.-]+\.(com|ca|net|org|io)\b", text))


def _inbox_outcomes(signal: ResidentInboxSignal) -> tuple[str, ...]:
    outcomes = ["resident awareness", "lower manual effort"]
    if signal.kind.startswith("signal."):
        outcomes = (*outcomes, "environment health", "reliable operation")
    if signal.classification in {
        ResidentInboxClassification.SOURCE_EVIDENCE.value,
        ResidentInboxClassification.URL_REFERENCE.value,
    }:
        outcomes = (*outcomes, "better source-backed decisions")
    if signal.classification == ResidentInboxClassification.PHYSICAL_OBSERVATION.value:
        outcomes = (*outcomes, "printability and quality")
    return outcomes


def _inbox_next_action(triages: list[ResidentInboxTriage]) -> str:
    if not triages:
        return "Sleep until a new resident inbox signal arrives."
    converted = [item for item in triages if item.decision == ResidentInboxStatus.CONVERTED.value]
    attached = [item for item in triages if item.decision == ResidentInboxStatus.ATTACHED.value]
    remembered = [item for item in triages if item.decision == ResidentInboxStatus.REMEMBERED.value]
    if converted:
        return f"Review and advance {len(converted)} inbox-created objective(s)."
    if attached:
        return f"Advance objective(s) updated with {len(attached)} inbox signal(s)."
    if remembered:
        return f"Use {len(remembered)} remembered inbox observation(s) in future decisions."
    return "No actionable inbox signal remained after triage."
