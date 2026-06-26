"""Backend-agnostic resident continuation kernel."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from niuu.domain.outcome import OutcomeSchema, parse_outcome_block
from ravn.domain.models import TokenUsage, TurnResult
from ravn.domain.operator_contact import (
    BroadcastThenCallbackOperatorContact,
    CallbackOperatorContact,
    ChannelOperatorContact,
    OperatorContactKind,
    OperatorContactPort,
    OperatorContactPurpose,
    OperatorContactRequest,
    OperatorContactStatus,
    PendingOperatorContact,
)
from ravn.domain.resident_continuation import (
    ContinuationDecisionKind,
    ResidentActionCandidate,
    ResidentBudgetDecision,
    ResidentBudgetLimits,
    ResidentBudgetSnapshot,
    ResidentContinuationContext,
    ResidentContinuationDecision,
    ResidentContinuationRun,
    ResidentMemoryEntry,
    ResidentMemoryPort,
    ResidentPolicyDecision,
    ResidentPolicyDecisionRecord,
    ResidentPolicyObservation,
    ResidentPolicyPort,
    ResidentTurnRecord,
    RiskBoundary,
    selected_action_from_outcome,
)
from ravn.ports.channel import ChannelPort
from ravn.ports.executor import ExecutionAgentPort
from ravn.resident_operator_contact import (
    ResidentOperatorContactConfig,
    ResidentOperatorContactCoordinator,
)

_OPERATOR_NEEDED_PATH = "operator-needed/latest.md"
_OPERATOR_ANSWER_PATH = "operator-answers/latest.md"

_DEFAULT_BOUNDARY_TERMS: dict[str, tuple[str, ...]] = {
    RiskBoundary.SPENDING.value: (
        "spend",
        "paid",
        "purchase",
        "buy",
        "subscribe",
        "money",
        "charge card",
        "place order",
    ),
    RiskBoundary.PHYSICAL_OPERATION.value: (
        "operate",
        "move",
        "heat",
        "start machine",
        "start a physical device",
        "start physical device",
        "control hardware",
        "actuate",
    ),
    RiskBoundary.EXTERNAL_SIDE_EFFECT.value: (
        "send",
        "publish",
        "post",
        "email",
        "message",
        "ship",
        "submit",
    ),
    RiskBoundary.DESTRUCTIVE_CHANGE.value: (
        "delete",
        "remove",
        "destroy",
        "drop",
        "overwrite",
        "reformat",
    ),
    RiskBoundary.PRODUCTION_CHANGE.value: (
        "deploy",
        "production",
        "release",
        "live",
    ),
    RiskBoundary.CREDENTIAL_USE.value: (
        "credential",
        "token",
        "secret",
        "password",
        "api key",
    ),
}


@dataclass(frozen=True)
class ResidentPolicyBoundary:
    """Configurable action boundary used by the generic policy assessor."""

    name: str
    terms: tuple[str, ...]
    approval_required: bool = True
    question: str = ""
    hard: bool = True


@dataclass(frozen=True)
class ConfigurableResidentPolicy(ResidentPolicyPort):
    """Policy assessor driven by boundary data and learned observations."""

    boundaries: tuple[ResidentPolicyBoundary, ...] = field(
        default_factory=lambda: tuple(
            ResidentPolicyBoundary(
                name=name,
                terms=terms,
                approval_required=True,
                question=f"May I proceed with an action touching {name.replace('_', ' ')}?",
            )
            for name, terms in _DEFAULT_BOUNDARY_TERMS.items()
        )
    )
    soft_boundaries: tuple[ResidentPolicyBoundary, ...] = ()
    allowed_boundaries: tuple[str, ...] = ()

    async def assess(
        self,
        action: ResidentActionCandidate,
        *,
        context: ResidentContinuationContext,
    ) -> ResidentPolicyDecision:
        calibration = _calibration_from_observations(context.policy_observations)
        configured_soft_allows = set(self.allowed_boundaries)
        soft_allows = calibration.allowed_boundaries | configured_soft_allows
        detected = set(action.risk_boundaries)
        text = _risk_scan_text(" ".join(part for part in (action.title, action.action) if part))

        all_boundaries = tuple(self.boundaries) + tuple(self.soft_boundaries)
        hard_boundary_names = {boundary.name for boundary in self.boundaries if boundary.hard}
        for boundary in all_boundaries:
            if not boundary.hard and boundary.name in soft_allows:
                continue
            if boundary.name in detected or _contains_term(text, boundary.terms):
                detected.add(boundary.name)

        hard_gated = tuple(sorted(name for name in detected if name in hard_boundary_names))
        soft_gated = tuple(
            sorted(
                name
                for name in detected
                if name not in hard_boundary_names
                and name not in soft_allows
            )
        )
        explicit_ask = tuple(
            sorted(name for name in calibration.ask_boundaries if name in detected)
        )
        gated = tuple(dict.fromkeys((*hard_gated, *soft_gated, *explicit_ask)))
        notes = calibration.notes
        if gated:
            joined = ", ".join(gated)
            question = f"May I proceed with this resident action despite {joined}: {action.action}"
            return ResidentPolicyDecision(
                allowed=False,
                needs_approval=True,
                reason=f"action crosses approval boundary: {joined}",
                risk_boundaries=gated,
                question=question,
                calibration_notes=notes,
            )

        return ResidentPolicyDecision(
            allowed=True,
            needs_approval=False,
            reason="within current resident policy and learned observations",
            risk_boundaries=tuple(sorted(detected)),
            calibration_notes=notes,
        )


def _contains_term(text: str, terms: tuple[str, ...]) -> bool:
    for term in terms:
        term = term.casefold().strip()
        if not term:
            continue
        if " " in term and term in text:
            return True
        if re.search(rf"\b{re.escape(term)}\b", text):
            return True
    return False


def _risk_scan_text(text: str) -> str:
    cleaned = f" {text.casefold()} "
    negated_patterns = (
        r"\bno[-\s]?spend(?:ing)?\b",
        r"\bnon[-\s]?spend(?:ing)?\b",
        r"\bwithout spending\b",
        r"\bwithout spend\b",
        r"\bno[-\s]?machine\b",
        r"\bnon[-\s]?machine\b",
        r"\bwithout operating (?:physical )?machines?\b",
        r"\bdoes not operate (?:physical )?machines?\b",
    )
    for pattern in negated_patterns:
        cleaned = re.sub(pattern, " ", cleaned)
    return " ".join(cleaned.split())


@dataclass(frozen=True)
class _PolicyCalibration:
    allowed_boundaries: set[str] = field(default_factory=set)
    ask_boundaries: set[str] = field(default_factory=set)
    notes: tuple[str, ...] = ()


def _calibration_from_observations(
    observations: tuple[ResidentPolicyObservation, ...],
) -> _PolicyCalibration:
    allowed: set[str] = set()
    ask: set[str] = set()
    notes: list[str] = []
    for observation in observations:
        if observation.status != "accepted":
            continue
        subject = observation.subject.strip()
        if subject.startswith("boundary:"):
            boundary = subject.removeprefix("boundary:").strip()
            allowed.add(boundary)
            notes.append(f"accepted legacy boundary allowance: {boundary}")
            continue
        if subject.startswith("soft-allow:"):
            boundary = subject.removeprefix("soft-allow:").strip()
            allowed.add(boundary)
            notes.append(f"accepted soft allowance: {boundary}")
            continue
        if subject.startswith("preference:continue:"):
            boundary = subject.removeprefix("preference:continue:").strip()
            allowed.add(boundary)
            notes.append(f"accepted continue preference: {boundary}")
            continue
        if subject.startswith("soft-ask:"):
            boundary = subject.removeprefix("soft-ask:").strip()
            ask.add(boundary)
            notes.append(f"accepted soft ask preference: {boundary}")
            continue
        if subject.startswith("preference:ask:"):
            boundary = subject.removeprefix("preference:ask:").strip()
            ask.add(boundary)
            notes.append(f"accepted ask preference: {boundary}")

    contradictions = allowed & ask
    if contradictions:
        for boundary in sorted(contradictions):
            allowed.discard(boundary)
            notes.append(f"contradictory accepted observations keep ask boundary: {boundary}")
    return _PolicyCalibration(
        allowed_boundaries=allowed,
        ask_boundaries=ask,
        notes=tuple(notes),
    )


class ResidentRunBudget:
    """In-memory run budget with persistable snapshots."""

    def __init__(
        self,
        limits: ResidentBudgetLimits,
        *,
        clock: Any = time.monotonic,
    ) -> None:
        self._limits = limits
        self._clock = clock
        self._started_at = float(clock())
        self._turns_used = 0
        self._usage = TokenUsage(input_tokens=0, output_tokens=0)
        self._cost_usd = 0.0

    @property
    def limits(self) -> ResidentBudgetLimits:
        return self._limits

    def snapshot(self) -> ResidentBudgetSnapshot:
        return ResidentBudgetSnapshot(
            turns_used=self._turns_used,
            elapsed_seconds=max(0.0, float(self._clock()) - self._started_at),
            usage=self._usage,
            cost_usd=self._cost_usd,
        )

    def can_continue(self) -> ResidentBudgetDecision:
        snapshot = self.snapshot()
        if self._limits.max_turns > 0 and snapshot.turns_used >= self._limits.max_turns:
            return ResidentBudgetDecision(False, f"max turns reached: {self._limits.max_turns}")
        if (
            self._limits.max_wall_clock_seconds > 0
            and snapshot.elapsed_seconds >= self._limits.max_wall_clock_seconds
        ):
            return ResidentBudgetDecision(
                False,
                f"max wall-clock seconds reached: {self._limits.max_wall_clock_seconds:g}",
            )
        if self._limits.max_tokens > 0 and snapshot.total_tokens >= self._limits.max_tokens:
            return ResidentBudgetDecision(
                False,
                f"max token budget reached: {self._limits.max_tokens}",
            )
        if self._limits.max_cost_usd > 0 and snapshot.cost_usd >= self._limits.max_cost_usd:
            return ResidentBudgetDecision(
                False,
                f"max cost budget reached: ${self._limits.max_cost_usd:.2f}",
            )
        return ResidentBudgetDecision(True, "budget available")

    def record_turn(self, result: TurnResult) -> ResidentBudgetSnapshot:
        self._turns_used += 1
        self._usage = self._usage + result.usage
        return self.snapshot()

    def record_usage(self, usage: TokenUsage) -> ResidentBudgetSnapshot:
        self._turns_used += 1
        self._usage = self._usage + usage
        return self.snapshot()


class NullResidentMemory(ResidentMemoryPort):
    """No-op memory used when persistence is unavailable."""

    async def recall(self, mandate: str, *, limit: int = 5) -> list[ResidentMemoryEntry]:
        return []

    async def write_turn(self, record: ResidentTurnRecord) -> str:
        return ""

    async def write_budget(self, snapshot: ResidentBudgetSnapshot) -> str:
        return ""

    async def write_policy_observation(self, observation: ResidentPolicyObservation) -> str:
        return ""

    async def list_policy_observations(self) -> list[ResidentPolicyObservation]:
        return []

    async def write_policy_decision(self, decision: ResidentPolicyDecisionRecord) -> str:
        return ""

    async def write_operator_needed(
        self,
        *,
        question: str,
        reason: str,
        turn: ResidentTurnRecord,
    ) -> str:
        return ""

    async def read_operator_needed(self) -> ResidentMemoryEntry | None:
        return None

    async def write_operator_answer(self, answer: str) -> str:
        return ""

    async def read_operator_answer(self) -> ResidentMemoryEntry | None:
        return None

    async def consume_operator_answer(self, answer: ResidentMemoryEntry) -> str:
        return ""


class LocalResidentMemory(ResidentMemoryPort):
    """Filesystem fallback that mirrors the Mimir memory shape."""

    def __init__(self, root: Path, *, prefix: str = "resident/continuation") -> None:
        self._root = Path(root)
        self._prefix = Path(prefix.strip("/").strip() or "resident/continuation")

    async def recall(self, mandate: str, *, limit: int = 5) -> list[ResidentMemoryEntry]:
        base = self._root / self._prefix
        if not base.exists():
            return []
        files = sorted(base.rglob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)
        entries: list[ResidentMemoryEntry] = []
        for path in files[:limit]:
            content = path.read_text(encoding="utf-8")
            entries.append(
                ResidentMemoryEntry(
                    path=str(path.relative_to(self._root)),
                    summary=_first_heading_or_line(content),
                    content=content,
                )
            )
        return entries

    async def write_turn(self, record: ResidentTurnRecord) -> str:
        stamp = record.created_at.strftime("%Y%m%dT%H%M%SZ")
        rel = self._prefix / "turns" / f"{stamp}-{record.turn_index}.md"
        return self._write(rel, _render_turn_record(record))

    async def write_budget(self, snapshot: ResidentBudgetSnapshot) -> str:
        rel = self._prefix / "budget" / "latest.md"
        return self._write(rel, _render_budget_snapshot(snapshot, updated_at=datetime.now(UTC)))

    async def write_policy_observation(self, observation: ResidentPolicyObservation) -> str:
        rel = self._prefix / "policy" / f"{_slug(observation.subject) or 'policy-observation'}.md"
        return self._write(rel, _render_policy_observation(observation))

    async def list_policy_observations(self) -> list[ResidentPolicyObservation]:
        base = self._root / self._prefix / "policy"
        if not base.exists():
            return []
        observations: list[ResidentPolicyObservation] = []
        for path in sorted(base.glob("*.md")):
            parsed = _parse_policy_observation(path.read_text(encoding="utf-8"))
            if parsed is not None:
                observations.append(parsed)
        return observations

    async def write_policy_decision(self, decision: ResidentPolicyDecisionRecord) -> str:
        stamp = decision.created_at.strftime("%Y%m%dT%H%M%SZ")
        slug = _slug(decision.action_title) or "policy-decision"
        rel = self._prefix / "policy-decisions" / f"{stamp}-{decision.turn_index}-{slug}.md"
        return self._write(rel, _render_policy_decision(decision))

    async def write_operator_needed(
        self,
        *,
        question: str,
        reason: str,
        turn: ResidentTurnRecord,
    ) -> str:
        rel = self._prefix / _OPERATOR_NEEDED_PATH
        return self._write(
            rel,
            _render_operator_needed(
                question=question,
                reason=reason,
                turn=turn,
                status="pending",
            ),
        )

    async def read_operator_needed(self) -> ResidentMemoryEntry | None:
        rel = self._prefix / _OPERATOR_NEEDED_PATH
        path = self._root / rel
        if not path.exists():
            return None
        content = path.read_text(encoding="utf-8")
        if not _operator_marker_is_pending(content):
            return None
        return ResidentMemoryEntry(
            path=str(rel),
            summary=_first_heading_or_line(content),
            content=content,
        )

    async def write_operator_answer(self, answer: str) -> str:
        now = datetime.now(UTC)
        answer_rel = self._prefix / _OPERATOR_ANSWER_PATH
        answer_ref = self._write(answer_rel, _render_operator_answer(answer, answered_at=now))
        history_rel = self._prefix / "operator-answers" / f"{_timestamp_slug(now)}.md"
        self._write(history_rel, _render_operator_answer(answer, answered_at=now))
        marker_rel = self._prefix / _OPERATOR_NEEDED_PATH
        marker_path = self._root / marker_rel
        prior = marker_path.read_text(encoding="utf-8") if marker_path.exists() else ""
        self._write(
            marker_rel,
            _render_answered_operator_needed(prior, answer_path=answer_ref, answered_at=now),
        )
        return answer_ref

    async def read_operator_answer(self) -> ResidentMemoryEntry | None:
        rel = self._prefix / _OPERATOR_ANSWER_PATH
        path = self._root / rel
        if not path.exists():
            return None
        content = path.read_text(encoding="utf-8")
        if _operator_answer_is_consumed(content):
            return None
        return ResidentMemoryEntry(
            path=str(rel),
            summary=_first_heading_or_line(content),
            content=content,
        )

    async def consume_operator_answer(self, answer: ResidentMemoryEntry) -> str:
        rel = Path(answer.path) if answer.path else self._prefix / _OPERATOR_ANSWER_PATH
        path = self._root / rel
        prior = path.read_text(encoding="utf-8") if path.exists() else answer.content
        return self._write(
            rel,
            _render_consumed_operator_answer(prior, consumed_at=datetime.now(UTC)),
        )

    def _write(self, rel: Path, content: str) -> str:
        path = self._root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(rel)


def _continuation_operator_contact(
    *,
    channel: ChannelPort | None,
    ask_operator: Any | None,
    source: str,
    persona: str,
    session_id: str,
) -> OperatorContactPort:
    channel_contact = (
        ChannelOperatorContact(
            channel=channel,
            source=source,
            persona=persona,
            session_id=session_id,
        )
        if channel is not None
        else None
    )
    callback_contact = (
        CallbackOperatorContact(ask_operator=ask_operator) if ask_operator is not None else None
    )
    if channel_contact is not None and callback_contact is not None:
        return BroadcastThenCallbackOperatorContact(
            broadcast=channel_contact,
            callback=callback_contact,
        )
    if channel_contact is not None:
        return channel_contact
    if callback_contact is not None:
        return callback_contact
    return PendingOperatorContact()


class ResidentContinuationKernel:
    """Runs bounded resident continuation through a backend-neutral executor."""

    def __init__(
        self,
        *,
        agent: ExecutionAgentPort,
        memory: ResidentMemoryPort | None = None,
        policy: ResidentPolicyPort | None = None,
        budget: ResidentRunBudget | None = None,
        persona_config: Any | None = None,
        ask_operator: Any | None = None,
        channel: ChannelPort | None = None,
        source: str = "resident-continuation",
    ) -> None:
        self._agent = agent
        self._memory = memory or NullResidentMemory()
        self._policy = policy or ConfigurableResidentPolicy()
        self._budget = budget or ResidentRunBudget(ResidentBudgetLimits())
        self._persona_config = persona_config
        self._ask_operator = ask_operator
        self._channel = channel
        self._source = source
        self._policy_observations: list[ResidentPolicyObservation] = []

    async def run(self, mandate: str) -> ResidentContinuationRun:
        decisions: list[ResidentContinuationDecision] = []
        records: list[ResidentTurnRecord] = []
        self._policy_observations = _merge_policy_observations(
            tuple(await self._memory.list_policy_observations())
        )
        pending = await self._memory.read_operator_needed()
        if pending is not None:
            decisions.append(
                ResidentContinuationDecision(
                    kind=ContinuationDecisionKind.SLEEP,
                    reason="waiting_for_operator",
                    question=_operator_marker_question(pending.content),
                )
            )
            await self._memory.write_budget(self._budget.snapshot())
            return ResidentContinuationRun(
                mandate=mandate,
                decisions=tuple(decisions),
                turns=tuple(records),
                budget=self._budget.snapshot(),
                policy_observations=tuple(self._policy_observations),
            )

        answer = await self._memory.read_operator_answer()
        if answer is not None:
            await self._record_operator_answer_observation(answer)
        prompt = _build_resume_prompt(mandate, answer) if answer is not None else mandate
        answer_to_consume = answer

        while True:
            budget_decision = self._budget.can_continue()
            if not budget_decision.allowed:
                decisions.append(
                    ResidentContinuationDecision(
                        kind=ContinuationDecisionKind.STOP,
                        reason=budget_decision.reason,
                    )
                )
                await self._memory.write_budget(self._budget.snapshot())
                break

            result = await self._agent.run_turn(prompt)
            snapshot = self._budget.record_turn(result)
            outcome_fields = _parse_outcome_fields(result.response, self._persona_config)
            record = ResidentTurnRecord(
                turn_index=snapshot.turns_used,
                prompt=prompt,
                response=result.response,
                outcome_fields=outcome_fields,
                tool_names=tuple(dict.fromkeys(call.name for call in result.tool_calls)),
                usage=result.usage,
                selected_next_action=selected_action_from_outcome(outcome_fields),
            )
            records.append(record)
            await self._memory.write_turn(record)
            await self._memory.write_budget(snapshot)
            if answer_to_consume is not None:
                await self._memory.consume_operator_answer(answer_to_consume)
                answer_to_consume = None

            recent_memory = tuple(await self._memory.recall(mandate, limit=5))
            context = ResidentContinuationContext(
                mandate=mandate,
                turn_record=record,
                budget=snapshot,
                recent_memory=recent_memory,
                available_tools=tuple(_tool_names(self._agent)),
                policy_observations=tuple(self._policy_observations),
            )
            decision = await self.decide(context)
            decisions.append(decision)
            if record.selected_next_action is not None:
                await self._memory.write_policy_decision(
                    _policy_decision_record(record, decision)
                )

            if decision.kind == ContinuationDecisionKind.CONTINUE and decision.prompt:
                prompt = decision.prompt
                continue

            if decision.kind == ContinuationDecisionKind.ASK_OPERATOR:
                await self._handle_operator_question(decision, record)
            break

        return ResidentContinuationRun(
            mandate=mandate,
            decisions=tuple(decisions),
            turns=tuple(records),
            budget=self._budget.snapshot(),
            policy_observations=tuple(self._policy_observations),
        )

    async def decide(
        self,
        context: ResidentContinuationContext,
    ) -> ResidentContinuationDecision:
        verdict = _verdict_from_record(context.turn_record)
        if verdict == "help_needed" or _blocked_needs_operator(context.turn_record.outcome_fields):
            question = _question_from_outcome(context.turn_record.outcome_fields)
            return ResidentContinuationDecision(
                kind=ContinuationDecisionKind.ASK_OPERATOR,
                reason=_reason_from_outcome(context.turn_record.outcome_fields),
                action=context.turn_record.selected_next_action,
                question=question,
            )

        action = context.turn_record.selected_next_action
        if action is None:
            return ResidentContinuationDecision(
                kind=ContinuationDecisionKind.SLEEP,
                reason="no selected next action in resident outcome",
            )

        budget_decision = self._budget.can_continue()
        if not budget_decision.allowed:
            return ResidentContinuationDecision(
                kind=ContinuationDecisionKind.STOP,
                reason=budget_decision.reason,
                action=action,
            )

        policy_decision = await self._policy.assess(action, context=context)
        if policy_decision.needs_approval:
            return ResidentContinuationDecision(
                kind=ContinuationDecisionKind.ASK_OPERATOR,
                reason=policy_decision.reason,
                action=action,
                question=policy_decision.question,
                risk_boundaries=policy_decision.risk_boundaries,
                calibration_notes=policy_decision.calibration_notes,
            )
        if not policy_decision.allowed:
            return ResidentContinuationDecision(
                kind=ContinuationDecisionKind.STOP,
                reason=policy_decision.reason,
                action=action,
                risk_boundaries=policy_decision.risk_boundaries,
                calibration_notes=policy_decision.calibration_notes,
            )

        return ResidentContinuationDecision(
            kind=ContinuationDecisionKind.CONTINUE,
            reason=f"selected action is safe and budget remains: {policy_decision.reason}",
            action=action,
            prompt=_build_continuation_prompt(context, action),
            risk_boundaries=policy_decision.risk_boundaries,
            calibration_notes=policy_decision.calibration_notes,
        )

    async def _handle_operator_question(
        self,
        decision: ResidentContinuationDecision,
        turn: ResidentTurnRecord,
    ) -> None:
        if not decision.question:
            return
        contact = _continuation_operator_contact(
            channel=self._channel,
            ask_operator=self._ask_operator,
            source=self._source,
            persona=str(getattr(self._persona_config, "name", "") or "resident"),
            session_id=str(getattr(self._agent, "task_id", "") or ""),
        )
        coordinator = ResidentOperatorContactCoordinator(
            memory=self._memory,
            contact=contact,
            config=ResidentOperatorContactConfig(persist_operator_feedback=False),
        )
        report = await coordinator.contact_operator(
            OperatorContactRequest(
                question=decision.question,
                reason=decision.reason or "needs_context",
                impact=_operator_contact_impact(turn),
                kind=OperatorContactKind.HELP_NEEDED
                if self._channel is not None
                else OperatorContactKind.ASK_USER,
                purpose=OperatorContactPurpose.APPROVAL
                if decision.risk_boundaries
                else OperatorContactPurpose.CLARIFICATION,
                id=f"resident-operator-{turn.turn_index}",
                risk_boundaries=decision.risk_boundaries,
                help_needed_outcome=dict(turn.outcome_fields),
                tool_input={"turn_index": str(turn.turn_index)},
            ),
            turn=turn,
        )
        if report.result.status == OperatorContactStatus.ANSWERED.value:
            await self._record_policy_observation(
                ResidentPolicyObservation(
                    subject=f"operator-contact:{report.request.purpose.value}",
                    observation=str(report.result.answer),
                    source="operator_answer",
                )
            )
            for parsed in _policy_observations_from_operator_text(str(report.result.answer)):
                await self._record_policy_observation(parsed)

    async def _record_operator_answer_observation(self, answer: ResidentMemoryEntry) -> None:
        observation_text = _operator_answer_text(answer.content)
        if not observation_text:
            return
        observation = ResidentPolicyObservation(
            subject=f"operator-answer:{_slug(answer.path) or 'latest'}",
            observation=observation_text,
            source="operator_answer",
        )
        await self._record_policy_observation(observation)
        for parsed in _policy_observations_from_operator_text(
            _operator_answer_policy_text(answer.content)
        ):
            await self._record_policy_observation(parsed)

    async def _record_policy_observation(self, observation: ResidentPolicyObservation) -> None:
        if any(
            _same_policy_observation(existing, observation)
            for existing in self._policy_observations
        ):
            return
        self._policy_observations.append(observation)
        await self._memory.write_policy_observation(observation)


def _parse_outcome_fields(text: str, persona_config: Any | None) -> dict[str, Any]:
    produces = getattr(persona_config, "produces", None)
    schema_fields = getattr(produces, "schema", None)
    if schema_fields:
        parsed = parse_outcome_block(text, OutcomeSchema(fields=schema_fields))
    else:
        parsed = parse_outcome_block(text, OutcomeSchema(fields={}))
    return dict(parsed.fields) if parsed is not None else {}


def _tool_names(agent: ExecutionAgentPort) -> list[str]:
    names: list[str] = []
    for tool in getattr(agent, "tools", []) or []:
        name = getattr(tool, "name", "")
        if callable(name):
            name = name()
        if str(name).strip():
            names.append(str(name).strip())
    return names


def _build_continuation_prompt(
    context: ResidentContinuationContext,
    action: ResidentActionCandidate,
) -> str:
    memory_lines = "\n".join(
        f"- {entry.path}: {entry.summary}" for entry in context.recent_memory[:5]
    )
    if not memory_lines:
        memory_lines = "- No prior resident memory found."

    tools = ", ".join(context.available_tools) if context.available_tools else "unknown"
    return (
        "Continue resident work from the mandate below. This is not a new operator task; "
        "it is the resident continuing its own selected safe next action.\n\n"
        f"Mandate:\n{context.mandate}\n\n"
        "Selected next action:\n"
        f"- title: {action.title}\n"
        f"- action: {action.action}\n"
        f"- reason: {action.reason or 'not specified'}\n\n"
        "Recent resident memory:\n"
        f"{memory_lines}\n\n"
        f"Available tools/capabilities: {tools}\n\n"
        "Do one bounded, safe, useful step toward the selected next action. "
        "Use tools when useful. Persist compact findings when memory tools are available. "
        "Ask the operator only when human judgment is genuinely needed. "
        "Finish with the persona's structured outcome and include the next selected action."
    )


def _build_resume_prompt(mandate: str, answer: ResidentMemoryEntry | None) -> str:
    if answer is None:
        return mandate
    return (
        "Continue resident work from the mandate below. The operator has replied to the "
        "resident's pending question; use that answer as new context and continue the "
        "original work.\n\n"
        f"Mandate:\n{mandate}\n\n"
        f"Operator answer memory ({answer.path}):\n{answer.content}\n\n"
        "Use the answer directly where it resolves the pending blocker. If the answer is "
        "partial, proceed with the parts that are now known and keep unknowns explicit. "
        "Finish with the persona's structured outcome."
    )


def _render_turn_record(record: ResidentTurnRecord) -> str:
    action = record.selected_next_action
    action_text = action.action if action is not None else ""
    fields = "\n".join(
        f"- {key}: {value!r}" for key, value in sorted(record.outcome_fields.items())
    )
    tools = ", ".join(record.tool_names) if record.tool_names else "none"
    return (
        f"# Resident Turn {record.turn_index}\n\n"
        f"- updated_at: {record.created_at.isoformat()}\n"
        f"- tools_used: {tools}\n"
        f"- input_tokens: {record.usage.input_tokens}\n"
        f"- output_tokens: {record.usage.output_tokens}\n\n"
        "## Summary\n\n"
        f"{_compact_line(record.response, limit=700)}\n\n"
        "## Outcome Fields\n\n"
        f"{fields or '- none'}\n\n"
        "## Selected Next Action\n\n"
        f"{action_text or 'none'}\n"
    )


def _render_operator_needed(
    *,
    question: str,
    reason: str,
    turn: ResidentTurnRecord,
    status: str,
) -> str:
    return (
        "# Operator Input Needed\n\n"
        f"- status: {status}\n"
        f"- turn: {turn.turn_index}\n"
        f"- reason: {_compact_line(reason, limit=500)}\n"
        f"- question: {_compact_line(question, limit=1000)}\n"
        f"- created_at: {datetime.now(UTC).isoformat()}\n\n"
        "## Selected Next Action\n\n"
        f"{turn.selected_next_action.action if turn.selected_next_action else 'none'}\n"
    )


def _render_operator_answer(answer: str, *, answered_at: datetime) -> str:
    return (
        "# Operator Answer\n\n"
        "- status: available\n"
        f"- answered_at: {answered_at.isoformat()}\n\n"
        f"{str(answer).strip()}\n"
    )


def _render_consumed_operator_answer(content: str, *, consumed_at: datetime) -> str:
    if content.strip():
        rendered = re.sub(r"^- status: .*$", "- status: consumed", content, flags=re.MULTILINE)
        if rendered == content and "- status:" not in rendered:
            rendered = rendered.replace(
                "# Operator Answer\n",
                "# Operator Answer\n\n- status: consumed\n",
                1,
            )
    else:
        rendered = "# Operator Answer\n\n- status: consumed\n"
    return rendered.rstrip() + "\n" + f"- consumed_at: {consumed_at.isoformat()}\n"


def _render_answered_operator_needed(
    prior: str,
    *,
    answer_path: str,
    answered_at: datetime,
) -> str:
    if prior.strip():
        content = re.sub(r"^- status: .*$", "- status: answered", prior, flags=re.MULTILINE)
        if content == prior and "- status:" not in content:
            content = content.replace(
                "# Operator Input Needed\n",
                "# Operator Input Needed\n\n- status: answered\n",
                1,
            )
    else:
        content = "# Operator Input Needed\n\n- status: answered\n"
    return (
        content.rstrip()
        + "\n"
        + f"- answered_at: {answered_at.isoformat()}\n"
        + f"- answer_ref: {answer_path}\n"
    )


def _operator_marker_is_pending(content: str) -> bool:
    return bool(re.search(r"^- status:\s*pending\s*$", content, flags=re.MULTILINE))


def _operator_answer_is_consumed(content: str) -> bool:
    return bool(re.search(r"^- status:\s*consumed\s*$", content, flags=re.MULTILINE))


def _operator_marker_question(content: str) -> str:
    match = re.search(r"^- question:\s*(.+?)\s*$", content, flags=re.MULTILINE)
    return _unquote(match.group(1).strip()) if match else ""


def _verdict_from_record(record: ResidentTurnRecord) -> str:
    verdict = _unquote(str(record.outcome_fields.get("verdict") or "").strip()).replace(" ", "_")
    if verdict:
        return verdict
    match = re.search(
        r"\bverdict\s*:\s*[\"']?"
        r"(help[_ ]needed|blocked|oriented|continue|sleep|stop|ask[_ ]operator)",
        record.response,
        flags=re.IGNORECASE,
    )
    if not match:
        return ""
    return _unquote(match.group(1).strip()).replace(" ", "_")


def _question_from_outcome(fields: dict[str, Any]) -> str:
    for key in ("question", "recommendation", "selected_next_action", "reason"):
        value = fields.get(key)
        if value is None:
            continue
        text = _unquote(_compact_line(str(value), limit=1000))
        if text:
            return text
    return "Resident needs operator input to continue."


def _reason_from_outcome(fields: dict[str, Any]) -> str:
    return _unquote(_compact_line(str(fields.get("reason") or "needs_context"), limit=500))


def _operator_contact_impact(turn: ResidentTurnRecord) -> str:
    if turn.selected_next_action is None:
        return "The resident will sleep until the operator answers."
    return _compact_line(
        "Answer controls whether the resident may continue with: "
        f"{turn.selected_next_action.action}",
        limit=500,
    )


def _blocked_needs_operator(fields: dict[str, Any]) -> bool:
    verdict = _unquote(str(fields.get("verdict") or "").strip()).replace(" ", "_")
    if verdict != "blocked":
        return False
    text = " ".join(
        str(fields.get(key) or "")
        for key in ("reason", "recommendation", "selected_next_action", "rationale")
    ).casefold()
    return any(term in text for term in ("operator", "human", "user input", "approval"))


def _attempted_from_outcome(fields: dict[str, Any]) -> list[str]:
    attempted = fields.get("attempted")
    if isinstance(attempted, list):
        return [str(item) for item in attempted if str(item).strip()][:3]
    return []


def _unquote(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1].strip()
    return text


def _render_budget_snapshot(snapshot: ResidentBudgetSnapshot, *, updated_at: datetime) -> str:
    return (
        "# Resident Continuation Budget\n\n"
        f"- updated_at: {updated_at.isoformat()}\n"
        f"- turns_used: {snapshot.turns_used}\n"
        f"- elapsed_seconds: {snapshot.elapsed_seconds:.2f}\n"
        f"- input_tokens: {snapshot.usage.input_tokens}\n"
        f"- output_tokens: {snapshot.usage.output_tokens}\n"
        f"- total_tokens: {snapshot.total_tokens}\n"
        f"- cost_usd: {snapshot.cost_usd:.6f}\n"
    )


def _render_policy_observation(observation: ResidentPolicyObservation) -> str:
    return (
        f"# Resident Policy Observation: {observation.subject}\n\n"
        f"- status: {observation.status}\n"
        f"- source: {observation.source}\n"
        f"- created_at: {observation.created_at.isoformat()}\n\n"
        f"{observation.observation}\n"
    )


def _render_policy_decision(decision: ResidentPolicyDecisionRecord) -> str:
    boundaries = ", ".join(decision.risk_boundaries) if decision.risk_boundaries else "none"
    notes = "\n".join(f"- {note}" for note in decision.calibration_notes) or "- none"
    return (
        f"# Resident Policy Decision: {decision.action_title}\n\n"
        f"- created_at: {decision.created_at.isoformat()}\n"
        f"- turn: {decision.turn_index}\n"
        f"- decision_kind: {decision.decision_kind}\n"
        f"- allowed: {str(decision.allowed).lower()}\n"
        f"- needs_approval: {str(decision.needs_approval).lower()}\n"
        f"- risk_boundaries: {boundaries}\n"
        f"- reason: {_compact_line(decision.reason, limit=700)}\n"
        f"- question: {_compact_line(decision.question, limit=700)}\n\n"
        "## Action\n\n"
        f"{decision.action}\n\n"
        "## Calibration Notes\n\n"
        f"{notes}\n"
    )


def _parse_policy_observation(content: str) -> ResidentPolicyObservation | None:
    subject_match = re.search(
        r"^#\s*Resident Policy Observation:\s*(.+?)\s*$",
        content,
        flags=re.MULTILINE,
    )
    if not subject_match:
        return None
    status_match = re.search(r"^-\s*status:\s*(.+?)\s*$", content, flags=re.MULTILINE)
    source_match = re.search(r"^-\s*source:\s*(.+?)\s*$", content, flags=re.MULTILINE)
    created_match = re.search(r"^-\s*created_at:\s*(.+?)\s*$", content, flags=re.MULTILINE)
    created_at = datetime.now(UTC)
    if created_match:
        try:
            created_at = datetime.fromisoformat(created_match.group(1).strip())
        except ValueError:
            created_at = datetime.now(UTC)
    return ResidentPolicyObservation(
        subject=subject_match.group(1).strip(),
        observation=_policy_observation_body(content),
        source=(source_match.group(1).strip() if source_match else "memory"),
        status=(status_match.group(1).strip() if status_match else "candidate"),
        created_at=created_at,
    )


def _policy_observation_body(content: str) -> str:
    body_started = False
    lines: list[str] = []
    for line in content.splitlines():
        if not body_started:
            if line.strip() == "":
                body_started = True
            continue
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("#"):
            continue
        if stripped:
            lines.append(stripped)
    return _compact_line(" ".join(lines), limit=1000)


def _policy_observations_from_operator_text(text: str) -> tuple[ResidentPolicyObservation, ...]:
    observations: list[ResidentPolicyObservation] = []
    for line in str(text or "").splitlines():
        match = re.match(
            r"^\s*policy\s*:\s*(accept|accepted|candidate|reject|rejected)\s+"
            r"((?:soft-allow|soft-ask|preference:continue|preference:ask):[a-z0-9_-]+)"
            r"(?:\s+because\s+(.+))?\s*$",
            line,
            flags=re.IGNORECASE,
        )
        if not match:
            continue
        status_word = match.group(1).casefold()
        status = {
            "accept": "accepted",
            "accepted": "accepted",
            "candidate": "candidate",
            "reject": "rejected",
            "rejected": "rejected",
        }[status_word]
        subject = match.group(2).casefold()
        reason = _compact_line(match.group(3) or "operator supplied explicit policy feedback")
        observations.append(
            ResidentPolicyObservation(
                subject=subject,
                observation=reason,
                source="operator_answer",
                status=status,
            )
        )
    return tuple(observations)


def _operator_answer_text(content: str) -> str:
    return _compact_line(" ".join(_operator_answer_body_lines(content)), limit=1000)


def _operator_answer_policy_text(content: str) -> str:
    return "\n".join(_operator_answer_body_lines(content))


def _operator_answer_body_lines(content: str) -> list[str]:
    lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("- "):
            continue
        lines.append(stripped)
    return lines


def _compact_line(text: str, *, limit: int = 240) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) > limit:
        compact = compact[: limit - 1].rstrip() + "…"
    return compact


def _policy_decision_record(
    record: ResidentTurnRecord,
    decision: ResidentContinuationDecision,
) -> ResidentPolicyDecisionRecord:
    action = record.selected_next_action
    return ResidentPolicyDecisionRecord(
        turn_index=record.turn_index,
        action_title=action.title if action is not None else "Selected next action",
        action=action.action if action is not None else "",
        decision_kind=decision.kind.value,
        allowed=decision.kind == ContinuationDecisionKind.CONTINUE,
        needs_approval=decision.kind == ContinuationDecisionKind.ASK_OPERATOR,
        reason=decision.reason,
        risk_boundaries=decision.risk_boundaries
        or (action.risk_boundaries if action is not None else ()),
        question=decision.question,
        calibration_notes=decision.calibration_notes,
    )


def _merge_policy_observations(
    observations: tuple[ResidentPolicyObservation, ...],
) -> list[ResidentPolicyObservation]:
    merged: list[ResidentPolicyObservation] = []
    for observation in observations:
        if not any(_same_policy_observation(existing, observation) for existing in merged):
            merged.append(observation)
    return merged


def _same_policy_observation(
    left: ResidentPolicyObservation,
    right: ResidentPolicyObservation,
) -> bool:
    return (
        left.subject == right.subject
        and left.source == right.source
        and left.status == right.status
        and left.observation == right.observation
    )


def _first_heading_or_line(content: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        return line.removeprefix("#").strip() or line
    return ""


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:80]


def _timestamp_slug(value: datetime) -> str:
    return value.strftime("%Y%m%dT%H%M%S%fZ")
