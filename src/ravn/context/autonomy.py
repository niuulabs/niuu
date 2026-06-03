"""Autonomy policy and durable proposal storage for Valkyrie self-improvement."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from ravn.context.evolution import PromptEvolution
from ravn.ports.permission import Allow, Deny, NeedsApproval, PermissionDecision


class AutonomyMode(StrEnum):
    """Resident Valkyrie autonomy modes."""

    GUARDED = "guarded"
    AUTONOMOUS = "autonomous"
    YOLO = "yolo"


class ProposalScope(StrEnum):
    """Scopes that self-improvement proposals may target."""

    PRIVATE = "private"
    ENVIRONMENT = "environment"
    DOMAIN = "domain"
    FLOCK = "flock"
    SHARED = "shared"
    PRODUCTION = "production"
    GLOBAL = "global"


class ProposalStatus(StrEnum):
    """Durable lifecycle states for self-improvement proposals."""

    PROPOSED = "proposed"
    NEEDS_REVIEW = "needs_review"
    BLOCKED = "blocked"
    APPLIED = "applied"
    ROLLED_BACK = "rolled_back"


class RiskClass(StrEnum):
    """Risk levels used by the autonomy policy."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


_HARD_GATED_BOUNDARIES = frozenset(
    {
        "authority_expansion",
        "credentials",
        "spending",
        "destructive",
        "external_send",
        "global_doctrine",
    }
)
_PRIVATE_ENV_SCOPES = frozenset({ProposalScope.PRIVATE, ProposalScope.ENVIRONMENT})
_YOLO_SCOPES = frozenset({ProposalScope.PRIVATE, ProposalScope.ENVIRONMENT, ProposalScope.DOMAIN})


@dataclass(frozen=True)
class AutonomyDecision:
    """Structured policy decision with a serialisable reason."""

    decision: str
    reason: str

    @classmethod
    def from_permission(cls, decision: PermissionDecision) -> AutonomyDecision:
        if isinstance(decision, Allow):
            return cls("allow", "within delegated autonomy boundary")
        if isinstance(decision, Deny):
            return cls("deny", decision.reason)
        return cls("needs_approval", decision.question)

    def to_permission(self) -> PermissionDecision:
        if self.decision == "allow":
            return Allow()
        if self.decision == "deny":
            return Deny(self.reason)
        return NeedsApproval(self.reason)


@dataclass
class SelfImprovementProposal:
    """Durable proposal produced by evolution, dream, or skill management."""

    proposal_id: str
    title: str
    artifact_type: str
    action: str
    content: str
    scope: str = ProposalScope.PRIVATE.value
    environment_id: str = ""
    domain: str = ""
    mode: str = AutonomyMode.GUARDED.value
    risk_class: str = RiskClass.LOW.value
    risk_boundaries: list[str] = field(default_factory=list)
    delegated_capabilities: list[str] = field(default_factory=list)
    source_episode_ids: list[str] = field(default_factory=list)
    status: str = ProposalStatus.PROPOSED.value
    policy_decision: str = ""
    policy_reason: str = ""
    applied_artifact_refs: dict[str, str] = field(default_factory=dict)
    rollback_metadata: dict = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    applied_at: str = ""
    rolled_back_at: str = ""

    @classmethod
    def from_dict(cls, data: dict) -> SelfImprovementProposal:
        return cls(
            proposal_id=str(data.get("proposal_id") or uuid4()),
            title=str(data.get("title") or "Untitled improvement proposal"),
            artifact_type=str(data.get("artifact_type") or "skill"),
            action=str(data.get("action") or "create"),
            content=str(data.get("content") or ""),
            scope=str(data.get("scope") or ProposalScope.PRIVATE.value),
            environment_id=str(data.get("environment_id") or ""),
            domain=str(data.get("domain") or ""),
            mode=str(data.get("mode") or AutonomyMode.GUARDED.value),
            risk_class=str(data.get("risk_class") or RiskClass.LOW.value),
            risk_boundaries=list(data.get("risk_boundaries") or []),
            delegated_capabilities=list(data.get("delegated_capabilities") or []),
            source_episode_ids=list(data.get("source_episode_ids") or []),
            status=str(data.get("status") or ProposalStatus.PROPOSED.value),
            policy_decision=str(data.get("policy_decision") or ""),
            policy_reason=str(data.get("policy_reason") or ""),
            applied_artifact_refs=dict(data.get("applied_artifact_refs") or {}),
            rollback_metadata=dict(data.get("rollback_metadata") or {}),
            created_at=str(data.get("created_at") or datetime.now(UTC).isoformat()),
            updated_at=str(data.get("updated_at") or datetime.now(UTC).isoformat()),
            applied_at=str(data.get("applied_at") or ""),
            rolled_back_at=str(data.get("rolled_back_at") or ""),
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def record_decision(self, decision: AutonomyDecision) -> None:
        self.policy_decision = decision.decision
        self.policy_reason = decision.reason
        if decision.decision == "allow":
            self.status = ProposalStatus.PROPOSED.value
        elif decision.decision == "deny":
            self.status = ProposalStatus.BLOCKED.value
        else:
            self.status = ProposalStatus.NEEDS_REVIEW.value
        self.updated_at = datetime.now(UTC).isoformat()

    def mark_applied(
        self,
        *,
        artifact_refs: dict[str, str] | None = None,
        rollback_metadata: dict | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        self.status = ProposalStatus.APPLIED.value
        self.applied_at = now
        self.updated_at = now
        self.applied_artifact_refs = artifact_refs or {}
        self.rollback_metadata = rollback_metadata or {}

    def mark_rolled_back(self) -> None:
        now = datetime.now(UTC).isoformat()
        self.status = ProposalStatus.ROLLED_BACK.value
        self.rolled_back_at = now
        self.updated_at = now


class AutonomyPolicy:
    """Evaluate self-improvement proposals against scoped Valkyrie autonomy."""

    def decide(self, proposal: SelfImprovementProposal) -> AutonomyDecision:
        mode = _mode(proposal.mode)
        scope = _scope(proposal.scope)
        risk = _risk(proposal.risk_class)
        gated = _gated_boundaries(proposal)

        undelegated = gated - set(proposal.delegated_capabilities)
        if undelegated:
            names = ", ".join(sorted(undelegated))
            return AutonomyDecision(
                "needs_approval",
                f"requires explicit delegation for gated boundary: {names}",
            )

        if scope in (ProposalScope.PRODUCTION, ProposalScope.GLOBAL):
            return AutonomyDecision(
                "needs_approval",
                f"{scope.value} changes are outside resident Valkyrie auto-apply scope",
            )

        if mode == AutonomyMode.GUARDED:
            return AutonomyDecision("needs_approval", "guarded mode records proposals for review")

        if mode == AutonomyMode.AUTONOMOUS:
            if scope in _PRIVATE_ENV_SCOPES and risk == RiskClass.LOW:
                return AutonomyDecision(
                    "allow",
                    "autonomous mode allows low-risk private/Environment changes",
                )
            return AutonomyDecision(
                "needs_approval",
                f"autonomous mode requires review for {risk.value} {scope.value} changes",
            )

        if mode == AutonomyMode.YOLO:
            if scope in _YOLO_SCOPES and risk in (RiskClass.LOW, RiskClass.MEDIUM):
                return AutonomyDecision(
                    "allow",
                    "YOLO mode allows delegated private, Environment, and domain evolution",
                )
            return AutonomyDecision(
                "needs_approval",
                f"YOLO mode requires review for {risk.value} {scope.value} changes",
            )

        return AutonomyDecision("needs_approval", f"unknown autonomy mode {proposal.mode!r}")


class JsonProposalStore:
    """Durable JSON storage for self-improvement proposal lifecycles."""

    def __init__(self, path: str | Path = "~/.ravn/autonomy_proposals.json") -> None:
        self._path = Path(path).expanduser()
        self._proposals: dict[str, SelfImprovementProposal] = {}
        self._load()

    def list(
        self,
        *,
        status: str | None = None,
        environment_id: str | None = None,
    ) -> list[SelfImprovementProposal]:
        rows = list(self._proposals.values())
        if status:
            rows = [p for p in rows if p.status == status]
        if environment_id:
            rows = [p for p in rows if p.environment_id == environment_id]
        return sorted(rows, key=lambda p: p.created_at)

    def get(self, proposal_id: str) -> SelfImprovementProposal:
        try:
            return self._proposals[proposal_id]
        except KeyError as exc:
            raise ValueError(f"unknown proposal: {proposal_id}") from exc

    def save(self, proposal: SelfImprovementProposal) -> SelfImprovementProposal:
        self._proposals[proposal.proposal_id] = proposal
        self._save()
        return proposal

    def record_decision(
        self,
        proposal: SelfImprovementProposal,
        decision: AutonomyDecision,
    ) -> SelfImprovementProposal:
        proposal.record_decision(decision)
        return self.save(proposal)

    async def apply(
        self,
        proposal_id: str,
        apply_fn: Callable[[SelfImprovementProposal], Awaitable[tuple[dict[str, str], dict]]],
    ) -> SelfImprovementProposal:
        proposal = self.get(proposal_id)
        if proposal.status == ProposalStatus.BLOCKED.value:
            raise ValueError(f"proposal is blocked: {proposal.policy_reason}")
        refs, rollback = await apply_fn(proposal)
        proposal.mark_applied(artifact_refs=refs, rollback_metadata=rollback)
        return self.save(proposal)

    async def rollback(
        self,
        proposal_id: str,
        rollback_fn: Callable[[SelfImprovementProposal], Awaitable[None]],
    ) -> SelfImprovementProposal:
        proposal = self.get(proposal_id)
        if proposal.status != ProposalStatus.APPLIED.value:
            raise ValueError(f"proposal is not applied: {proposal_id}")
        await rollback_fn(proposal)
        proposal.mark_rolled_back()
        return self.save(proposal)

    def _load(self) -> None:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self._proposals = {}
            return
        proposals = raw.get("proposals", raw if isinstance(raw, list) else [])
        self._proposals = {
            proposal.proposal_id: proposal
            for proposal in (SelfImprovementProposal.from_dict(p) for p in proposals)
        }

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"proposals": [p.to_dict() for p in self.list()]}
        self._path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def proposals_from_evolution(
    evolution: PromptEvolution,
    *,
    mode: str = AutonomyMode.GUARDED.value,
    scope: str = ProposalScope.PRIVATE.value,
    environment_id: str = "",
    domain: str = "",
) -> list[SelfImprovementProposal]:
    """Convert mined ``PromptEvolution`` output into durable proposals."""
    proposals: list[SelfImprovementProposal] = []
    for skill in evolution.suggested_skills:
        tools = ", ".join(skill.tool_pattern)
        proposals.append(
            SelfImprovementProposal(
                proposal_id=str(uuid4()),
                title=f"Create skill for {tools}",
                artifact_type="skill",
                action="create",
                content=skill.description,
                scope=scope,
                environment_id=environment_id,
                domain=domain,
                mode=mode,
                risk_class=RiskClass.LOW.value,
                source_episode_ids=skill.source_episode_ids,
                applied_artifact_refs={"requires_tools": tools},
            )
        )
    for warning in evolution.system_warnings:
        proposals.append(
            SelfImprovementProposal(
                proposal_id=str(uuid4()),
                title="Add system warning",
                artifact_type="system_prompt",
                action="update",
                content=warning.warning_text,
                scope=ProposalScope.GLOBAL.value,
                mode=mode,
                risk_class=RiskClass.HIGH.value,
                risk_boundaries=["global_doctrine"],
                source_episode_ids=warning.source_outcome_ids,
            )
        )
    for strategy in evolution.strategy_injections:
        proposals.append(
            SelfImprovementProposal(
                proposal_id=str(uuid4()),
                title=f"Add strategy for {strategy.task_type}",
                artifact_type="strategy",
                action="update",
                content=strategy.strategy_text,
                scope=scope,
                environment_id=environment_id,
                domain=domain or strategy.task_type,
                mode=mode,
                risk_class=RiskClass.MEDIUM.value,
                source_episode_ids=strategy.source_episode_ids,
            )
        )
    return proposals


async def evaluate_and_store_proposals(
    proposals: list[SelfImprovementProposal],
    *,
    store: JsonProposalStore,
    policy: AutonomyPolicy | None = None,
) -> list[SelfImprovementProposal]:
    """Apply policy decisions to proposals and persist them."""
    policy = policy or AutonomyPolicy()
    saved: list[SelfImprovementProposal] = []
    for proposal in proposals:
        saved.append(store.record_decision(proposal, policy.decide(proposal)))
    return saved


def _gated_boundaries(proposal: SelfImprovementProposal) -> set[str]:
    boundaries = {b for b in proposal.risk_boundaries if b in _HARD_GATED_BOUNDARIES}
    content = proposal.content.lower()
    action = proposal.action.lower()
    if proposal.scope == ProposalScope.GLOBAL.value:
        boundaries.add("global_doctrine")
    for boundary in _HARD_GATED_BOUNDARIES:
        token = boundary.replace("_", " ")
        if boundary in action or boundary in content or token in content:
            boundaries.add(boundary)
    return boundaries


def _mode(value: str) -> AutonomyMode:
    try:
        return AutonomyMode(value)
    except ValueError:
        return AutonomyMode.GUARDED


def _scope(value: str) -> ProposalScope:
    try:
        return ProposalScope(value)
    except ValueError:
        return ProposalScope.PRIVATE


def _risk(value: str) -> RiskClass:
    try:
        return RiskClass(value)
    except ValueError:
        return RiskClass.LOW
