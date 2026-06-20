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
    ResidentPolicyObservation,
    ResidentPolicyPort,
    ResidentTurnRecord,
    RiskBoundary,
    selected_action_from_outcome,
)
from ravn.ports.executor import ExecutionAgentPort
from ravn.ports.mimir import MimirPort

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
    allowed_boundaries: tuple[str, ...] = ()

    async def assess(
        self,
        action: ResidentActionCandidate,
        *,
        context: ResidentContinuationContext,
    ) -> ResidentPolicyDecision:
        observed_allows = _allowed_boundaries_from_observations(context.policy_observations)
        allowed = set(self.allowed_boundaries) | observed_allows
        detected = set(action.risk_boundaries)
        text = _risk_scan_text(" ".join(part for part in (action.title, action.action) if part))

        for boundary in self.boundaries:
            if boundary.name in allowed:
                continue
            if boundary.name in detected or _contains_term(text, boundary.terms):
                detected.add(boundary.name)

        gated = tuple(sorted(name for name in detected if name not in allowed))
        if gated:
            joined = ", ".join(gated)
            question = f"May I proceed with this resident action despite {joined}: {action.action}"
            return ResidentPolicyDecision(
                allowed=False,
                needs_approval=True,
                reason=f"action crosses approval boundary: {joined}",
                risk_boundaries=gated,
                question=question,
            )

        return ResidentPolicyDecision(
            allowed=True,
            needs_approval=False,
            reason="within current resident policy and learned observations",
            risk_boundaries=tuple(sorted(detected)),
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


def _allowed_boundaries_from_observations(
    observations: tuple[ResidentPolicyObservation, ...],
) -> set[str]:
    allowed: set[str] = set()
    for observation in observations:
        if observation.status != "accepted":
            continue
        subject = observation.subject.strip()
        if subject.startswith("boundary:"):
            allowed.add(subject.removeprefix("boundary:").strip())
    return allowed


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


class MimirResidentMemory(ResidentMemoryPort):
    """Resident continuation memory backed by existing Mimir pages."""

    def __init__(self, mimir: MimirPort, *, prefix: str = "resident/continuation") -> None:
        self._mimir = mimir
        self._prefix = prefix.strip("/").strip() or "resident/continuation"

    async def recall(self, mandate: str, *, limit: int = 5) -> list[ResidentMemoryEntry]:
        query = _compact_line(mandate) or "resident continuation"
        pages = await self._mimir.search(query)
        entries: list[ResidentMemoryEntry] = []
        for page in pages:
            path = getattr(page.meta, "path", "")
            if not path.startswith(self._prefix):
                continue
            summary = getattr(page.meta, "summary", "") or _first_heading_or_line(page.content)
            entries.append(ResidentMemoryEntry(path=path, summary=summary, content=page.content))
            if len(entries) >= limit:
                break
        return entries

    async def write_turn(self, record: ResidentTurnRecord) -> str:
        stamp = record.created_at.strftime("%Y%m%dT%H%M%SZ")
        path = f"{self._prefix}/turns/{stamp}-{record.turn_index}.md"
        await self._mimir.upsert_page(path, _render_turn_record(record))
        return path

    async def write_budget(self, snapshot: ResidentBudgetSnapshot) -> str:
        now = datetime.now(UTC)
        path = f"{self._prefix}/budget/latest.md"
        await self._mimir.upsert_page(path, _render_budget_snapshot(snapshot, updated_at=now))
        return path

    async def write_policy_observation(self, observation: ResidentPolicyObservation) -> str:
        slug = _slug(observation.subject) or "policy-observation"
        path = f"{self._prefix}/policy/{slug}.md"
        await self._mimir.upsert_page(path, _render_policy_observation(observation))
        return path


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

    def _write(self, rel: Path, content: str) -> str:
        path = self._root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(rel)


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
    ) -> None:
        self._agent = agent
        self._memory = memory or NullResidentMemory()
        self._policy = policy or ConfigurableResidentPolicy()
        self._budget = budget or ResidentRunBudget(ResidentBudgetLimits())
        self._persona_config = persona_config
        self._ask_operator = ask_operator
        self._policy_observations: list[ResidentPolicyObservation] = []

    async def run(self, mandate: str) -> ResidentContinuationRun:
        decisions: list[ResidentContinuationDecision] = []
        records: list[ResidentTurnRecord] = []
        prompt = mandate

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

            if decision.kind == ContinuationDecisionKind.CONTINUE and decision.prompt:
                prompt = decision.prompt
                continue

            if decision.kind == ContinuationDecisionKind.ASK_OPERATOR:
                await self._handle_operator_question(decision)
            break

        return ResidentContinuationRun(
            mandate=mandate,
            decisions=tuple(decisions),
            turns=tuple(records),
            budget=self._budget.snapshot(),
        )

    async def decide(
        self,
        context: ResidentContinuationContext,
    ) -> ResidentContinuationDecision:
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
            )
        if not policy_decision.allowed:
            return ResidentContinuationDecision(
                kind=ContinuationDecisionKind.STOP,
                reason=policy_decision.reason,
                action=action,
            )

        return ResidentContinuationDecision(
            kind=ContinuationDecisionKind.CONTINUE,
            reason=f"selected action is safe and budget remains: {policy_decision.reason}",
            action=action,
            prompt=_build_continuation_prompt(context, action),
        )

    async def _handle_operator_question(self, decision: ResidentContinuationDecision) -> None:
        if self._ask_operator is None or not decision.question:
            return
        answer = await self._ask_operator(decision.question)
        observation = ResidentPolicyObservation(
            subject=f"question:{_slug(decision.question)}",
            observation=str(answer),
            source="operator_answer",
        )
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


def _compact_line(text: str, *, limit: int = 240) -> str:
    compact = " ".join(str(text or "").split())
    if len(compact) > limit:
        compact = compact[: limit - 1].rstrip() + "…"
    return compact


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
