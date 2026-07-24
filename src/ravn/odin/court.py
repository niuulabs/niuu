"""ODIN court resolver for resident Valkyrie judgments and actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from niuu.observability import get_observability
from ravn.odin.review import ReviewItem, ReviewKind, ReviewRequester
from sleipnir.domain import registry
from sleipnir.domain.catalog import (
    attention_decision_made,
    odin_court_decided,
    room_opened,
    valkyrie_action_requested,
)
from sleipnir.domain.events import SleipnirEvent
from sleipnir.ports.events import SleipnirPublisher, SleipnirSubscriber, Subscription

_SUBSCRIBED_EVENT_TYPES = [
    registry.VALKYRIE_JUDGMENT_PROPOSED,
    registry.VALKYRIE_JUDGMENT_REJECTED,
    registry.VALKYRIE_ACTION_PROPOSED,
    registry.VALKYRIE_STATE_UPDATED,
    registry.ENVIRONMENT_STATE_CHANGED,
    "signal.*",
]

_TIER_PRIORITY = {"silent": 0, "ambient": 1, "present": 2, "urgent": 3}
_AUTONOMOUS_AUTHORITIES = {"autonomous", "yolo", "self_authorized"}
_REVIEW_AUTHORITIES = {
    "court_required",
    "human_review",
    "human_review_required",
    "human_required",
    "manual",
    "requires_review",
}


class CourtAuditSink(Protocol):
    """Port for persisting ODIN court decisions to Mimir or episodic memory."""

    async def record_decision(self, record: CourtDecisionRecord) -> None:
        """Persist an auditable court decision record."""


@dataclass(frozen=True)
class CourtDecisionRecord:
    """Replayable audit record for one resolved ODIN court case."""

    audit_ref: str
    environment_id: str
    root_correlation_id: str
    decision: str
    tier: str
    action_authorization: str
    escalation_path: str
    huddle_id: str
    judgment_refs: list[str]
    action_refs: list[str]
    rejected_refs: list[str]
    dissent: list[dict]
    evidence: list[dict]
    rationale: str
    created_at: datetime
    raw_event_ids: list[str]


class InMemoryCourtAuditSink:
    """Simple audit sink for tests and single-process resident deployments."""

    def __init__(self) -> None:
        self._records: list[CourtDecisionRecord] = []

    async def record_decision(self, record: CourtDecisionRecord) -> None:
        self._records.append(record)

    def records(self) -> list[CourtDecisionRecord]:
        return list(self._records)

    def find(self, *, environment_id: str, root_correlation_id: str) -> CourtDecisionRecord | None:
        for record in reversed(self._records):
            if (
                record.environment_id == environment_id
                and record.root_correlation_id == root_correlation_id
            ):
                return record
        return None


@dataclass
class _CourtCase:
    environment_id: str
    root_correlation_id: str
    created_at: datetime
    updated_at: datetime
    tenant_id: str | None
    raw_events: list[SleipnirEvent] = field(default_factory=list)
    signals: list[SleipnirEvent] = field(default_factory=list)
    judgments: list[SleipnirEvent] = field(default_factory=list)
    rejected_judgments: list[SleipnirEvent] = field(default_factory=list)
    actions: list[SleipnirEvent] = field(default_factory=list)
    resolved: bool = False


class OdinCourt:
    """Fan-in resolver for resident Valkyrie attention and action decisions."""

    def __init__(
        self,
        *,
        publisher: SleipnirPublisher,
        subscriber: SleipnirSubscriber,
        audit_sink: CourtAuditSink | None = None,
        review_requester: ReviewRequester | None = None,
        court_id: str = "odin-court",
        quorum_size: int = 2,
        timeout_s: float = 30.0,
    ) -> None:
        if quorum_size < 1:
            raise ValueError("quorum_size must be >= 1")
        if timeout_s < 0:
            raise ValueError("timeout_s must be >= 0")
        self._publisher = publisher
        self._subscriber = subscriber
        self._audit_sink = audit_sink or InMemoryCourtAuditSink()
        self._review_requester = review_requester
        self._court_id = court_id
        self._quorum_size = quorum_size
        self._timeout = timedelta(seconds=timeout_s)
        self._subscription: Subscription | None = None
        self._cases: dict[tuple[str, str], _CourtCase] = {}
        self._decisions: list[CourtDecisionRecord] = []

    @property
    def is_running(self) -> bool:
        return self._subscription is not None

    async def start(self) -> None:
        """Subscribe the court to resident Environment/Valkyrie events."""
        if self._subscription is not None:
            return
        self._subscription = await self._subscriber.subscribe(
            _SUBSCRIBED_EVENT_TYPES,
            self._handle_event,
        )

    async def stop(self) -> None:
        """Unsubscribe from Sleipnir."""
        if self._subscription is not None:
            await self._subscription.unsubscribe()
            self._subscription = None

    def audit_records(self) -> list[CourtDecisionRecord]:
        return list(self._decisions)

    async def sweep_expired(self, *, now: datetime | None = None) -> None:
        """Resolve cases that have enough evidence or have aged out."""
        current = now or datetime.now(UTC)
        cases = list(self._cases.values())
        for case in cases:
            if case.resolved:
                continue
            has_decidable_input = bool(case.judgments or case.actions)
            if has_decidable_input and current - case.updated_at >= self._timeout:
                await self._resolve_case(case)

    async def _handle_event(self, event: SleipnirEvent) -> None:
        fields = _extract_fields(event)
        environment_id = _non_empty_str(fields.get("environment_id")) or "environment:unknown"
        root_correlation_id = _root_correlation_id(event, fields)
        key = (environment_id, root_correlation_id)
        case = self._cases.get(key)
        if case is None:
            case = _CourtCase(
                environment_id=environment_id,
                root_correlation_id=root_correlation_id,
                created_at=event.timestamp,
                updated_at=event.timestamp,
                tenant_id=event.tenant_id,
            )
            self._cases[key] = case

        case.raw_events.append(event)
        case.updated_at = max(case.updated_at, event.timestamp)
        if case.tenant_id is None:
            case.tenant_id = event.tenant_id

        if event.event_type == registry.VALKYRIE_JUDGMENT_PROPOSED:
            case.judgments.append(event)
        elif event.event_type == registry.VALKYRIE_JUDGMENT_REJECTED:
            case.rejected_judgments.append(event)
        elif event.event_type == registry.VALKYRIE_ACTION_PROPOSED:
            case.actions.append(event)
        else:
            case.signals.append(event)

        if self._case_ready(case):
            await self._resolve_case(case)

    def _case_ready(self, case: _CourtCase) -> bool:
        if case.resolved:
            return False
        return len(case.judgments) + len(case.actions) >= self._quorum_size

    async def _resolve_case(self, case: _CourtCase) -> None:
        telemetry = get_observability()
        attributes = {
            "ravn.environment.id": case.environment_id,
            "ravn.task.root_correlation_id": case.root_correlation_id,
            "ravn.odin.judgment_count": len(case.judgments),
            "ravn.odin.action_count": len(case.actions),
            "ravn.odin.rejected_count": len(case.rejected_judgments),
        }
        with telemetry.span("ravn.odin.resolve_case", attributes=attributes) as span:
            try:
                await self._resolve_case_observed(case)
            except Exception as exc:
                telemetry.mark_error(span, type(exc).__name__)
                raise

    async def _resolve_case_observed(self, case: _CourtCase) -> None:
        if case.resolved:
            return
        case.resolved = True

        tier = _highest_tier(case.judgments)
        action_authorization = _action_authorization(case.judgments, case.actions)
        decision = _decision_for(tier, action_authorization, bool(case.actions))
        escalation_path = _escalation_path(decision, case)
        huddle_id = (
            _stable_id("huddle", case.environment_id, case.root_correlation_id)
            if decision == "open_huddle"
            else ""
        )
        audit_ref = _stable_id("odin-court", case.environment_id, case.root_correlation_id)
        evidence = _evidence_for(case)
        dissent = _dissent_for(case)
        rationale = _rationale_for(decision, tier, action_authorization, case)
        telemetry = get_observability()
        decision_attributes = {
            "ravn.odin.decision": decision,
            "ravn.odin.tier": tier,
            "ravn.odin.action_authorization": action_authorization,
            "ravn.odin.escalation_path": escalation_path,
        }
        telemetry.set_attributes(decision_attributes)
        telemetry.event(
            "ravn.odin.decision",
            attributes=decision_attributes,
            content={"evidence": evidence, "dissent": dissent, "rationale": rationale},
        )
        telemetry.count("ravn.odin.decisions", attributes=decision_attributes)
        judgment_refs = [event.event_id for event in case.judgments]
        action_refs = [event.event_id for event in case.actions]
        rejected_refs = [event.event_id for event in case.rejected_judgments]

        record = CourtDecisionRecord(
            audit_ref=audit_ref,
            environment_id=case.environment_id,
            root_correlation_id=case.root_correlation_id,
            decision=decision,
            tier=tier,
            action_authorization=action_authorization,
            escalation_path=escalation_path,
            huddle_id=huddle_id,
            judgment_refs=judgment_refs,
            action_refs=action_refs,
            rejected_refs=rejected_refs,
            dissent=dissent,
            evidence=evidence,
            rationale=rationale,
            created_at=datetime.now(UTC),
            raw_event_ids=[event.event_id for event in case.raw_events],
        )
        self._decisions.append(record)
        await self._audit_sink.record_decision(record)

        cause = case.raw_events[-1].event_id if case.raw_events else None
        await self._publisher.publish(
            odin_court_decided(
                environment_id=case.environment_id,
                court_id=audit_ref,
                decision=decision,
                authority_boundary=action_authorization,
                dissent=dissent,
                source=self._court_id,
                correlation_id=case.root_correlation_id,
                causation_id=cause,
                tenant_id=case.tenant_id,
            )
        )
        await self._publisher.publish(
            attention_decision_made(
                environment_id=case.environment_id,
                root_correlation_id=case.root_correlation_id,
                decision=decision,
                tier=tier,
                action_authorization=action_authorization,
                escalation_path=escalation_path,
                huddle_id=huddle_id,
                judgment_refs=judgment_refs,
                action_refs=action_refs,
                dissent=dissent,
                evidence=evidence,
                rationale=rationale,
                audit_ref=audit_ref,
                source=self._court_id,
                correlation_id=case.root_correlation_id,
                causation_id=cause,
                tenant_id=case.tenant_id,
            )
        )

        if decision == "autonomous_action":
            await self._publish_action_request(case)
        elif decision == "open_huddle":
            await self._publish_huddle_opened(case, huddle_id)
        elif decision == "draft_for_review":
            await self._file_escalation_review(case, record)

    async def _file_escalation_review(
        self,
        case: _CourtCase,
        record: CourtDecisionRecord,
    ) -> None:
        """File a draft-for-review action as a ReviewItem — the review queue
        the escalation path names is the unified ODIN review path."""
        if self._review_requester is None:
            return
        action_fields: dict = {}
        if case.actions:
            action_fields = _extract_fields(case.actions[0])
        valkyrie_ids = _valkyrie_ids(case)
        capability = _non_empty_str(action_fields.get("capability")) or "unknown"
        target = action_fields.get("target")
        item = ReviewItem.new(
            kind=ReviewKind.COURT_ESCALATION.value,
            requested_action="execute_action",
            environment_id=case.environment_id,
            valkyrie_id=valkyrie_ids[0] if valkyrie_ids else "",
            title=f"Action draft: {capability}",
            summary=record.rationale,
            urgency={"urgent": 0.9, "present": 0.7}.get(record.tier, 0.5),
            risk_class="high" if record.tier == "urgent" else "medium",
            dedupe_key=f"{ReviewKind.COURT_ESCALATION.value}:{record.audit_ref}",
            evidence={
                "audit_ref": record.audit_ref,
                "tier": record.tier,
                "action_authorization": record.action_authorization,
                "judgment_refs": list(record.judgment_refs),
                "action_refs": list(record.action_refs),
                "rationale": record.rationale,
                "court_evidence": list(record.evidence),
                "dissent": list(record.dissent),
                "action": {
                    "action_id": _non_empty_str(action_fields.get("action_id")),
                    "capability": capability,
                    "valkyrie_id": _non_empty_str(action_fields.get("valkyrie_id")),
                    "target": target if isinstance(target, dict) else {},
                    "dry_run": bool(action_fields.get("dry_run", True)),
                },
            },
            requested_by=self._court_id,
            correlation_id=case.root_correlation_id,
        )
        await self._review_requester.request(item)

    async def _publish_action_request(self, case: _CourtCase) -> None:
        if not case.actions:
            return
        action = case.actions[0]
        fields = _extract_fields(action)
        await self._publisher.publish(
            valkyrie_action_requested(
                environment_id=case.environment_id,
                valkyrie_id=_non_empty_str(fields.get("valkyrie_id")) or "valkyrie:unknown",
                action_id=_non_empty_str(fields.get("action_id")) or action.event_id,
                capability=_non_empty_str(fields.get("capability")) or "unknown",
                authority_boundary=_normal_authority(fields.get("action_authority")),
                target=fields.get("target") if isinstance(fields.get("target"), dict) else {},
                dry_run=bool(fields.get("dry_run", True)),
                source=self._court_id,
                correlation_id=case.root_correlation_id,
                causation_id=action.event_id,
                tenant_id=case.tenant_id,
            )
        )

    async def _publish_huddle_opened(self, case: _CourtCase, huddle_id: str) -> None:
        participants = ["human:operator"]
        for valkyrie_id in _valkyrie_ids(case):
            participants.append(valkyrie_id)
        await self._publisher.publish(
            room_opened(
                environment_id=case.environment_id,
                room_id=huddle_id,
                purpose=f"Resolve {case.environment_id} {case.root_correlation_id}",
                participants=participants,
                source=self._court_id,
                correlation_id=case.root_correlation_id,
                causation_id=case.raw_events[-1].event_id if case.raw_events else None,
                tenant_id=case.tenant_id,
            )
        )


def _extract_fields(event: SleipnirEvent) -> dict:
    payload = event.payload
    outcome = payload.get("outcome")
    if isinstance(outcome, dict):
        merged = dict(outcome)
        for key, value in payload.items():
            if key not in merged:
                merged[key] = value
        return merged
    return payload


def _root_correlation_id(event: SleipnirEvent, fields: dict) -> str:
    correlation_ids = fields.get("correlation_ids")
    if isinstance(correlation_ids, dict):
        root = _non_empty_str(correlation_ids.get("root"))
        if root:
            return root
    return (
        _non_empty_str(fields.get("root_correlation_id"))
        or _non_empty_str(event.correlation_id)
        or event.event_id
    )


def _non_empty_str(value: object) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _highest_tier(judgments: list[SleipnirEvent]) -> str:
    if not judgments:
        return "present"
    return max(
        (_non_empty_str(_extract_fields(event).get("tier")) or "silent" for event in judgments),
        key=lambda tier: _TIER_PRIORITY.get(tier, -1),
    )


def _action_authorization(
    judgments: list[SleipnirEvent],
    actions: list[SleipnirEvent],
) -> str:
    authorities: list[str] = []
    for event in judgments:
        fields = _extract_fields(event)
        authorities.append(_normal_authority(fields.get("action_authority")))
    for event in actions:
        fields = _extract_fields(event)
        authorities.append(_normal_authority(fields.get("action_authority")))
    authorities = [authority for authority in authorities if authority]
    if any(authority in _REVIEW_AUTHORITIES for authority in authorities):
        return "human_review_required"
    if actions and all(authority in _AUTONOMOUS_AUTHORITIES for authority in authorities):
        return "autonomous"
    if actions:
        return "human_review_required"
    return "none"


def _normal_authority(value: object) -> str:
    authority = _non_empty_str(value).lower()
    if authority in _AUTONOMOUS_AUTHORITIES:
        return "autonomous"
    if authority in _REVIEW_AUTHORITIES:
        return "human_review_required"
    if authority == "human":
        return "human_review_required"
    return authority


def _decision_for(tier: str, action_authorization: str, has_action: bool) -> str:
    if has_action and action_authorization == "autonomous":
        return "autonomous_action"
    if has_action:
        return "draft_for_review"
    if tier == "urgent":
        return "open_huddle"
    if tier == "present":
        return "notify"
    if tier == "ambient":
        return "record_only"
    return "no_op"


def _escalation_path(outcome: str, case: _CourtCase) -> str:
    if outcome == "notify":
        return _first_target_surface(case) or "surface:default"
    return {
        "autonomous_action": "action_executor",
        "draft_for_review": "review_queue",
        "open_huddle": "huddle",
        "record_only": "mimir",
    }.get(outcome, "none")


def _first_target_surface(case: _CourtCase) -> str:
    for event in [*case.judgments, *case.actions]:
        surfaces = _extract_fields(event).get("target_surfaces")
        if isinstance(surfaces, list):
            for surface in surfaces:
                value = _non_empty_str(surface)
                if value:
                    return value
    return ""


def _evidence_for(case: _CourtCase) -> list[dict]:
    evidence: list[dict] = []
    seen: set[str] = set()
    for event in [*case.signals, *case.judgments, *case.actions]:
        fields = _extract_fields(event)
        event_evidence = fields.get("evidence")
        if isinstance(event_evidence, list):
            for item in event_evidence:
                if isinstance(item, dict):
                    _append_unique(evidence, seen, item)
        _append_unique(
            evidence,
            seen,
            {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "summary": event.summary,
            },
        )
    return evidence


def _dissent_for(case: _CourtCase) -> list[dict]:
    dissent: list[dict] = []
    _append_value_dissent(dissent, case.judgments, "tier")
    _append_value_dissent(dissent, case.judgments, "recommended_action")
    _append_value_dissent(dissent, case.judgments, "action_authority")
    _append_value_dissent(dissent, case.actions, "action_authority")

    for event in case.judgments:
        refs = _extract_fields(event).get("dissent_refs")
        if isinstance(refs, list):
            for ref in refs:
                value = _non_empty_str(ref)
                if value:
                    dissent.append(
                        {
                            "type": "valkyrie_dissent_ref",
                            "event_id": event.event_id,
                            "ref": value,
                        }
                    )

    for event in case.rejected_judgments:
        fields = _extract_fields(event)
        dissent.append(
            {
                "type": "rejected_judgment",
                "event_id": event.event_id,
                "errors": fields.get("errors", []),
                "rejected_fields": fields.get("rejected_fields", {}),
            }
        )
    return dissent


def _append_value_dissent(
    dissent: list[dict],
    events: list[SleipnirEvent],
    field_name: str,
) -> None:
    values: dict[str, list[str]] = {}
    for event in events:
        value = _non_empty_str(_extract_fields(event).get(field_name))
        if value:
            values.setdefault(value, []).append(event.event_id)
    if len(values) > 1:
        dissent.append(
            {
                "type": f"{field_name}_disagreement",
                "values": values,
            }
        )


def _append_unique(evidence: list[dict], seen: set[str], item: dict) -> None:
    marker = repr(sorted(item.items()))
    if marker in seen:
        return
    seen.add(marker)
    evidence.append(dict(item))


def _rationale_for(
    decision: str,
    tier: str,
    action_authorization: str,
    case: _CourtCase,
) -> str:
    return (
        f"{len(case.judgments)} judgments and {len(case.actions)} action proposals "
        f"resolved to {decision} at {tier} with {action_authorization} authority"
    )


def _valkyrie_ids(case: _CourtCase) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for event in [*case.judgments, *case.actions]:
        value = _non_empty_str(_extract_fields(event).get("valkyrie_id"))
        if value and value not in seen:
            seen.add(value)
            values.append(value)
    return values


def _stable_id(prefix: str, environment_id: str, root_correlation_id: str) -> str:
    raw = f"{prefix}:{environment_id}:{root_correlation_id}"
    return "".join(char if char.isalnum() or char in {":", "-", "_"} else "-" for char in raw)
